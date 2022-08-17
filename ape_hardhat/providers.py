import random
import shutil
from pathlib import Path
from subprocess import PIPE, call
from typing import Dict, Iterator, List, Optional, Union, cast

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
    ProviderError,
    SubprocessError,
    VirtualMachineError,
)
from ape.logging import logger
from ape.types import AddressType, SnapshotID
from ape.utils import cached_property
from ape_test import Config as TestConfig
from eth_utils import is_0x_prefixed, to_hex
from evm_trace import CallTreeNode, CallType, TraceFrame, get_calltree_from_geth_trace
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.middleware import geth_poa_middleware

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
_NO_REASON_REVERT_MESSAGE = "Transaction reverted without a reason string"
_REVERT_REASON_PREFIX = (
    "Error: VM Exception while processing transaction: reverted with reason string "
)


class HardhatConfigJS:
    """
    A class representing the actual ``hardhat.config.js`` file.
    """

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
        return self._base_path / HARDHAT_CONFIG_FILE_NAME

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
    request_timeout: int = 30
    fork_request_timeout: int = 300

    # For setting the values in --fork and --fork-block-number command arguments.
    # Used only in HardhatForkProvider.
    # Mapping of ecosystem_name => network_name => HardhatForkConfig
    fork: Dict[str, Dict[str, HardhatForkConfig]] = {}

    class Config:
        extra = "allow"


def _call(*args):
    return call([*args], stderr=PIPE, stdout=PIPE, stdin=PIPE)


class HardhatProvider(SubprocessProvider, Web3Provider, TestProviderAPI):
    port: Optional[int] = None
    attempted_ports: List[int] = []
    unlocked_accounts: List[AddressType] = []

    @property
    def mnemonic(self) -> str:
        return self._test_config.mnemonic

    @property
    def number_of_accounts(self) -> int:
        return self._test_config.number_of_accounts

    @property
    def process_name(self) -> str:
        return "Hardhat node"

    @property
    def timeout(self) -> int:
        return self.config.request_timeout  # type: ignore

    @property
    def chain_id(self) -> int:
        if hasattr(self.web3, "eth"):
            return self.web3.eth.chain_id
        else:
            return HARDHAT_CHAIN_ID

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

    @cached_property
    def _test_config(self) -> TestConfig:
        return cast(TestConfig, self.config_manager.get_config("test"))

    def connect(self):
        """
        Start the hardhat process and verify it's up and accepting connections.
        """

        js_config = HardhatConfigJS(self.project_folder, self.mnemonic, self.number_of_accounts)
        js_config.write_if_not_exists()

        # NOTE: Must set port before calling 'super().connect()'.
        if not self.port:
            self.port = self.config.port  # type: ignore

        if self.is_connected:
            # Connects to already running process
            self._start()
        else:
            # Only do base-process setup if not connecting to already-running process
            super().connect()

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

        self._web3 = Web3(HTTPProvider(self.uri, request_kwargs={"timeout": self.timeout}))
        if not self._web3.isConnected():
            self._web3 = None
            return

        # Verify is actually a Hardhat provider,
        # or else skip it to possibly try another port.
        client_version = self._web3.clientVersion

        if "hardhat" in client_version.lower():
            self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
        else:
            raise ProviderError(
                f"Port '{self.port}' already in use by another process that isn't a Hardhat node."
            )

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
                        ports_str = ", ".join([str(p) for p in self.attempted_ports])
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

    def set_block_gas_limit(self, gas_limit: int) -> bool:
        return self._make_request("evm_setBlockGasLimit", [hex(gas_limit)]) is True

    def set_timestamp(self, new_timestamp: int):
        pending_timestamp = self.get_block("pending").timestamp
        seconds_to_increase = new_timestamp - pending_timestamp
        self._make_request("evm_increaseTime", [seconds_to_increase])

    def mine(self, num_blocks: int = 1):
        # NOTE: Request fails when given numbers with any left padded 0s.
        num_blocks_arg = f"0x{HexBytes(num_blocks).hex().replace('0x', '').lstrip('0')}"
        self._make_request("hardhat_mine", [num_blocks_arg])

    def snapshot(self) -> str:
        return self._make_request("evm_snapshot", [])

    def revert(self, snapshot_id: SnapshotID) -> bool:
        if isinstance(snapshot_id, int):
            snapshot_id = HexBytes(snapshot_id).hex()

        return self._make_request("evm_revert", [snapshot_id]) is True

    def unlock_account(self, address: AddressType) -> bool:
        result = self._make_request("hardhat_impersonateAccount", [address])
        if result:
            self.unlocked_accounts.append(address)

        return result is True

    def send_transaction(self, txn: TransactionAPI) -> ReceiptAPI:
        """
        Creates a new message call transaction or a contract creation
        for signed transactions.
        """

        sender = txn.sender
        if sender:
            sender = self.conversion_manager.convert(txn.sender, AddressType)

        if sender in self.unlocked_accounts:
            # Allow for an unsigned transaction
            txn = self.prepare_transaction(txn)
            txn_dict = txn.dict()

            try:
                txn_hash = self._web3.eth.send_transaction(txn_dict)  # type: ignore
            except ValueError as err:
                raise self.get_virtual_machine_error(err) from err

            receipt = self.get_transaction(
                txn_hash.hex(), required_confirmations=txn.required_confirmations or 0
            )
            receipt.raise_for_status()

        else:
            receipt = super().send_transaction(txn)

        return receipt

    def get_transaction_trace(self, txn_hash: str) -> Iterator[TraceFrame]:
        result = self._make_request("debug_traceTransaction", [txn_hash])
        frames = result.get("structLogs", [])
        for frame in frames:
            yield TraceFrame(**frame)

    def get_call_tree(self, txn_hash: str) -> CallTreeNode:
        receipt = self.get_transaction(txn_hash)
        root_node_kwargs = {
            "gas_cost": receipt.gas_used,
            "gas_limit": receipt.gas_limit,
            "address": receipt.receiver,
            "calldata": receipt.data,
            "value": receipt.value,
            "call_type": CallType.CALL,
            "failed": receipt.failed,
        }
        return get_calltree_from_geth_trace(receipt.trace, **root_node_kwargs)

    def set_balance(self, account: AddressType, amount: Union[int, float, str, bytes]):
        is_str = isinstance(amount, str)
        is_hex = False if not is_str else is_0x_prefixed(str(amount))
        is_key_word = is_str and len(str(amount).split(" ")) > 1
        if is_key_word:
            # This allows values such as "1000 ETH".
            amount = self.conversion_manager.convert(amount, int)
            is_str = False

        amount_hex_str = str(amount)

        # Convert to hex str
        if is_str and not is_hex:
            amount_hex_str = to_hex(int(amount))
        elif isinstance(amount, int) or isinstance(amount, bytes):
            amount_hex_str = to_hex(amount)

        self._make_request("hardhat_setBalance", [account, amount_hex_str])

    def get_virtual_machine_error(self, exception: Exception) -> VirtualMachineError:
        if not len(exception.args):
            return VirtualMachineError(base_err=exception)

        err_data = exception.args[0]

        message = err_data if isinstance(err_data, str) else str(err_data.get("message"))
        if not message:
            return VirtualMachineError(base_err=exception)
        elif message.startswith("execution reverted: "):
            message = message.replace("execution reverted: ", "")

        if message.startswith(_REVERT_REASON_PREFIX):
            message = message.replace(_REVERT_REASON_PREFIX, "").strip("'")
            return ContractLogicError(revert_message=message)

        elif _NO_REASON_REVERT_MESSAGE in message:
            return ContractLogicError()

        elif message == "Transaction ran out of gas":
            return OutOfGasError()  # type: ignore

        return VirtualMachineError(message=message)


