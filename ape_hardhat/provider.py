import json
import random
import re
import shutil
from pathlib import Path
from subprocess import PIPE, CalledProcessError, call, check_output
from typing import Literal, Optional, Union, cast

from ape.api import (
    ForkedNetworkAPI,
    PluginConfig,
    ReceiptAPI,
    SubprocessProvider,
    TestProviderAPI,
    TraceAPI,
    TransactionAPI,
)
from ape.exceptions import (
    ContractLogicError,
    OutOfGasError,
    RPCTimeoutError,
    SignatureError,
    SubprocessError,
    TransactionError,
    VirtualMachineError,
)
from ape.logging import logger
from ape.types import AddressType, ContractCode, SnapshotID
from ape.utils import DEFAULT_TEST_HD_PATH, cached_property
from ape_ethereum.provider import Web3Provider
from ape_ethereum.trace import TraceApproach, TransactionTrace
from ape_ethereum.transactions import TransactionStatusEnum
from ape_test import ApeTestConfig
from chompjs import parse_js_object  # type: ignore
from eth_pydantic_types import HexBytes
from eth_utils import is_0x_prefixed, is_hex, to_hex
from packaging.version import Version
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from web3 import HTTPProvider, Web3
from web3.exceptions import ExtraDataLengthError
from web3.gas_strategies.rpc import rpc_gas_price_strategy

try:
    from web3.middleware import ExtraDataToPOAMiddleware  # type: ignore
