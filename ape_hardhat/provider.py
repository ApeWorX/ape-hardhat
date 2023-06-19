import json
import random
import re
import shutil
from pathlib import Path
from subprocess import PIPE, CalledProcessError, call, check_output
from typing import Dict, Iterator, List, Literal, Optional, Union, cast

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
    RPCTimeoutError,
    SubprocessError,
    TransactionError,
    VirtualMachineError,
)
from ape.logging import logger
from ape.types import AddressType, CallTreeNode, ContractCode, SnapshotID, TraceFrame
from ape.utils import cached_property
from ape_test import Config as TestConfig
from chompjs import parse_js_object  # type: ignore
from eth_typing import HexStr
from eth_utils import is_0x_prefixed, is_hex, to_hex
from evm_trace import CallType
from evm_trace import TraceFrame as EvmTraceFrame
from evm_trace import get_calltree_from_geth_trace
from hexbytes import HexBytes
from pydantic import BaseModel, Field
from web3 import HTTPProvider, Web3
from web3.exceptions import ExtraDataLengthError
from web3.gas_strategies.rpc import rpc_gas_price_strategy
from web3.middleware import geth_poa_middleware
from web3.middleware.validation import MAX_EXTRADATA_LENGTH
from web3.types import TxParams
from yarl import URL

from .exceptions import HardhatNotInstalledError, HardhatProviderError, HardhatSubprocessError

EPHEMERAL_PORTS_START = 49152
EPHEMERAL_PORTS_END = 60999
DEFAULT_PORT = 8545
HARDHAT_CHAIN_ID = 31337
HARDHAT_CONFIG = """
// See https://hardhat.org/config/ for config options.
module.exports = {{
  networks: {{
    hardhat: {{
      hardfork: "{hard_fork}",
      // Base fee of 0 allows use of 0 gas price when testing
      initialBaseFeePerGas: 0,
      accounts: {{
        mnemonic: "{mnemonic}",
        path: "{hd_path}",
        count: {number_of_accounts}
      }}
    }},
  }},
}};
""".lstrip()
HARDHAT_HD_PATH = "m/44'/60'/0'"
DEFAULT_HARDHAT_CONFIG_FILE_NAME = "hardhat.config.js"
HARDHAT_CONFIG_FILE_NAME_OPTIONS = (DEFAULT_HARDHAT_CONFIG_FILE_NAME, "hardhat.config.ts")
HARDHAT_PLUGIN_PATTERN = re.compile(r"hardhat-[A-Za-z0-9-]+$")
DEFAULT_HARDHAT_HARD_FORK = "shanghai"
_NO_REASON_REVERT_MESSAGE = "Transaction reverted without a reason string"
_REVERT_REASON_PREFIX = (
    "Error: VM Exception while processing transaction: reverted with reason string "
)


def _validate_hardhat_config_file(
    path: Path,
    mnemonic: str,
    num_of_accounts: int,
    hard_fork: str = DEFAULT_HARDHAT_HARD_FORK,
):
    if not path.is_file() and path.is_dir():
        path = path / DEFAULT_HARDHAT_CONFIG_FILE_NAME

    elif path.name not in HARDHAT_CONFIG_FILE_NAME_OPTIONS:
        raise ValueError(
            f"Expecting file name to be one of '{', '.join(HARDHAT_CONFIG_FILE_NAME_OPTIONS)}'. "
            f"Receiver '{path.name}'."
        )

    content = HARDHAT_CONFIG.format(
        hd_path=HARDHAT_HD_PATH,
        mnemonic=mnemonic,
        number_of_accounts=num_of_accounts,
        hard_fork=hard_fork,
    )
    if not path.is_file():
        # Create default '.js' file.
        logger.debug(f"Creating file '{path.name}'.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    invalid_config_warning = (
        f"Existing '{path.name}' conflicts with ape. "
        "Some features may not work as intended. "
        f"The default config looks like this:\n{content}\n"
        "NOTE: You can configure the test account mnemonic "
        "and/or number of test accounts using your `ape-config.yaml`: "
        "https://docs.apeworx.io/ape/stable/userguides/config.html#testing"
    )

    try:
        js_obj = {}
        try:
            js_obj = parse_js_object(path.read_text())
        except Exception:
            # Will fail if using type-script features.
            pass

        if js_obj:
            accounts_config = js_obj.get("networks", {}).get("hardhat", {}).get("accounts", {})
            if not accounts_config or (
                accounts_config.get("mnemonic") != mnemonic
                or accounts_config.get("count") != num_of_accounts
                or accounts_config.get("path") != HARDHAT_HD_PATH
            ):
                logger.warning(invalid_config_warning)

        else:
            # Not as good of a check, but we do our best.
            content = path.read_text()
            if (
                mnemonic not in content
                or HARDHAT_HD_PATH not in content
                or str(num_of_accounts) not in content
            ):
                logger.warning(invalid_config_warning)

    except Exception as err:
        logger.error(
            f"Failed to parse Hardhat config file: {err}. "
            f"Some features may not work as intended."
        )


