"""
Implementation for HardhatProvider.
"""

import atexit
import ctypes
import platform
import random
import shutil
import signal
import sys
import time
from subprocess import PIPE, Popen, call
from typing import Any, List, Optional

from ape.api import ReceiptAPI, TestProviderAPI, TransactionAPI, Web3Provider
from ape.api.config import ConfigItem
from ape.exceptions import (
    ContractLogicError,
    OutOfGasError,
    ProviderError,
    TransactionError,
    VirtualMachineError,
)
from ape.logging import logger
from ape.utils import gas_estimation_error_message
from web3 import HTTPProvider, Web3
from web3.gas_strategies.rpc import rpc_gas_price_strategy

EPHEMERAL_PORTS_START = 49152
EPHEMERAL_PORTS_END = 60999
HARDHAT_CHAIN_ID = 31337
HARDHAT_START_NETWORK_RETRIES = [0.1, 0.2, 0.3, 0.5, 1.0]  # seconds between network retries
HARDHAT_START_PROCESS_ATTEMPTS = 3  # number of attempts to start subprocess before giving up
PROCESS_WAIT_TIMEOUT = 15  # seconds to wait for process to terminate
DEFAULT_SETTINGS = {"uri": "http://localhost:8545"}
HARDHAT_CONFIG = """
// See https://hardhat.org/config/ for config options.
module.exports = {{
  networks: {{
    hardhat: {{
      hardfork: "london",
      // base fee of 0 allows use of 0 gas price when testing
      initialBaseFeePerGas: 0,
      accounts: {{
        mnemonic: "{0}",
        path: "m/44'/60'/0'"
      }}
    }},
  }},
}};
"""


def _signal_handler(signum, frame):
    """Runs on SIGTERM and SIGINT to force ``atexit`` handlers to run."""
    atexit._run_exitfuncs()
    sys.exit(143 if signum == signal.SIGTERM else 130)


def _linux_set_death_signal():
    """
    Automatically sends SIGTERM to child subprocesses when parent process
    dies (only usable on Linux).
    """
    # from: https://stackoverflow.com/a/43152455/75956
    # the first argument, 1, is the flag for PR_SET_PDEATHSIG
    # the second argument is what signal to send to child subprocesses
    libc = ctypes.CDLL("libc.so.6")
    return libc.prctl(1, signal.SIGTERM)


def _get_preexec_fn():
    if platform.uname().system == "Linux":
        return _linux_set_death_signal
    else:
        return None


def _windows_taskkill(pid: int) -> None:
    """
    Kills the given process and all child processes using taskkill.exe. Used
    for subprocesses started up on Windows which run in a cmd.exe wrapper that
    doesn't propagate signals by default (leaving orphaned processes).
    """
    taskkill_bin = shutil.which("taskkill")
    if not taskkill_bin:
        raise HardhatSubprocessError("Could not find taskkill.exe executable.")
    proc = Popen(
        [
            taskkill_bin,
            "/F",  # forcefully terminate
            "/T",  # terminate child processes
            "/PID",
            str(pid),
        ],
        stderr=PIPE,
        stdout=PIPE,
        stdin=PIPE,
    )
    proc.wait(timeout=PROCESS_WAIT_TIMEOUT)


def _kill_process(proc):
    """Helper function for killing a process and its child subprocesses."""
    if platform.uname().system == "Windows":
        _windows_taskkill(proc.pid)
    proc.kill()
    proc.wait(timeout=PROCESS_WAIT_TIMEOUT)


class HardhatProviderError(ProviderError):
    """An error related to the Hardhat network provider plugin."""


class HardhatSubprocessError(HardhatProviderError):
    """An error related to launching subprocesses to run Hardhat."""


class HardhatNetworkConfig(ConfigItem):
    # --port <INT, default from Hardhat is 8545, but our default is to assign a random port number>
    port: Optional[int] = None

    # retry strategy configs, try increasing these if you're getting HardhatSubprocessError
    network_retries: List[float] = HARDHAT_START_NETWORK_RETRIES
    process_attempts: int = HARDHAT_START_PROCESS_ATTEMPTS