except ImportError:
    from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware  # type: ignore
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
        count: {number_of_accounts},
        accountsBalance: "{initial_balance}",
      }}
    }},
  }},
}};
""".lstrip()
DEFAULT_HARDHAT_CONFIG_FILE_NAME = "hardhat.config.js"
HARDHAT_CONFIG_FILE_NAME_OPTIONS = (DEFAULT_HARDHAT_CONFIG_FILE_NAME, "hardhat.config.ts")
HARDHAT_PLUGIN_PATTERN = re.compile(r"hardhat-[A-Za-z0-9-]+$")
_NO_REASON_REVERT_MESSAGE = "Transaction reverted without a reason string"
_REVERT_REASON_PREFIX = (
    "Error: VM Exception while processing transaction: reverted with reason string "
)


def _validate_hardhat_config_file(
    path: Path,
    mnemonic: str,
    num_of_accounts: int,
    initial_balance: int,
    hardhat_version: str,
    hard_fork: Optional[str] = None,
    hd_path: Optional[str] = None,
) -> Path:
    if not path.is_file() and path.is_dir():
        path = path / DEFAULT_HARDHAT_CONFIG_FILE_NAME

    elif path.name not in HARDHAT_CONFIG_FILE_NAME_OPTIONS:
        raise ValueError(
            f"Expecting file name to be one of '{', '.join(HARDHAT_CONFIG_FILE_NAME_OPTIONS)}'. "
            f"Receiver '{path.name}'."
        )

    if not hard_fork:
        # Figure out hardfork to use.
        shanghai_cutoff = Version("2.14.0")
        vers = Version(hardhat_version)
        if vers < shanghai_cutoff:
            hard_fork = "merge"
        else:
            hard_fork = "shanghai"

    hd_path = hd_path or DEFAULT_TEST_HD_PATH
    content = HARDHAT_CONFIG.format(
        hd_path=hd_path,
        mnemonic=mnemonic,
        number_of_accounts=num_of_accounts,
        hard_fork=hard_fork,
        initial_balance=initial_balance,
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
                or accounts_config.get("path") != hd_path
            ):
                logger.warning(invalid_config_warning)

        else:
            # Not as good of a check, but we do our best.
            content = path.read_text()
            if (
                mnemonic not in content
                or hd_path not in content
                or str(num_of_accounts) not in content
            ):
                logger.warning(invalid_config_warning)

    except Exception as err:
        logger.error(
            f"Failed to parse Hardhat config file: {err}. "
            f"Some features may not work as intended."
        )

    return path


class PackageJson(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None
    dependencies: Optional[dict[str, str]] = None
    dev_dependencies: Optional[dict[str, str]] = Field(default=None, alias="devDependencies")


class HardhatForkConfig(PluginConfig):
    host: Optional[Union[str, Literal["auto"]]] = None
    """
    The host address or ``"auto"`` to use localhost with a random port (with attempts).
    If ``host`` is specified in the root config, this will take precendence for this
    network.
    """

    upstream_provider: Optional[str] = None
    """
    The name of the upstream provider, such as ``alchemy`` or ``infura``.
    """

    block_number: Optional[int] = None
    """
    The block number to fork. It is recommended to set this.
    """

    enable_hardhat_deployments: bool = False
    """
    Set to ``True`` if using the ``hardhat-deployments`` plugin and you wish
    to have those still occur.
    """


class HardhatNetworkConfig(PluginConfig):
    evm_version: Optional[str] = None
    """The EVM hardfork to use. Defaults to letting Hardhat decide."""

    host: Optional[Union[str, Literal["auto"]]] = None
    """The host address or ``"auto"`` to use localhost with a random port (with attempts)."""

    manage_process: bool = True
    """
    If ``True`` and the host is local and Hardhat is not running, will attempt to start.
    Defaults to ``True``. If ``host`` is remote, will not be able to start.
    """

    bin_path: Optional[Path] = None
    """
    The path to the Hardhat node binary.
    Only needed when using a non-standard path;
    otherwise, uses the binary from the installed Hardhat package.
    """

    request_timeout: int = 30
    fork_request_timeout: int = 300
    process_attempts: int = 5

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
    fork: dict[str, dict[str, HardhatForkConfig]] = {}

    model_config = SettingsConfigDict(extra="allow")

    @field_validator("bin_path", mode="before")
    @classmethod
    def resolve_bin_path(cls, value):
        if not value:
            return None

        # NOTE: Don't resolve symlinks because CLI bin will turn
        # into a .js file otherwise.
        return Path(value).expanduser().absolute()


class ForkedNetworkMetadata(BaseModel):
    """
    Metadata from the RPC ``hardhat_metadata``.
    """

    chain_id: int = Field(alias="chainId")
    """
    The chain ID of the network being forked.
    """

    fork_block_number: int = Field(alias="forkBlockNumber")
    """
    The number of the block that the network forked from.
    """

    fork_block_hash: str = Field(alias="forkBlockHash")
    """
    The hash of the block that the network forked from.
    """


class NetworkMetadata(BaseModel):
    """
    Metadata from the RPC ``hardhat_metadata``.
    """

    client_version: str = Field(alias="clientVersion")
    """
    A string identifying the version of Hardhat, for debugging purposes,
    not meant to be displayed to users.
    """

    instance_id: str = Field(alias="instanceId")
    """
    A 0x-prefixed hex-encoded 32 bytes id which uniquely identifies an
    instance/run of Hardhat Network. Running Hardhat Network more than
    once (even with the same version and parameters) will always result
    in different instanceIds. Running hardhat_reset will change the
    instanceId of an existing Hardhat Network.
    """

    forked_network: Optional[ForkedNetworkMetadata] = Field(default=None, alias="forkedNetwork")
    """
    An object with information about the forked network. This field is
    only present when Hardhat Network is forking another chain.
    """


def _call(*args):
    return call([*args], stderr=PIPE, stdout=PIPE, stdin=PIPE)


class HardhatProvider(SubprocessProvider, Web3Provider, TestProviderAPI):
    _host: Optional[str] = None
    attempted_ports: list[int] = []
    _did_warn_wrong_node = False

    # Will get set to False if notices not installed correctly.
    # However, will still attempt to connect and only raise
    # if failed to connect. This is because sometimes Hardhat may still work,
    # such when running via `pytester`.
    _detected_correct_install: bool = True

    # Hardhat supports `debug_trceCall`.
    _supports_debug_trace_call: Optional[bool] = True

    @property
    def unlocked_accounts(self) -> list[AddressType]:
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
        return self.settings.request_timeout

    @property
    def connection_id(self) -> Optional[str]:
        return f"{self.network_choice}:{self._host}"

    @property
    def _clean_uri(self) -> str:
        try:
            return str(URL(self.uri).with_user(None).with_password(None))
        except ValueError:
            # Likely isn't a real URI.
            return self.uri

    @property
    def _port(self) -> Optional[int]:
        return URL(self.uri).port

    @property
    def chain_id(self) -> int:
        return self.web3.eth.chain_id if hasattr(self.web3, "eth") else HARDHAT_CHAIN_ID

    @property
    def hardhat_version(self) -> str:
        # NOTE: Even if a version appears in this output, Hardhat still may not be installed
        # because of how NPM works.
        npx = shutil.which("npx") or "npx"
        result = check_output([npx, "hardhat", "--version"])
        return result.decode("utf8").strip()

    @cached_property
    def node_bin(self) -> str:
        npx = shutil.which("npx")
        suffix = "See ape-hardhat README for install steps."
        if not npx:
            raise HardhatSubprocessError(f"Could not locate `npx` executable. {suffix}")

        elif _call(npx, "--version") != 0:
            raise HardhatSubprocessError(f"`npm` executable returned error code. {suffix}.")

        hardhat_version = self.hardhat_version
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
        node = shutil.which("node")
        if not node:
            raise HardhatSubprocessError(f"Could not locate `node` executable. {suffix}")

        return node

    @property
    def config_host(self) -> Optional[str]:
        # NOTE: Overriden in Forked networks.
        return self.settings.host

    @property
    def uri(self) -> str:
        if self._host is not None:
            return self._host
        if config_host := self.config_host:
            if config_host == "auto":
                self._host = "auto"
                return self._host

            if not config_host.startswith("http"):
                if "127.0.0.1" in config_host or "localhost" in config_host:
                    self._host = f"http://{config_host}"
                else:
                    self._host = f"https://{config_host}"
            else:
                self._host = config_host
            if "127.0.0.1" in config_host or "localhost" in config_host:
                host_without_http = self._host[7:]
                if ":" not in host_without_http:
                    self._host = f"{self._host}:{DEFAULT_PORT}"
        else:
            self._host = f"http://127.0.0.1:{DEFAULT_PORT}"

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
    def bin_path(self) -> Path:
        if path := self.settings.bin_path:
            return path

        suffix = Path("node_modules") / ".bin" / "hardhat"
        options = (self.local_project.path, Path.home())
        for base in options:
            path = base / suffix
            if path.exists():
                return path

        # Default to the expected path suffx (relative).
        return suffix

    @property
    def _ape_managed_hardhat_config_file(self):
        return self.config_manager.DATA_FOLDER / "hardhat" / DEFAULT_HARDHAT_CONFIG_FILE_NAME

    @property
    def hardhat_config_file(self) -> Path:
        if self.settings.hardhat_config_file and self.settings.hardhat_config_file.is_dir():
            path = self.settings.hardhat_config_file / DEFAULT_HARDHAT_CONFIG_FILE_NAME
        elif self.settings.hardhat_config_file:
            path = self.settings.hardhat_config_file
        else:
            path = self._ape_managed_hardhat_config_file

        return path.expanduser().absolute()

    @property
    def metadata(self) -> NetworkMetadata:
        """
        Get network metadata, including an object about forked-metadata.
        If the network is not a fork, it will be ``None``.
        This is a helpful way of determing if a Hardhat node is a fork or not
        when connecting to a remote Hardhat network.
        """
        metadata = self.make_request("hardhat_metadata", [])
        return NetworkMetadata.model_validate(metadata)

    @cached_property
    def _test_config(self) -> ApeTestConfig:
        return cast(ApeTestConfig, self.config_manager.get_config("test"))

    @property
    def auto_mine(self) -> bool:
        return self.make_request("hardhat_getAutomine", [])

    @auto_mine.setter
    def auto_mine(self, value) -> None:
        # NOTE: The prefix is for `evm_` instead of `hardhat_` for some reason!
        return self.make_request("evm_setAutomine", [value])

    @cached_property
    def _package_json(self) -> PackageJson:
        json_path = self.local_project.path / "package.json"

        if not json_path.is_file():
            return PackageJson()

        return PackageJson.model_validate_json(json_path.read_text())

    @cached_property
    def _hardhat_plugins(self) -> list[str]:
        plugins: list[str] = []

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

        # NOTE: Must set port before calling 'super().connect()'.
        if "host" in self.provider_settings:
            self._host = self.provider_settings["host"]

        elif self._host is None:
            self._host = self.uri

        if self.is_connected:
            # Connects to already running process
            self._start()
        elif self.settings.manage_process and (
            "localhost" in self._host or "127.0.0.1" in self._host or self._host == "auto"
        ):
            # Only do base-process setup if not connecting to already-running process
            # and is running on localhost.
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
                for _ in range(self.settings.process_attempts):
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

        elif not self.is_connected:
            raise HardhatProviderError(
                f"Failed to connect to remote Hardhat node at '{self._clean_uri}'."
            )

    def _set_web3(self):
        if not self._host:
            return

        self._web3 = _create_web3(self.uri, self.timeout)

        try:
            is_connected = self._web3.is_connected()
        except Exception:
            self._web3 = None
            return

        if not is_connected:
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
            self._web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    def _start(self):
        use_random_port = self._host == "auto"
        if use_random_port:
            self._host = None

            if DEFAULT_PORT not in self.attempted_ports:
                self._host = f"http://127.0.0.1:{DEFAULT_PORT}"

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

        elif self._host is not None and ":" in self._host and self._port is not None:
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

    def build_command(self) -> list[str]:
        # Run `node` on the actual binary.
        # This allows the process mgmt to function and prevents dangling nodes.
        if not self.bin_path.is_file():
            raise HardhatSubprocessError("Unable to find Hardhat binary. Is it installed?")

        return self._get_command()

    def _get_command(self) -> list[str]:
        if self.hardhat_config_file == self._ape_managed_hardhat_config_file:
            # If we are using the Ape managed file, regenerate before launch.
            self._ape_managed_hardhat_config_file.unlink(missing_ok=True)

        # Validate (and create if needed) the user-given path.
        hh_config_path = _validate_hardhat_config_file(
            self.hardhat_config_file,
            self.mnemonic,
            self.number_of_accounts,
            self._test_config.balance,
            self.hardhat_version,
            hard_fork=self.config.evm_version,
            hd_path=self.test_config.hd_path or DEFAULT_TEST_HD_PATH,
        )

        return [
            self.node_bin,
            str(self.bin_path),
            "node",
            "--hostname",
            "127.0.0.1",
            "--port",
            f"{self._port or DEFAULT_PORT}",
            "--config",
            str(hh_config_path),
        ]

    def set_block_gas_limit(self, gas_limit: int) -> bool:
        return self.make_request("evm_setBlockGasLimit", [hex(gas_limit)]) is True

    def set_code(self, address: AddressType, code: ContractCode) -> bool:
        if isinstance(code, bytes):
            code = code.hex()

        elif not is_hex(code):
            raise ValueError(f"Value {code} is not convertible to hex")

        return self.make_request("hardhat_setCode", [address, code])

    def set_timestamp(self, new_timestamp: int):
        self.make_request("evm_setNextBlockTimestamp", [new_timestamp])

    def mine(self, num_blocks: int = 1):
        # NOTE: Request fails when given numbers with any left padded 0s.
        num_blocks_arg = f"0x{HexBytes(num_blocks).hex().replace('0x', '').lstrip('0')}"
        self.make_request("hardhat_mine", [num_blocks_arg])

    def snapshot(self) -> str:
        return self.make_request("evm_snapshot", [])

    def restore(self, snapshot_id: SnapshotID) -> bool:
        if isinstance(snapshot_id, int):
            snapshot_id = HexBytes(snapshot_id).hex()

        return self.make_request("evm_revert", [snapshot_id]) is True

    def unlock_account(self, address: AddressType) -> bool:
        return self.make_request("hardhat_impersonateAccount", [address])

    def _increment_call_func_coverage_hit_count(self, txn: TransactionAPI):
        """
        A helper method for increment a method call function hit count in a
        non-orthodox way. This is because Hardhat does support call traces yet.
        """
        if (
            not txn.receiver
            or not self._test_runner
            or not self._test_runner.config_wrapper.track_coverage
        ):
            return

        cov_data = self._test_runner.coverage_tracker.data
        if not cov_data:
            return

        contract_type = self.chain_manager.contracts.get(txn.receiver)
        if not contract_type:
            return

        contract_src = self.local_project._create_contract_source(contract_type)
        if not contract_src:
            return

        method_id = txn.data[:4]
        if method_id in contract_type.view_methods:
            method = contract_type.methods[method_id]
            self._test_runner.coverage_tracker.hit_function(contract_src, method)

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
            txn_dict = txn.model_dump(by_alias=True, mode="json")
            if isinstance(txn_dict.get("type"), int):
                txn_dict["type"] = HexBytes(txn_dict["type"]).hex()

            txn_params = cast(TxParams, txn_dict)
            vm_err = None
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

                vm_err = self.get_virtual_machine_error(err, txn=tx)
                if txn.raise_on_revert:
                    raise vm_err from err

                try:
                    txn_hash = txn.txn_hash
                except SignatureError:
                    txn_hash = None

            required_confirmations = txn.required_confirmations or 0
            if txn_hash is not None:
                receipt = self.get_receipt(
                    txn_hash.hex(), required_confirmations=required_confirmations, txn=txn_dict
                )
                if vm_err:
                    receipt.error = vm_err
                if txn.raise_on_revert:
                    receipt.raise_for_status()

            else:
                # If we get here, likely was a failed (but allowed-fail)
                # impersonated-sender receipt.
                receipt = self._create_receipt(
                    block_number=-1,  # Not in a block.
                    error=vm_err,
                    required_confirmations=required_confirmations,
                    status=TransactionStatusEnum.FAILING,
                    txn_hash="",  # No hash exists, likely from impersonated sender.
                    **txn_dict,
                )

        else:
            receipt = super().send_transaction(txn)

        return receipt

    def get_transaction_trace(self, transaction_hash: str, **kwargs) -> TraceAPI:
        if "debug_trace_transaction_parameters" not in kwargs:
            kwargs["debug_trace_transaction_parameters"] = {}

        if "call_trace_approach" not in kwargs:
            kwargs["call_trace_approach"] = TraceApproach.GETH_STRUCT_LOG_PARSE

        return _get_transaction_trace(transaction_hash, **kwargs)

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

        self.make_request("hardhat_setBalance", [account, amount_hex_str])

    def get_virtual_machine_error(self, exception: Exception, **kwargs) -> VirtualMachineError:
        if not len(exception.args):
            return VirtualMachineError(base_err=exception, **kwargs)

        err_data = exception.args[0]

        message = err_data if isinstance(err_data, str) else str(err_data.get("message"))
        if not message:
            return VirtualMachineError(base_err=exception, **kwargs)

        elif message.startswith("execution reverted: "):
            message = message.replace("execution reverted: ", "")

        def _handle_execution_reverted(revert_message: Optional[str] = None, **kwargs):
            if revert_message in ("", "0x", None):
                revert_message = TransactionError.DEFAULT_MESSAGE

            enriched = self.compiler_manager.enrich_error(
                ContractLogicError(revert_message=revert_message, **kwargs)
            )

            if enriched.message == TransactionError.DEFAULT_MESSAGE and revert_message:
                # Since input data is always missing, and to preserve backwards compat,
                # use the selector as the message still.
                enriched.message = revert_message

            # Show call trace if availble
            if enriched.txn:
                # Unlikely scenario where a transaction is on the error even though a receipt
                # exists.
                if isinstance(enriched.txn, TransactionAPI) and enriched.txn.receipt:
                    enriched.txn.receipt.show_trace()
                elif isinstance(enriched.txn, ReceiptAPI):
                    enriched.txn.show_trace()

            return enriched

        builtin_check = (
            "Error: VM Exception while processing transaction: reverted with panic code "
        )
        if message.startswith(builtin_check):
            message = message.replace(builtin_check, "")
            panic_code = message.split("(")[0].strip()
            return _handle_execution_reverted(panic_code, **kwargs)

        if message.startswith(_REVERT_REASON_PREFIX):
            message = message.replace(_REVERT_REASON_PREFIX, "").strip("'")
            return _handle_execution_reverted(message, **kwargs)

        elif _NO_REASON_REVERT_MESSAGE in message:
            return _handle_execution_reverted(**kwargs)

        elif message == "Transaction ran out of gas":
            return OutOfGasError(**kwargs)

        elif "reverted with an unrecognized custom error" in message and "(return data:" in message:
            # Happens during custom Solidity exceptions.
            message = message.split("(return data:")[-1].rstrip("/)").strip()
            return _handle_execution_reverted(message, **kwargs)

        return VirtualMachineError(message, **kwargs)


class HardhatForkProvider(HardhatProvider):
    """
    A Hardhat provider that uses ``--fork``, like:
    ``npx hardhat node --fork <upstream-provider-url>``.

    Set the ``upstream_provider`` in the ``hardhat.fork`` config
    section of your ``ape-config.yaml` file to specify which provider
    to use as your archive node.
    """

    @model_validator(mode="before")
    @classmethod
    def set_upstream_provider(cls, value):
        network = value["network"]
        adhoc_settings = value.get("provider_settings", {}).get("fork", {})
        ecosystem_name = network.ecosystem.name
        plugin_config = cls.config_manager.get_config(value["name"])
        config_settings = plugin_config.get("fork", {})

        def _get_upstream(data: dict) -> Optional[str]:
            return (
                data.get(ecosystem_name, {})
                .get(network.name.replace("-fork", ""), {})
                .get("upstream_provider")
            )

        # If upstream provider set anywhere in provider settings, ignore.
        if name := (_get_upstream(adhoc_settings) or _get_upstream(config_settings)):
            getattr(network.ecosystem.config, network.name).upstream_provider = name

        return value

    @property
    def forked_network(self) -> ForkedNetworkAPI:
        return cast(ForkedNetworkAPI, self.network)

    @property
    def fork_url(self) -> str:
        return self.forked_network.upstream_provider.connection_str

    @property
    def fork_block_number(self) -> Optional[int]:
        return self._fork_config.block_number

    @property
    def enable_hardhat_deployments(self) -> bool:
        return self._fork_config.enable_hardhat_deployments

    @property
    def timeout(self) -> int:
        return self.settings.fork_request_timeout

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

    @property
    def config_host(self) -> Optional[str]:
        # First, attempt to get the host from the forked config.
        if host := self._fork_config.host:
            return host

        return super().config_host

    def connect(self):
        super().connect()

        if not self.metadata.forked_network:
            # This will fail when trying to connect hardhat-fork to
            # a non-forked network.
            raise HardhatProviderError(
                "Network is not a fork. "
                "Hardhat is likely already running on the local network. "
                "Try using config:\n\n(ape-config.yaml)\n```\nhardhat:\n  "
                "host: auto\n```\n\nso that multiple processes can automatically "
                "use different ports."
            )

    def build_command(self) -> list[str]:
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
        forking_params: dict[str, Union[str, int]] = {"jsonRpcUrl": self.fork_url}
        block_number = block_number if block_number is not None else self.fork_block_number
        if block_number is not None:
            forking_params["blockNumber"] = block_number

        return self.make_request("hardhat_reset", [{"forking": forking_params}])


def _create_web3(uri: str, timeout: int) -> Web3:
    # NOTE: This method exists so can be mocked in testing.
    return Web3(HTTPProvider(uri, request_kwargs={"timeout": timeout}))


def _get_transaction_trace(transaction_hash: str, **kwargs) -> TraceAPI:
    # Abstracted for testing purposes.
    return TransactionTrace(transaction_hash=transaction_hash, **kwargs)
