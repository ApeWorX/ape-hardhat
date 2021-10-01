"""
Implementation for HardhatProvider.
"""

import atexit
import ctypes
import platform
import random
import signal
import sys
import time
from subprocess import PIPE, Popen, call
from typing import Any, List, Optional

from ape.exceptions import ProviderError
from ape_http.providers import DEFAULT_SETTINGS, EthereumProvider, NetworkConfig

EPHEMERAL_PORTS_START = 49152
EPHEMERAL_PORTS_END = 60999
HARDHAT_CHAIN_ID = 31337
HARDHAT_CONFIG = """
// See https://hardhat.org/config/ for config options.
module.exports = {
  networks: {
    hardhat: {
      hardfork: "london",
      // base fee of 0 allows use of 0 gas price when testing
      initialBaseFeePerGas: 0,
    },
  },
};
"""
HARDHAT_START_NETWORK_RETRIES = [0.1, 0.2, 0.3, 0.5, 1.0]  # seconds between network retries
HARDHAT_START_PROCESS_ATTEMPTS = 3  # number of attempts to start subprocess before giving up


def _signal_handler(signum, frame):
    """Runs on SIGTERM and SIGINT to force ``atexit`` handlers to run."""
    atexit._run_exitfuncs()
    sys.exit(143 if signum == signal.SIGTERM else 130)


def _set_death_signal():
    """Automatically sends SIGTERM to child subprocesses when parent process dies."""
    if platform.uname().system == "Linux":
        # from: https://stackoverflow.com/a/43152455/75956
        # the first argument "1" is PR_SET_PDEATHSIG
        # the second argument is what signal to send the child
        libc = ctypes.CDLL("libc.so.6")
        return libc.prctl(1, signal.SIGTERM)


class HardhatSubprocessError(ProviderError):
    pass


class HardhatNetworkConfig(NetworkConfig):
    # --port <INT, default from Hardhat is 8545, but our default is to assign a random port number>
    port: Optional[int] = None

    # retry strategy configs, try increasing these if you're getting HardhatSubprocessError
    network_retries: List[float] = HARDHAT_START_NETWORK_RETRIES
    process_attempts: int = HARDHAT_START_PROCESS_ATTEMPTS


class HardhatProvider(EthereumProvider):
    def __post_init__(self):
        self._hardhat_web3 = (
            None  # we need to maintain a separate per-instance web3 client for Hardhat
        )
        self.port = None
        self.process = None

        hardhat_config_file = self.network.config_manager.PROJECT_FOLDER / "hardhat.config.js"

        if not hardhat_config_file.is_file():
            hardhat_config_file.write_text(HARDHAT_CONFIG)

        if call(["npx", "--version"], stderr=PIPE, stdout=PIPE, stdin=PIPE) != 0:
            raise HardhatSubprocessError(
                "Missing npx binary. See ape-hardhat README for install steps."
            )
        if call(["npx", "hardhat", "--version"], stderr=PIPE, stdout=PIPE, stdin=PIPE) != 0:
            raise HardhatSubprocessError(
                "Missing hardhat NPM package. See ape-hardhat README for install steps."
            )

        # register atexit handler to make sure disconnect is called for normal object lifecycle
        atexit.register(self.disconnect)

        # register signal handlers to make sure atexit handlers are called when the parent python
        # process is killed
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    def _start_process(self):
        """Start the hardhat process and wait for it to respond over the network."""
        # handle configs
        cmd = ["npx", "hardhat", "node"]

        # pick a random port if one isn't configured
        self.port = self.config.port
        if not self.port:
            self.port = random.randint(EPHEMERAL_PORTS_START, EPHEMERAL_PORTS_END)
        cmd.extend(["--port", str(self.port)])

        # TODO: Add configs to send stdout to logger / redirect to a file in plugin data dir?
        process = Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, preexec_fn=_set_death_signal)
        connected = False
        for retry_time in self.config.network_retries:
            time.sleep(retry_time)
            super().connect()
            connected = self._verify_connection()
            if connected:
                break
        if not connected:
            if process.poll() is None:
                raise HardhatSubprocessError(
                    "Hardhat process is running, but could not connect to RPC server. "
                    "Run `npx hardhat node` or adjust retry strategy configs to troubleshoot."
                )
            raise HardhatSubprocessError(
                "Hardhat command exited prematurely. Run `npx hardhat node` to troubleshoot or "
                "adjust retry strategy configs to troubleshoot."
            )
        elif process.poll() is not None:
            raise HardhatSubprocessError(
                "Hardhat process exited prematurely, but connection succeeded. "
                "Is something else listening on the port?"
            )
        self.process = process

    def _verify_connection(self):
        """Make a network call for chain_id and verify the result."""
        try:
            chain_id = self.chain_id
            if chain_id != HARDHAT_CHAIN_ID:
                raise AssertionError(f"Unexpected chain ID: {chain_id}")
            return True
        except Exception as exc:
            print("Hardhat connection failed:", exc)
        return False

    def connect(self):
        """Start the hardhat process and verify it's up and accepting connections."""

        if self.process:
            raise RuntimeError("Cannot connect twice. Call disconnect before connecting again.")

        if self.config.port:
            # if a port is configured, only make one start up attempt
            self._start_process()
        else:
            for _ in range(self.config.process_attempts):
                try:
                    self._start_process()
                    break
                except HardhatSubprocessError as exc:
                    print("Retrying hardhat subprocess startup:", exc)

        # subprocess should be running and receiving network requests at this point
        if not (self.process and self.process.poll() is None and self.port):
            raise HardhatSubprocessError(
                "Could not start hardhat subprocess on a random port. "
                "See logs or run `npx hardhat node` or adjust retry strategy configs to "
                "troubleshoot."
            )

    @property
    def uri(self) -> str:
        uri = getattr(self.config, self.network.ecosystem.name)[self.network.name]["uri"]
        if uri != DEFAULT_SETTINGS["uri"]:
            # the user configured their own URI in the project configs, let's use that
            return uri
        elif self.port:
            # the user did not override the default URI, and we have a port
            # number, so let's build the URI using that port number
            return f"http://localhost:{self.port}"
        else:
            raise RuntimeError("Can't build URI before `connect` is called.")

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

    def disconnect(self):
        super().disconnect()
        if self.process:
            self.process.kill()
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

    def snapshot(self) -> int:
        return self._make_request("evm_snapshot", [])

    def revert(self, snapshot_id: int) -> bool:
        return self._make_request("evm_revert", [snapshot_id])

    def unlock_account(self, address: str) -> bool:
        return self._make_request("hardhat_impersonateAccount", [address])