class HardhatForkProvider(HardhatProvider):
    """
    A Hardhat provider that uses ``--fork``, like:
    ``npx hardhat node --fork <upstream-provider-url>``.

    Set the ``upstream_provider`` in the ``hardhat.fork`` config
    section of your ``ape-config.yaml` file to specify which provider
    to use as your archive node.
    """

    @property
    def fork_url(self) -> str:
        if not isinstance(self._upstream_provider, UpstreamProvider):
            raise HardhatProviderError(
                f"Provider '{self._upstream_provider.name}' is not an upstream provider."
            )

        return self._upstream_provider.connection_str

    @property
    def fork_block_number(self) -> Optional[int]:
        return self._fork_config.block_number

    @property
    def timeout(self) -> int:
        return self.config.fork_request_timeout  # type: ignore

    @property
    def _upstream_network_name(self) -> str:
        return self.network.name.replace("-fork", "")

    @cached_property
    def _fork_config(self) -> HardhatForkConfig:
        config = cast(HardhatNetworkConfig, self.config)

        ecosystem_name = self.network.ecosystem.name
        if ecosystem_name not in config.fork:
            return HardhatForkConfig()  # Just use default

        network_name = self._upstream_network_name
        if network_name not in config.fork[ecosystem_name]:
            return HardhatForkConfig()  # Just use default

        return config.fork[ecosystem_name][network_name]

    @cached_property
    def _upstream_provider(self) -> ProviderAPI:
        upstream_network = self.network.ecosystem.networks[self._upstream_network_name]
        upstream_provider_name = self._fork_config.upstream_provider
        # NOTE: if 'upstream_provider_name' is 'None', this gets the default mainnet provider.
        return upstream_network.get_provider(provider_name=upstream_provider_name)

    def connect(self):
        super().connect()

        # Verify that we're connected to a Hardhat node with mainnet-fork mode.
        self._upstream_provider.connect()
        upstream_chain_id = self._upstream_provider.chain_id
        upstream_genesis_block_hash = self._upstream_provider.get_block(0).hash
        self._upstream_provider.disconnect()

        # If upstream network is rinkeby, goerli, or kovan (PoA test-nets)
        if upstream_chain_id in (4, 5, 42):
            self._web3.middleware_onion.inject(geth_poa_middleware, layer=0)

        if self.get_block(0).hash != upstream_genesis_block_hash:
            logger.warning(
                "Upstream network has mismatching genesis block. "
                "This could be an issue with hardhat."
            )

    def build_command(self) -> List[str]:
        if not self.fork_url:
            raise HardhatProviderError("Upstream provider does not have a ``connection_str``.")

        if self.fork_url.replace("localhost", "127.0.0.1") == self.uri:
            raise HardhatProviderError(
                "Invalid upstream-fork URL. Can't be same as local Hardhat node."
            )

        cmd = super().build_command()
        cmd.extend(("--fork", self.fork_url))
        if self.fork_block_number is not None:
            cmd.extend(("--fork-block-number", str(self.fork_block_number)))

        return cmd

    def reset_fork(self, block_number: Optional[int] = None):
        forking_params: Dict[str, Union[str, int]] = {"jsonRpcUrl": self.fork_url}
        block_number = block_number if block_number is not None else self.fork_block_number
        if block_number is not None:
            forking_params["blockNumber"] = block_number

        return self._make_request("hardhat_reset", [{"forking": forking_params}])
