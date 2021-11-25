import atexit
import random
import signal
import sys
from typing import Any, List, Optional

from ape.api import ReceiptAPI, TestProviderAPI, TransactionAPI, UpstreamProvider, Web3Provider
from ape.api.config import ConfigItem
from ape.exceptions import ContractLogicError, OutOfGasError, TransactionError, VirtualMachineError
from ape.logging import logger
from ape.utils import gas_estimation_error_message
from web3 import HTTPProvider, Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy

from .exceptions import HardhatProviderError, HardhatSubprocessError
from .process import HardhatProcess

EPHEMERAL_PORTS_START = 49152
EPHEMERAL_PORTS_END = 60999
HARDHAT_START_NETWORK_RETRIES = [0.1, 0.2, 0.3, 0.5, 1.0]  # seconds between network retries
HARDHAT_START_PROCESS_ATTEMPTS = 3  # number of attempts to start subprocess before giving up
DEFAULT_PORT = 8545


def _signal_handler(signum, frame):
    """Runs on SIGTERM and SIGINT to force ``atexit`` handlers to run."""
    atexit._run_exitfuncs()
    sys.exit(143 if signum == signal.SIGTERM else 130)


class HardhatForkConfig(ConfigItem):
    upstream_provider: Optional[str] = None
    block_number: Optional[int] = None


class HardhatNetworkConfig(ConfigItem):
    # --port <INT, default from Hardhat is 8545, but our default is to assign a random port number>
    port: Optional[int] = None

    # Retry strategy configs, try increasing these if you're getting HardhatSubprocessError
    network_retries: List[float] = HARDHAT_START_NETWORK_RETRIES
    process_attempts: int = HARDHAT_START_PROCESS_ATTEMPTS

    # For setting the values in --fork and --fork-block-number command arguments.
    # Used only in HardhatMainnetForkProvider.
    mainnet_fork: Optional[HardhatForkConfig] = None


class HardhatProvider(Web3Provider, TestProviderAPI):
    def __post_init__(self):
        self._hardhat_web3 = (
            None  # we need to maintain a separate per-instance web3 client for Hardhat
        )
        self.port = self.config.port
        self.process = None
        self._config_manager = self.network.config_manager
        self._base_path = self._config_manager.PROJECT_FOLDER

        # When the user did not specify a port and we are attempting to start
        # the process ourselves, we first try the default port of 8545. Otherwise,
        # we pick a random port in an ephemeral range.
        self._tried_default_port = False

        # register atexit handler to make sure disconnect is called for normal object lifecycle
        atexit.register(self.disconnect)

        # register signal handlers to make sure atexit handlers are called when the parent python
        # process is killed
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        config = self._config_manager.get_config("test")
        if hasattr(config, "mnemonic"):
            mnemonic = config.mnemonic
            number_of_accounts = config.number_of_accounts
        else:
            self._failing_to_load_test_plugins = True
            logger.error("Failed to load config from 'ape-test' plugin, using default values.")

            from ape_test import Config as TestConfig

            _test_config_cls = TestConfig
            mnemonic = _test_config_cls.__defaults__["mnemonic"]
            number_of_accounts = _test_config_cls.__defaults__["number_of_accounts"]

        self._mnemonic = mnemonic
        self._number_of_accounts = number_of_accounts

    def connect(self):
        """Start the hardhat process and verify it's up and accepting connections."""

        if self.process:
            raise HardhatProviderError(
                "Cannot connect twice. Call disconnect before connecting again."
            )

        if self.port:
            self._set_web3()
            if not self._web3:
                self._start_process()
                self._set_web3()
            else:
                # We get here when user configured a port and the hardhat process
                # was already running.
                logger.info(f"Connecting to existing Hardhat node at port '{self.port}'.")
        else:
            for _ in range(self.config.process_attempts):
                try:
                    self._start_process()
                    break
                except HardhatSubprocessError as exc:
                    logger.info("Retrying hardhat subprocess startup: %r", exc)
                    self.port = None

            self._set_web3()

    def _set_web3(self):
        self._web3 = Web3(HTTPProvider(self.uri))
        if not self._web3.isConnected():
            self._web3 = None
            return

        # Verify is actually a hardhat provider,
        # or else skip it to possibly try another port.
        client_version = self._web3.clientVersion

        if "hardhat" in client_version.lower():
            self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
        else:
            # This will trigger the plugin to try another port
            # (provided the user did not request a specific port).
            self._web3 = None

    def _start_process(self):
        if not self.port:
            if not self._tried_default_port:
                # Try port 8545 first.
                self.port = DEFAULT_PORT
                self._tried_default_port = True

            else:
                # Pick a random port if one isn't configured and 8545 is taken.
                self.port = random.randint(EPHEMERAL_PORTS_START, EPHEMERAL_PORTS_END)

        self.process = self._create_process()
        self.process.start()

    def _create_process(self):
        """
        Sub-classes may override this to specify alternative values in the process,
        such as using mainnet-fork mode.
        """
        return HardhatProcess(self._base_path, self.port, self._mnemonic, self._number_of_accounts)

    @property
    def uri(self) -> str:
        if not self.port:
            raise HardhatProviderError("Can't build URI before `connect()` is called.")

        return f"http://127.0.0.1:{self.port}"

    @property  # type: ignore
    def _web3(self):
        """
        This property overrides the ``EthereumProvider._web3`` class variable to return our
        instance variable.
        """
        return self._hardhat_web3

    @_web3.setter
    def _web3(self, value):
        """
        Redirect the base class's assignments of self._web3 class variable to our instance variable.
        """
        self._hardhat_web3 = value

    @property
    def priority_fee(self) -> int:
        """
        Priority fee not needed in development network.
        """
        return 0

    def disconnect(self):
        self._web3 = None
        if self.process:
            self.process.stop()
            self.process = None

        self.port = None

    def _make_request(self, rpc: str, args: list) -> Any:
        return self._web3.manager.request_blocking(rpc, args)  # type: ignore

    def set_block_gas_limit(self, gas_limit: int) -> bool:
        return self._make_request("evm_setBlockGasLimit", [hex(gas_limit)])

    def sleep(self, seconds: int) -> int:
        return int(self._make_request("evm_increaseTime", [seconds]))

    def mine(self, timestamp: Optional[int] = None) -> str:
        return self._make_request("evm_mine", [timestamp] if timestamp else [])

    def snapshot(self) -> str:
        result = self._make_request("evm_snapshot", [])
        return str(result)

    def revert(self, snapshot_id: str):
        if isinstance(snapshot_id, str) and snapshot_id.isnumeric():
            snapshot_id = int(snapshot_id)  # type: ignore

        return self._make_request("evm_revert", [snapshot_id])

    def unlock_account(self, address: str) -> bool:
        return self._make_request("hardhat_impersonateAccount", [address])

    def estimate_gas_cost(self, txn: TransactionAPI) -> int:
        """
        Generates and returns an estimate of how much gas is necessary
        to allow the transaction to complete.
        The transaction will not be added to the blockchain.
        """
        try:
            return super().estimate_gas_cost(txn)
        except ValueError as err:
            tx_error = _get_vm_error(err)

            # If this is the cause of a would-be revert,
            # raise ContractLogicError so that we can confirm tx-reverts.
            if isinstance(tx_error, ContractLogicError):
                raise tx_error from err

            message = gas_estimation_error_message(tx_error)
            raise TransactionError(base_err=tx_error, message=message) from err

    def send_transaction(self, txn: TransactionAPI) -> ReceiptAPI:
        """
        Creates a new message call transaction or a contract creation
        for signed transactions.
        """
        try:
            receipt = super().send_transaction(txn)
        except ValueError as err:
            raise _get_vm_error(err) from err

        receipt.raise_for_status(txn)
        return receipt