class HardhatProvider(Web3Provider, TestProviderAPI):
    def __post_init__(self):
        self._hardhat_web3 = (
            None  # we need to maintain a separate per-instance web3 client for Hardhat
        )
        self.port = None
        self.process = None
        self.npx_bin = shutil.which("npx")

        hardhat_config_file = self.network.config_manager.PROJECT_FOLDER / "hardhat.config.js"
        if not hardhat_config_file.is_file():
            try:
                mnemonic = self.network.config_manager.get_config("test")["mnemonic"]
            except KeyError:
                # For some reason, test plugins don't show up in child processes
                from ape_test import Config as TestConfig

                mnemonic = TestConfig.__defaults__["mnemonic"]

            hardhat_config = HARDHAT_CONFIG.format(mnemonic)
            hardhat_config_file.write_text(hardhat_config)

        if not self.npx_bin:
            raise HardhatSubprocessError(
                "Could not locate NPM executable. See ape-hardhat README for install steps."
            )
        if call([self.npx_bin, "--version"], stderr=PIPE, stdout=PIPE, stdin=PIPE) != 0:
            raise HardhatSubprocessError(
                "NPM executable returned error code. See ape-hardhat README for install steps."
            )
        if call([self.npx_bin, "hardhat", "--version"], stderr=PIPE, stdout=PIPE, stdin=PIPE) != 0:
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
        cmd = [self.npx_bin, "hardhat", "node"]

        # pick a random port if one isn't configured
        self.port = self.config.port
        if not self.port:
            self.port = random.randint(EPHEMERAL_PORTS_START, EPHEMERAL_PORTS_END)
        cmd.extend(["--port", str(self.port)])

        # TODO: Add configs to send stdout to logger / redirect to a file in plugin data dir?
        process = Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, preexec_fn=_get_preexec_fn())
        connected = False
        for retry_time in self.config.network_retries:
            time.sleep(retry_time)
            self._web3 = Web3(HTTPProvider(self.uri))
            self._web3.eth.set_gas_price_strategy(rpc_gas_price_strategy)
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

    def _verify_connection(self) -> bool:
        """Make a network call for chain_id and verify the result."""
        try:
            chain_id = self.chain_id
            if chain_id != HARDHAT_CHAIN_ID:
                raise HardhatProviderError(f"Unexpected chain ID: {chain_id}")
            return True
        except Exception as exc:
            logger.debug("Hardhat connection failed: %r", exc)
        return False

    def connect(self):
        """Start the hardhat process and verify it's up and accepting connections."""

        if self.process:
            raise HardhatProviderError(
                "Cannot connect twice. Call disconnect before connecting again."
            )

        if self.config.port:
            # if a port is configured, only make one start up attempt
            self._start_process()
        else:
            for _ in range(self.config.process_attempts):
                try:
                    self._start_process()
                    break
                except HardhatSubprocessError as exc:
                    logger.info("Retrying hardhat subprocess startup: %r", exc)

        # subprocess should be running and receiving network requests at this point
        if not (self.process and self.process.poll() is None and self.port):
            raise HardhatSubprocessError(
                "Could not start hardhat subprocess on a random port. "
                "See logs or run `npx hardhat node` or adjust retry strategy configs to "
                "troubleshoot."
            )

    @property
    def uri(self) -> str:
        if self.port:
            # the user did not override the default URI, and we have a port
            # number, so let's build the URI using that port number
            return f"http://localhost:{self.port}"
        else:
            raise HardhatProviderError("Can't build URI before `connect` is called.")

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
            _kill_process(self.process)
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
        return self._make_request("evm_snapshot", [])

    def revert(self, snapshot_id: str):
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


def _get_vm_error(web3_value_error: ValueError) -> TransactionError:
    if not len(web3_value_error.args):
        return VirtualMachineError(web3_value_error)

    err_data = web3_value_error.args[0]
    if not isinstance(err_data, dict):
        return VirtualMachineError(web3_value_error)

    message = str(err_data.get("message"))
    if not message:
        return VirtualMachineError(web3_value_error)

    # Handle `ContactLogicError` similary to other providers in `ape`.
    # by stripping off the unnecessary prefix that hardhat has on reverts.
    hardhat_prefix = (
        "Error: VM Exception while processing transaction: reverted with reason string "
    )
    if message.startswith(hardhat_prefix):
        message = message.replace(hardhat_prefix, "").strip("'")
        raise ContractLogicError(message)

    elif message == "Transaction ran out of gas":
        raise OutOfGasError()

    return VirtualMachineError(message=message)
