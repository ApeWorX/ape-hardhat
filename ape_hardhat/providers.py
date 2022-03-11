import random
import shutil
from pathlib import Path
from subprocess import PIPE, call
from typing import Any, List, Optional, Union, cast

from ape._compat import Literal
from ape.api import (
    PluginConfig,
    ProviderAPI,
    ReceiptAPI,
    SubprocessProvider,
    TestProviderAPI,
    TransactionAPI,
    UpstreamProvider,
    Web3Provider,
)
from ape.exceptions import (
    ContractLogicError,
    OutOfGasError,
    SubprocessError,
    TransactionError,
    VirtualMachineError,
)
from ape.logging import logger
from ape.types import SnapshotID
from ape.utils import DEFAULT_NUMBER_OF_TEST_ACCOUNTS, cached_property, gas_estimation_error_message
from web3 import HTTPProvider, Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy

from .exceptions import HardhatNotInstalledError, HardhatProviderError, HardhatSubprocessError

EPHEMERAL_PORTS_START = 49152
EPHEMERAL_PORTS_END = 60999
HARDHAT_START_NETWORK_RETRIES = [0.1, 0.2, 0.3, 0.5, 1.0]  # seconds between network retries
HARDHAT_START_PROCESS_ATTEMPTS = 3  # number of attempts to start subprocess before giving up
DEFAULT_PORT = 8545
HARDHAT_CHAIN_ID = 31337
HARDHAT_CONFIG = """
// See https://hardhat.org/config/ for config options.
module.exports = {{
  networks: {{
    hardhat: {{
      hardfork: "london",
      // Base fee of 0 allows use of 0 gas price when testing
      initialBaseFeePerGas: 0,
      accounts: {{
        mnemonic: "{mnemonic}",
        path: "m/44'/60'/0'",
        count: {number_of_accounts}
      }}
    }},
  }},
}};
"""
HARDHAT_CONFIG_FILE_NAME = "hardhat.config.js"


class HardhatConfigJS:
    """
    A class representing the actual ``hardhat.config.js`` file.
    """

    FILE_NAME = "hardhat.config.js"

    def __init__(
        self,
        project_path: Path,
        mnemonic: str,
        num_of_accounts: int,
        hard_fork: Optional[str] = None,
    ):
        self._base_path = project_path
        self._mnemonic = mnemonic
        self._num_of_accounts = num_of_accounts
        self._hard_fork = hard_fork or "london"

    @property
    def _content(self) -> str:
        return HARDHAT_CONFIG.format(
            mnemonic=self._mnemonic, number_of_accounts=self._num_of_accounts
        )

    @property
    def _path(self) -> Path:
        return self._base_path / self.FILE_NAME

    def write_if_not_exists(self):
        if not self._path.is_file():
            self._path.write_text(self._content)


class HardhatForkConfig(PluginConfig):
    upstream_provider: Optional[str] = None
    block_number: Optional[int] = None


class HardhatNetworkConfig(PluginConfig):
    port: Optional[Union[int, Literal["auto"]]] = DEFAULT_PORT

    # Retry strategy configs, try increasing these if you're getting HardhatSubprocessError
    network_retries: List[float] = HARDHAT_START_NETWORK_RETRIES
    process_attempts: int = HARDHAT_START_PROCESS_ATTEMPTS

    # For setting the values in --fork and --fork-block-number command arguments.
    # Used only in HardhatMainnetForkProvider.
    mainnet_fork: Optional[HardhatForkConfig] = None


def _call(*args):
    return call([*args], stderr=PIPE, stdout=PIPE, stdin=PIPE)