class PackageJson(BaseModel):
    name: Optional[str]
    version: Optional[str]
    description: Optional[str]
    dependencies: Optional[Dict[str, str]]
    dev_dependencies: Optional[Dict[str, str]] = Field(None, alias="devDependencies")


class HardhatForkConfig(PluginConfig):
    upstream_provider: Optional[str] = None
    block_number: Optional[int] = None
    enable_hardhat_deployments: bool = False


class HardhatNetworkConfig(PluginConfig):
    port: Optional[Union[int, Literal["auto"]]] = DEFAULT_PORT
    """Depreciated. Use ``host`` config."""

    host: Optional[Union[str, Literal["auto"]]] = None
    """The host address or ``"auto"`` to use localhost with a random port (with attempts)."""

    manage_process: bool = True
    """
    If ``True`` and the host is local and Hardhat is not running, will attempt to start.
    Defaults to ``True``. If ``host`` is remote, will not be able to start.
    """

    request_timeout: int = 30
    fork_request_timeout: int = 300
    process_attempts: int = 0

    hardhat_config_file: Optional[Path] = None
    """
    Optionally specify a Hardhat config file to use
    (in the case when you don't wish to use the one Ape creates).
    Note: If you do this, you may need to ensure its settings
    matches Ape's.
    """

    # For setting the values in --fork and --fork-block-number command arguments.
    # Used only in HardhatForkProvider.
    # Mapping of ecosystem_name => network_name => HardhatForkConfig
    fork: Dict[str, Dict[str, HardhatForkConfig]] = {}

    class Config:
        extra = "allow"


def _call(*args):
    return call([*args], stderr=PIPE, stdout=PIPE, stdin=PIPE)


