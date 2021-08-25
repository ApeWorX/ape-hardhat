try:
    from importlib.metadata import PackageNotFoundError as _PackageNotFoundError  # type: ignore
    from importlib.metadata import version as _version  # type: ignore
except ModuleNotFoundError:
    from importlib_metadata import PackageNotFoundError as _PackageNotFoundError  # type: ignore
    from importlib_metadata import version as _version  # type: ignore

try:
    __version__ = _version(__name__)
except _PackageNotFoundError:
    # package is not installed
    __version__ = "<unknown>"

import atexit
import time
from subprocess import PIPE, Popen, call
from typing import Any, Optional

from ape import plugins
from ape_http.providers import EthereumProvider, NetworkConfig

HARDHAT_CHAIN_ID = 31337
HARDHAT_CONFIG = """
// See https://hardhat.org/config/ for config options.
module.exports = {
};
"""


class HardhatNetworkConfig(NetworkConfig):
    # --fork <URL, JSON-RPC server to fork from>
    fork_url: Optional[str] = None

    # --fork-block-number <INT, block number to fork from>
    fork_block_number: Optional[int] = None

    # --port <INT, default 8545>
    port: Optional[int] = None


class HardhatProvider(EthereumProvider):
    _process = None

    def __post_init__(self):
        hardhat_config_file = self.network.config_manager.PROJECT_FOLDER / "hardhat.config.js"

        # Write the hardhat config file in the Ape project dir if it doesn't exist yet
        if not hardhat_config_file.is_file():
            hardhat_config_file.write_text(HARDHAT_CONFIG)

        # Check if npx and hardhat are installed
        if call(["npx", "--version"], stderr=PIPE, stdout=PIPE, stdin=PIPE) != 0:
            raise RuntimeError("Missing npx binary. See ape-hardhat README for install steps.")
        if call(["npx", "hardhat", "--version"], stderr=PIPE, stdout=PIPE, stdin=PIPE) != 0:
            raise RuntimeError(
                "Missing hardhat NPM package. See ape-hardhat README for install steps."
            )

        if self._process:
            # Return early if the hardhat node process is already running
            return

        cmd = ["npx", "hardhat", "node"]
        if self.config.port:
            cmd.extend(["--port", str(self.config.port)])
        if self.config.fork_url:
            cmd.extend(["--fork", self.config.fork_url])
        if self.config.fork_block_number:
            cmd.extend(["--fork-block-number", str(self.config.fork_block_number)])

        # TODO Add a config to send stdout to logger?
        # Or redirect stdout to a file in plugin data dir?
        self._process = Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)

        # register atexit handler to make sure disconnect is called
        atexit.register(self.disconnect)

    def connect(self):
        """Verify that the hardhat process is up and accepting connections."""
        super().connect()
        retries = [0.1, 0.2, 0.3, 0.5, 1.0]  # seconds between retries
        connected = False
        for retry_time in retries:
            time.sleep(retry_time)
            try:
                # make a network call for chain_id and verify the result
                assert self.chain_id == HARDHAT_CHAIN_ID
                connected = True
                break
            except Exception as exc:
                print("Retrying hardhat connection:", exc)
        if not connected:
            if self._process.poll() is None:
                raise RuntimeError(
                    "Hardhat process is running, but could not connect to RPC server. "
                    "Run `npx hardhat node` to troubleshoot."
                )
            raise RuntimeError(
                "Hardhat command exited prematurely. Run `npx hardhat node` to troubleshoot."
            )
        elif self._process.poll() is not None:
            raise RuntimeError(
                "Hardhat process exited prematurely, but connection succeeded. "
                "Is something else listening on the port?"
            )

    def disconnect(self):
        super().disconnect()
        if self._process and self._process.poll() is None:
            # process is still running, kill it
            self._process.kill()
        self._process = None

    def _make_request(self, rpc: str, args: list) -> Any:
        return self._web3.manager.request_blocking(rpc, args)  # type: ignore

    def set_block_gas_limit(self, gas_limit: int) -> bool:
        return self._make_request("evm_setBlockGasLimit", [hex(gas_limit)])

    def sleep(self, seconds: int) -> int:
        return int(self._make_request("evm_increaseTime", [seconds]))

    def mine(self, timestamp: Optional[int] = None) -> str:
        return self._make_request("evm_mine", [timestamp] if timestamp else [])

    def snapshot(self) -> int:
        return self._make_request("evm_snapshot", [])

    def revert(self, snapshot_id: int) -> bool:
        return self._make_request("evm_revert", [snapshot_id])

    def unlock_account(self, address: str) -> bool:
        return self._make_request("hardhat_impersonateAccount", [address])


@plugins.register(plugins.Config)
def config_class():
    return HardhatNetworkConfig


@plugins.register(plugins.ProviderPlugin)
def providers():
    yield "ethereum", "development", HardhatProvider