class HardhatProvider(SubprocessProvider, Web3Provider, TestProviderAPI):
    port: Optional[int] = None
    attempted_ports: List[int] = []
    mnemonic: str = ""
    number_of_accounts: int = DEFAULT_NUMBER_OF_TEST_ACCOUNTS

    @property
    def process_name(self) -> str:
        return "Hardhat node"

    @cached_property
    def npx_bin(self) -> str:
        npx = shutil.which("npx")

        if not npx:
            raise HardhatSubprocessError(
                "Could not locate NPM executable. See ape-hardhat README for install steps."
            )
        elif _call(npx, "--version") != 0:
            raise HardhatSubprocessError(
                "NPM executable returned error code. See ape-hardhat README for install steps."
            )
        elif _call(npx, "hardhat") != 0:
            raise HardhatNotInstalledError()

        return npx

    @cached_property
    def hardhat_config_js(self) -> HardhatConfigJS:
        js_config = HardhatConfigJS(self.project_folder, self.mnemonic, self.number_of_accounts)
        js_config.write_if_not_exists()
        return js_config

    @property
    def project_folder(self) -> Path:
        return self.config_manager.PROJECT_FOLDER

    @property
    def uri(self) -> str:
        if not self.port:
            raise HardhatProviderError("Can't build URI before `connect()` is called.")

        return f"http://127.0.0.1:{self.port}"

    @property
    def priority_fee(self) -> int:
        """
        Priority fee not needed in development network.
        """
        return 0

    @property
    def is_connected(self) -> bool:
        self._set_web3()
        return self._web3 is not None

    def connect(self):
        """
        Start the hardhat process and verify it's up and accepting connections.
        """
        # NOTE: Must set port before calling 'super().connect()'.
        if not self.port:
            self.port = self.config.port  # type: ignore

        if self.is_connected:
            # Connects to already running process
            self._start()
        else:
            # Only do base-process setup if not connecting to already-running process
            super().connect()

            config = self.config_manager.get_config("test")
            self.mnemonic = config.mnemonic
            self.number_of_accounts = config.number_of_accounts

            if self.port:
                self._set_web3()
                if not self._web3:
                    self._start()
                else:
                    # The user configured a port and the hardhat process was already running.
                    logger.info(
                        f"Connecting to existing '{self.process_name}' at port '{self.port}'."
                    )
            else:
                for _ in range(self.config.process_attempts):  # type: ignore
                    try:
                        self._start()
                        break
                    except HardhatNotInstalledError:
                        # Is a sub-class of `HardhatSubprocessError` but we to still raise
                        # so we don't keep retrying.
                        raise
                    except SubprocessError as exc:
                        logger.info("Retrying Hardhat subprocess startup: %r", exc)
                        self.port = None

    def _set_web3(self):
        if not self.port:
            return

        self._web3 = Web3(HTTPProvider(self.uri))
        if not self._web3.isConnected():
            self._web3 = None
            return

        # Verify is actually a Hardhat provider,
        # or else skip it to possibly try another port.
        client_version = self._web3.clientVersion

        if "hardhat" in client_version.lower():
            self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
        else:
            # This will trigger the plugin to try another port
            # (provided the user did not request a specific port).
            self._web3 = None

    def _start(self):
        use_random_port = self.port == "auto"
        if use_random_port:
            self.port = None

            if DEFAULT_PORT not in self.attempted_ports and not use_random_port:
                self.port = DEFAULT_PORT
            else:
                port = random.randint(EPHEMERAL_PORTS_START, EPHEMERAL_PORTS_END)
                max_attempts = 25
                attempts = 0
                while port in self.attempted_ports:
                    port = random.randint(EPHEMERAL_PORTS_START, EPHEMERAL_PORTS_END)
                    attempts += 1
                    if attempts == max_attempts:
                        ports_str = ", ".join(self.attempted_ports)
                        raise HardhatProviderError(
                            f"Unable to find an available port. Ports tried: {ports_str}"
                        )

                self.port = port

        self.attempted_ports.append(self.port)
        self.start()

    def disconnect(self):
        self._web3 = None
        self.port = None
        super().disconnect()

    def build_command(self) -> List[str]:
        return [
            self.npx_bin,
            "hardhat",
            "node",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]

    def _make_request(self, rpc: str, args: list) -> Any:
        return self._web3.manager.request_blocking(rpc, args)  # type: ignore

    def set_block_gas_limit(self, gas_limit: int) -> bool:
        return self._make_request("evm_setBlockGasLimit", [hex(gas_limit)])

    def set_timestamp(self, new_timestamp: int):
        pending_timestamp = self.get_block("pending").timestamp
        seconds_to_increase = new_timestamp - pending_timestamp
        self._make_request("evm_increaseTime", [seconds_to_increase])

    def mine(self, num_blocks: int = 1):
        for i in range(num_blocks):
            self._make_request("evm_mine", [])

    def snapshot(self) -> str:
        result = self._make_request("evm_snapshot", [])
        return str(result)

    def revert(self, snapshot_id: SnapshotID):
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

        receipt.raise_for_status()
        return receipt


class HardhatMainnetForkProvider(HardhatProvider):
    """
    A Hardhat provider that uses ``--fork``, like:
    ``npx hardhat node --fork <upstream-provider-url>``.

    Set the ``upstream_provider`` in the ``hardhat.mainnet_fork`` config
    section of your ``ape-config.yaml` file to specify which provider
    to use as your archive node.
    """

    @property
    def _fork_config(self) -> HardhatForkConfig:
        config = cast(HardhatNetworkConfig, self.config)
        return config.mainnet_fork or HardhatForkConfig()

    @cached_property
    def _upstream_provider(self) -> ProviderAPI:
        # NOTE: if 'upstream_provider_name' is 'None', this gets the default mainnet provider.
        mainnet = self.network.ecosystem.mainnet
        upstream_provider_name = self._fork_config.upstream_provider
        upstream_provider = mainnet.get_provider(provider_name=upstream_provider_name)
        return upstream_provider

    def connect(self):
        super().connect()

        # Verify that we're connected to a Hardhat node with mainnet-fork mode.
        self._upstream_provider.connect()
        upstream_genesis_block_hash = self._upstream_provider.get_block(0).hash
        self._upstream_provider.disconnect()
        if self.get_block(0).hash != upstream_genesis_block_hash:
            self.disconnect()
            raise HardhatProviderError(
                f"Upstream network is not {self.network.name.replace('-fork', '')}"
            )

    def build_command(self) -> List[str]:
        if not isinstance(self._upstream_provider, UpstreamProvider):
            raise HardhatProviderError(
                f"Provider '{self._upstream_provider.name}' is not an upstream provider."
            )

        fork_url = self._upstream_provider.connection_str
        if not fork_url:
            raise HardhatProviderError("Upstream provider does not have a ``connection_str``.")

        if fork_url.replace("localhost", "127.0.0.1") == self.uri:
            raise HardhatProviderError(
                "Invalid upstream-fork URL. Can't be same as local Hardhat node."
            )

        cmd = super().build_command()
        cmd.extend(("--fork", fork_url))
        fork_block_number = self._fork_config.block_number
        if fork_block_number is not None:
            cmd.extend(("--fork-block-number", str(fork_block_number)))

        return cmd


def _get_vm_error(web3_value_error: ValueError) -> TransactionError:
    if not len(web3_value_error.args):
        return VirtualMachineError(base_err=web3_value_error)

    err_data = web3_value_error.args[0]
    if not isinstance(err_data, dict):
        return VirtualMachineError(base_err=web3_value_error)

    message = str(err_data.get("message"))
    if not message:
        return VirtualMachineError(base_err=web3_value_error)

    # Handle `ContactLogicError` similarly to other providers in `ape`.
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