class HardhatProvider(SubprocessProvider, Web3Provider, TestProviderAPI):
    _host: Optional[str] = None
    attempted_ports: List[int] = []
    _did_warn_wrong_node = False

    # Will get set to False if notices not installed correctly.
    # However, will still attempt to connect and only raise
    # if failed to connect. This is because sometimes Hardhat may still work,
    # such when running via `pytester`.
    _detected_correct_install: bool = True

    @property
    def unlocked_accounts(self) -> List[AddressType]:
        return list(self.account_manager.test_accounts._impersonated_accounts)

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
        return self.config.request_timeout

    @property
    def _clean_uri(self) -> str:
        return str(URL(self.uri).with_user(None).with_password(None))

    @property
    def _port(self) -> Optional[int]:
        return URL(self.uri).port

    @property
    def chain_id(self) -> int:
        return self.web3.eth.chain_id if hasattr(self.web3, "eth") else HARDHAT_CHAIN_ID

    @property
    def npx_bin(self) -> str:
        npx = shutil.which("npx")
        suffix = "See ape-hardhat README for install steps."
        if not npx:
            raise HardhatSubprocessError(f"Could not locate `npx` executable. {suffix}")

        elif _call(npx, "--version") != 0:
            raise HardhatSubprocessError(f"`npm` executable returned error code. {suffix}.")

        # NOTE: Even if a version appears in this output, Hardhat still may not be installed
        # because of how NPM works.
        hardhat_version = check_output([npx, "hardhat", "--version"]).decode("utf8").strip()
        logger.debug(f"Using Hardhat version '{hardhat_version}'.")
        if not hardhat_version or not hardhat_version[0].isnumeric():
            raise HardhatNotInstalledError()

        npm = shutil.which("npm")
        if not npm:
            raise HardhatSubprocessError(f"Could not locate `npm` executable. {suffix}")

        try:
            install_result = check_output([npm, "list", "hardhat", "--json"])
        except CalledProcessError:
            self._detected_correct_install = False
            return npx

        data = json.loads(install_result)

        # This actually ensures it is installed.
        self._detected_correct_install = "hardhat" in data.get("dependencies", {})
        return npx

    @property
    def project_folder(self) -> Path:
        return self.config_manager.PROJECT_FOLDER

    @property
    def uri(self) -> str:
        if self.config.host:
            if "https" not in self.config.host or "http" not in self.config.host:
                self.config.host = "http://" + self.config.host  # type: ignore
        if self._host is None:
            self._host = self.config.host or f"http://127.0.0.1:{DEFAULT_PORT}"
        return self._host

    @property
    def priority_fee(self) -> int:
        """
        Priority fee not needed in development network.
        """
        return 0

    @property
    def is_connected(self) -> bool:
        if self._host in ("auto", None):
            # Hasn't tried yet.
            return False

        self._set_web3()
        return self._web3 is not None

    @property
    def hardhat_config_file(self) -> Path:
        if self.config.hardhat_config_file and self.config.hardhat_config_file.is_dir():
            path = self.config.hardhat_config_file / DEFAULT_HARDHAT_CONFIG_FILE_NAME
        elif self.config.hardhat_config_file:
            path = self.config.hardhat_config_file
        else:
            path = self.config_manager.DATA_FOLDER / "hardhat" / DEFAULT_HARDHAT_CONFIG_FILE_NAME

        return path.expanduser().absolute()

    @cached_property
    def _test_config(self) -> TestConfig:
        return cast(TestConfig, self.config_manager.get_config("test"))

    @cached_property
    def _package_json(self) -> PackageJson:
        json_path = self.project_folder / "package.json"

        if not json_path.is_file():
            return PackageJson()

        return PackageJson.parse_file(json_path)

    @cached_property
    def _hardhat_plugins(self) -> List[str]:
        plugins: List[str] = []

        def package_is_plugin(package: str) -> bool:
            return re.search(HARDHAT_PLUGIN_PATTERN, package) is not None

        if self._package_json.dependencies:
            plugins.extend(filter(package_is_plugin, self._package_json.dependencies.keys()))

        if self._package_json.dev_dependencies:
            plugins.extend(filter(package_is_plugin, self._package_json.dev_dependencies.keys()))

        return plugins

    def _has_hardhat_plugin(self, plugin_name: str) -> bool:
        return next((True for plugin in self._hardhat_plugins if plugin == plugin_name), False)

    def connect(self):
        """
        Start the hardhat process and verify it's up and accepting connections.
        """

        _validate_hardhat_config_file(
            self.hardhat_config_file, self.mnemonic, self.number_of_accounts
        )

        # NOTE: Must set port before calling 'super().connect()'.
        warning = "`port` setting is depreciated. Please use `host` key that includes the port."

        if "port" in self.provider_settings:
            # TODO: Can remove after 0.7.
            logger.warning(warning)
            self._host = f"http://127.0.0.1:{self.provider_settings['port']}"

        elif self.config.port != DEFAULT_PORT and self.config.host is not None:
            raise HardhatProviderError(
                "Cannot use depreciated `port` field with `host`."
                "Place `port` at end of `host` instead."
            )

        elif self.config.port != DEFAULT_PORT:
            # We only get here if the user configured a port without a host,
            # the old way of doing it. TODO: Can remove after 0.7.
            logger.warning(warning)
            if self.config.port not in (None, "auto"):
                self._host = f"http://127.0.0.1:{self.config.port}"
            else:
                # This will trigger selecting a random port on localhost and trying.
                self._host = "auto"

        elif "host" in self.provider_settings:
            self._host = self.provider_settings["host"]

        elif self._host is None:
            self._host = self.uri

        if self.is_connected:
            # Connects to already running process
            self._start()
        elif self.config.manage_process:
            # Only do base-process setup if not connecting to already-running process
            super().connect()

            if self._host:
                self._set_web3()
                if not self._web3:
                    self._start()
                else:
                    # The user configured a host and the hardhat process was already running.
                    logger.info(
                        f"Connecting to existing '{self.process_name}' at host '{self._clean_uri}'."
                    )
            else:
                for _ in range(self.config.process_attempts):
                    try:
                        self._start()
                        break
                    except HardhatNotInstalledError:
                        # Is a sub-class of `HardhatSubprocessError` but we to still raise
                        # so we don't keep retrying.
                        raise
                    except SubprocessError as exc:
                        logger.info("Retrying Hardhat subprocess startup: %r", exc)
                        self._host = None
        else:
            raise HardhatProviderError(
                f"Failed to connect to remote Hardhat node at {self._clean_uri}`"
            )

    def _set_web3(self):
        if not self._host:
            return

        self._web3 = _create_web3(self.uri, self.timeout)
        if not self._web3.is_connected():
            self._web3 = None
            return

        # Verify is actually a Hardhat provider,
        # or else skip it to possibly try another port.
        client_version = self._web3.client_version.lower()
        if "hardhat" in client_version:
            self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
        elif self._port is not None:
            raise HardhatProviderError(
                f"A process that is not a Hardhat node is running at host {self._clean_uri}."
            )
        else:
            # Not sure if possible to get here.
            raise HardhatProviderError("Failed to start Hardhat process.")

        def check_poa(block_id) -> bool:
            try:
                block = self.web3.eth.get_block(block_id)
            except ExtraDataLengthError:
                return True
            else:
                return (
                    "proofOfAuthorityData" in block
                    or len(block.get("extraData", "")) > MAX_EXTRADATA_LENGTH
                )

        # Handle if using PoA Hardhat
        if any(map(check_poa, (0, "latest"))):
            self._web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    def _start(self):
        use_random_port = self._host == "auto"
        if use_random_port:
            self._host = None

            if DEFAULT_PORT not in self.attempted_ports and not use_random_port:
                self._host = f"127.0.0.1:{DEFAULT_PORT}"
            else:
                # Pick a random port
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

                self.attempted_ports.append(port)
                self._host = f"http://127.0.0.1:{port}"

        elif ":" in self._host and self._port is not None:
            # Append the one and only port to the attempted ports list, for honest keeping.
            self.attempted_ports.append(self._port)

        else:
            self._host = f"http://127.0.0.1:{DEFAULT_PORT}"

        try:
            self.start()
        except RPCTimeoutError as err:
            if not self._detected_correct_install:
                raise HardhatNotInstalledError() from err

            raise  # RPCTimeoutError

    def disconnect(self):
        self._web3 = None
        self._host = None
        super().disconnect()

    def build_command(self) -> List[str]:
        return [
            self.npx_bin,
            "hardhat",
            "node",
            "--hostname",
            "127.0.0.1",
            "--port",
            f"{self._port or DEFAULT_PORT}",
            "--config",
            str(self.hardhat_config_file),
        ]

    def set_block_gas_limit(self, gas_limit: int) -> bool:
        return self._make_request("evm_setBlockGasLimit", [hex(gas_limit)]) is True

    def set_code(self, address: AddressType, code: ContractCode) -> bool:
        if isinstance(code, bytes):
            code = code.hex()

        elif not is_hex(code):
            raise ValueError(f"Value {code} is not convertible to hex")

        return self._make_request("hardhat_setCode", [address, code])

    def set_timestamp(self, new_timestamp: int):
        self._make_request("evm_setNextBlockTimestamp", [new_timestamp])

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
        return self._make_request("hardhat_impersonateAccount", [address])

    def send_transaction(self, txn: TransactionAPI) -> ReceiptAPI:
        """
        Creates a new message call transaction or a contract creation
        for signed transactions.
        """

        sender = txn.sender
        if sender:
            sender = self.conversion_manager.convert(txn.sender, AddressType)

        sender_address = cast(AddressType, sender)
        if sender_address in self.unlocked_accounts:
            # Allow for an unsigned transaction
            txn = self.prepare_transaction(txn)
            txn_dict = txn.dict()
            if isinstance(txn_dict.get("type"), int):
                txn_dict["type"] = HexBytes(txn_dict["type"]).hex()

            txn_params = cast(TxParams, txn_dict)
            try:
                txn_hash = self.web3.eth.send_transaction(txn_params)
            except ValueError as err:
                err_args = getattr(err, "args", None)
                tx: Union[TransactionAPI, ReceiptAPI]
                if (
                    err_args is not None
                    and isinstance(err_args[0], dict)
                    and "data" in err_args[0]
                    and "txHash" in err_args[0]["data"]
                ):
                    # Txn hash won't work in Ape at this point, but at least
                    # we have it here. Use the receipt instead of the txn
                    # for the err, so we can do source tracing.
                    txn_hash_from_err = err_args[0]["data"]["txHash"]
                    tx = self.get_receipt(txn_hash_from_err)

                else:
                    tx = txn

                raise self.get_virtual_machine_error(err, txn=tx) from err

            receipt = self.get_receipt(
                txn_hash.hex(), required_confirmations=txn.required_confirmations or 0, txn=txn_dict
            )
            receipt.raise_for_status()

        else:
            receipt = super().send_transaction(txn)

        return receipt

    def get_receipt(
        self,
        txn_hash: str,
        required_confirmations: int = 0,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> ReceiptAPI:
        try:
            # Try once without waiting first.
            # NOTE: This is required for txn sent with an impersonated account.
            receipt_data = dict(self.web3.eth.get_transaction_receipt(HexStr(txn_hash)))
        except Exception:
            return super().get_receipt(
                txn_hash, required_confirmations=required_confirmations, timeout=timeout
            )

        txn = kwargs.get("txn", dict(self.web3.eth.get_transaction(HexStr(txn_hash))))
        data: Dict = {"txn_hash": txn_hash, **receipt_data, **txn}
        if "gas_price" not in data:
            data["gas_price"] = self.gas_price

        receipt = self.network.ecosystem.decode_receipt(data)
        self.chain_manager.history.append(receipt)
        return receipt

    def get_transaction_trace(self, txn_hash: str) -> Iterator[TraceFrame]:
        for trace in self._get_transaction_trace(txn_hash):
            yield self._create_trace_frame(trace)

    def _get_transaction_trace(self, txn_hash: str) -> Iterator[EvmTraceFrame]:
        result = self._make_request("debug_traceTransaction", [txn_hash])
        frames = result.get("structLogs", [])
        for frame in frames:
            yield EvmTraceFrame(**frame)

    def get_call_tree(self, txn_hash: str) -> CallTreeNode:
        receipt = self.chain_manager.get_receipt(txn_hash)

        # Subtract base gas costs.
        # (21_000 + 4 gas per 0-byte and 16 gas per non-zero byte).
        data_gas = sum([4 if x == 0 else 16 for x in receipt.data])
        method_gas_cost = receipt.gas_used - 21_000 - data_gas

        evm_call = get_calltree_from_geth_trace(
            self._get_transaction_trace(txn_hash),
            gas_cost=method_gas_cost,
            gas_limit=receipt.gas_limit,
            address=receipt.receiver,
            calldata=receipt.data,
            value=receipt.value,
            call_type=CallType.CALL,
            failed=receipt.failed,
        )
        return self._create_call_tree_node(evm_call, txn_hash=txn_hash)

    def set_balance(self, account: AddressType, amount: Union[int, float, str, bytes]):
        is_str = isinstance(amount, str)
        _is_hex = False if not is_str else is_0x_prefixed(str(amount))
        is_key_word = is_str and len(str(amount).split(" ")) > 1
        if is_key_word:
            # This allows values such as "1000 ETH".
            amount = self.conversion_manager.convert(amount, int)
            is_str = False

        amount_hex_str = str(amount)

        # Convert to hex str
        if is_str and not _is_hex:
            amount_hex_str = to_hex(int(amount))
        elif isinstance(amount, int) or isinstance(amount, bytes):
            amount_hex_str = to_hex(amount)

        self._make_request("hardhat_setBalance", [account, amount_hex_str])

    def get_virtual_machine_error(self, exception: Exception, **kwargs) -> VirtualMachineError:
        if not len(exception.args):
            return VirtualMachineError(base_err=exception, **kwargs)

        err_data = exception.args[0]

        message = err_data if isinstance(err_data, str) else str(err_data.get("message"))
        if not message:
            return VirtualMachineError(base_err=exception, **kwargs)

        elif message.startswith("execution reverted: "):
            message = message.replace("execution reverted: ", "")

        builtin_check = (
            "Error: VM Exception while processing transaction: reverted with panic code "
        )
        if message.startswith(builtin_check):
            message = message.replace(builtin_check, "")
            panic_code = message.split("(")[0].strip()
            err = ContractLogicError(revert_message=panic_code, **kwargs)
            enriched_err = self.compiler_manager.enrich_error(err)
            if enriched_err != err:
                # It was enriched.
                return enriched_err

            # Use full message.
            return ContractLogicError(revert_message=message, **kwargs)

        if message.startswith(_REVERT_REASON_PREFIX):
            message = message.replace(_REVERT_REASON_PREFIX, "").strip("'")
            err = ContractLogicError(revert_message=message, **kwargs)
            return self.compiler_manager.enrich_error(err)

        elif _NO_REASON_REVERT_MESSAGE in message:
            err = ContractLogicError(**kwargs)
            return self.compiler_manager.enrich_error(err)

        elif message == "Transaction ran out of gas":
            return OutOfGasError(**kwargs)

        elif "reverted with an unrecognized custom error" in message and "(return data:" in message:
            # Happens during custom Solidity exceptions.
            message = message.split("(return data:")[-1].rstrip("/)").strip()
            err = ContractLogicError(revert_message=message, **kwargs)
            enriched_error = self.compiler_manager.enrich_error(err)

            if enriched_error.message == TransactionError.DEFAULT_MESSAGE:
                # Since input data is always missing, and to preserve backwards compat,
                # use the selector as the message still.
                enriched_error.message = message

            return enriched_error

        return VirtualMachineError(message, **kwargs)


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
    def enable_hardhat_deployments(self) -> bool:
        return self._fork_config.enable_hardhat_deployments

    @property
    def timeout(self) -> int:
        return self.config.fork_request_timeout

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

        try:
            upstream_genesis_block_hash = self._upstream_provider.get_block(0).hash
        except ExtraDataLengthError as err:
            if isinstance(self._upstream_provider, Web3Provider):
                logger.error(
                    f"Upstream provider '{self._upstream_provider.name}' "
                    f"missing Geth PoA middleware."
                )
                self._upstream_provider.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
                upstream_genesis_block_hash = self._upstream_provider.get_block(0).hash
            else:
                raise HardhatProviderError(f"Unable to get genesis block: {err}.") from err

        self._upstream_provider.disconnect()

        if self.get_block(0).hash != upstream_genesis_block_hash:
            logger.warning(
                "Upstream network has mismatching genesis block. "
                "This could be an issue with hardhat."
            )

    def build_command(self) -> List[str]:
        if not self.fork_url:
            raise HardhatProviderError("Upstream provider does not have a ``connection_str``.")

        if self.fork_url.replace("localhost", "127.0.0.1").replace("http://", "") == self.uri:
            raise HardhatProviderError(
                "Invalid upstream-fork URL. Can't be same as local Hardhat node."
            )

        cmd = super().build_command()
        cmd.extend(("--fork", self.fork_url))

        # --no-deploy option is only available if hardhat-deploy is installed
        if not self.enable_hardhat_deployments and self._has_hardhat_plugin("hardhat-deploy"):
            cmd.append("--no-deploy")
        if self.fork_block_number is not None:
            cmd.extend(("--fork-block-number", str(self.fork_block_number)))

        return cmd

    def reset_fork(self, block_number: Optional[int] = None):
        forking_params: Dict[str, Union[str, int]] = {"jsonRpcUrl": self.fork_url}
        block_number = block_number if block_number is not None else self.fork_block_number
        if block_number is not None:
            forking_params["blockNumber"] = block_number

        return self._make_request("hardhat_reset", [{"forking": forking_params}])


def _create_web3(uri: str, timeout: int) -> Web3:
    # NOTE: This method exists so can be mocked in testing.
    return Web3(HTTPProvider(uri, request_kwargs={"timeout": timeout}))