class HardhatMainnetForkProvider(HardhatProvider):
    """
    A Hardhat provider that uses ``--fork``, like:
    ``npx hardhat node --fork <upstream-provider-url>``.

    Set the ``upstream_provider`` in the ``hardhat.mainnet_fork`` config
    section of your ``ape-config.yaml` file to specify which provider
    to use as your archive node.
    """

    def _create_process(self) -> HardhatProcess:
        mainnet_fork = self.config.mainnet_fork or {}  # type: ignore
        upstream_provider_name = mainnet_fork.get("upstream_provider")
        fork_block_num = mainnet_fork.get("block_number")
        mainnet = self.network.ecosystem.mainnet

        # NOTE: if `upstream_provider_name` is `None`, this gets the default mainnet provider.
        upstream_provider = mainnet.get_provider(provider_name=upstream_provider_name)

        if not isinstance(upstream_provider, UpstreamProvider):
            raise HardhatProviderError(
                f"Provider '{upstream_provider_name}' is not an upstream provider."
            )

        fork_url = upstream_provider.connection_str
        if not fork_url:
            raise HardhatProviderError("Upstream provider does not have a ``connection_str``.")

        return HardhatProcess(
            self._base_path,
            self.port,
            self._mnemonic,
            self._number_of_accounts,
            fork_url=fork_url,
            fork_block_number=fork_block_num,
        )


def _get_vm_error(web3_value_error: ValueError) -> TransactionError:
    if not len(web3_value_error.args):
        return VirtualMachineError(base_err=web3_value_error)

    err_data = web3_value_error.args[0]
    if not isinstance(err_data, dict):
        return VirtualMachineError(base_err=web3_value_error)

    message = str(err_data.get("message"))
    if not message:
        return VirtualMachineError(base_err=web3_value_error)

    # Handle `ContactLogicError` similary to other providers in `ape`.
    # by stripping off the unnecessary prefix that hardhat has on reverts.
    hardhat_prefix = (
        "Error: VM Exception while processing transaction: reverted with reason string "
    )
    if message.startswith(hardhat_prefix):
        message = message.replace(hardhat_prefix, "").strip("'")
        return ContractLogicError(revert_message=message)
    elif "Transaction reverted without a reason string" in message:
        return ContractLogicError()

    elif message == "Transaction ran out of gas":
        return OutOfGasError()

    return VirtualMachineError(message=message)
