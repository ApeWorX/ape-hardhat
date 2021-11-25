import ctypes
import platform
import shutil
import signal
import time
from pathlib import Path
from subprocess import PIPE, Popen, call
from typing import Callable, Optional
from urllib.request import urlopen

from ape.logging import logger

from .exceptions import HardhatSubprocessError, HardhatTimeoutError, RPCTimeoutError

HARDHAT_CHAIN_ID = 31337
PROCESS_WAIT_TIMEOUT = 15  # seconds to wait for process to terminate
HARDHAT_CONFIG = """
// See https://hardhat.org/config/ for config options.
module.exports = {{
  networks: {{
    hardhat: {{
      hardfork: "london",
      // base fee of 0 allows use of 0 gas price when testing
      initialBaseFeePerGas: 0,
      accounts: {{
        mnemonic: "{mnemonic}",
        path: "m/44'/60'/0'",
        count: {number_of_accounts},
      }}
    }},
  }},
}};
"""


class HardhatConfig:
    """
    A class representing the actual 'hardhat.config.js' file.
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


class HardhatProcess:
    """
    A wrapper class around the Hardhat node process.
    """

    def __init__(
        self,
        base_path: Path,
        port: int,
        mnemonic: str,
        number_of_accounts: int,
        hard_fork: Optional[str] = None,
        fork_url: Optional[str] = None,
        fork_block_number: Optional[int] = None,
    ):
        self._port = port
        self._fork_url = fork_url
        self._fork_block_number = fork_block_number
        self._npx_bin = shutil.which("npx")
        self._process = None
        self._config_file = HardhatConfig(
            base_path, mnemonic, number_of_accounts, hard_fork=hard_fork
        )
        self._config_file.write_if_not_exists()

        if not self._npx_bin:
            raise HardhatSubprocessError(
                "Could not locate NPM executable. See ape-hardhat README for install steps."
            )
        elif _call(self._npx_bin, "--version") != 0:
            raise HardhatSubprocessError(
                "NPM executable returned error code. See ape-hardhat README for install steps."
            )
        elif _call(self._npx_bin, "hardhat", "--version") != 0:
            raise HardhatSubprocessError(
                "Missing hardhat NPM package. See ape-hardhat README for install steps."
            )

    @property
    def started(self) -> bool:
        return self._process is not None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is not None

    @property
    def is_rpc_ready(self):
        try:
            urlopen(f"http://127.0.0.1:{self._port}")
        except Exception:
            return False
        else:
            return True

    def start(self, timeout=20):
        """Start the hardhat process and wait for it to respond over the network."""

        # TODO: Add configs to send stdout to logger / redirect to a file in plugin data dir?
        cmd = [
            self._npx_bin,
            "hardhat",
            "node",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(self._port),
        ]

        if self._fork_url is not None:
            cmd.extend(("--fork", self._fork_url))

        if self._fork_block_number is not None:
            cmd.extend(("--fork-block-number", self._fork_block_number))

        if self.is_rpc_ready:
            logger.info(f"Connecting to existing Hardhat node at port '{self._port}'.")
            process = None  # Not managing the process.
        else:
            logger.info(f"Started Hardhat node at port '{self._port}'.")

            pre_exec_fn = _linux_set_death_signal if platform.uname().system == "Linux" else None
            process = _popen(*cmd, preexec_fn=pre_exec_fn)  # Starts hardhat if it not running.

            if process is None:
                raise HardhatSubprocessError(
                    "Failed to start hardhat. Use 'npx hardhat node' to debug."
                )

            with RPCTimeoutError(seconds=timeout) as _timeout:
                while True:
                    if self.is_rpc_ready:
                        break

                    time.sleep(0.1)
                    _timeout.check()

        self._process = process

    def stop(self):
        """Helper function for killing a process and its child subprocesses."""
        if not self._process:
            return

        logger.info("Stopping Hardhat node.")
        _kill_process(self._process)
        self._process = None


def _popen(*cmd, preexec_fn: Optional[Callable] = None):
    return Popen([*cmd], stdin=PIPE, stdout=PIPE, stderr=PIPE, preexec_fn=preexec_fn)


def _call(*args):
    return call([*args], stderr=PIPE, stdout=PIPE, stdin=PIPE)


def _wait_for_popen(proc, timeout=30):
    try:
        with HardhatTimeoutError(seconds=timeout) as _timeout:
            while proc.poll() is None:
                time.sleep(0.1)
                _timeout.check()
    except HardhatTimeoutError:
        pass


def _kill_process(proc):
    if platform.uname().system == "Windows":
        _windows_taskkill(proc.pid)
        return

    warn_prefix = "Trying to close Hardhat node process."

    def _try_close(warn_message):
        try:
            proc.send_signal(signal.SIGINT)
            _wait_for_popen(proc, PROCESS_WAIT_TIMEOUT)
        except KeyboardInterrupt:
            logger.warning(warn_message)

    try:
        if proc.poll() is None:
            _try_close(f"{warn_prefix}. Press Ctrl+C 1 more times to force quit")

        if proc.poll() is None:
            proc.kill()
            _wait_for_popen(proc, 2)

    except KeyboardInterrupt:
        proc.kill()


def _windows_taskkill(pid: int) -> None:
    """
    Kills the given process and all child processes using taskkill.exe. Used
    for subprocesses started up on Windows which run in a cmd.exe wrapper that
    doesn't propagate signals by default (leaving orphaned processes).
    """
    taskkill_bin = shutil.which("taskkill")
    if not taskkill_bin:
        raise HardhatSubprocessError("Could not find taskkill.exe executable.")

    proc = _popen(
        taskkill_bin,
        "/F",  # forcefully terminate
        "/T",  # terminate child processes
        "/PID",
        str(pid),
    )
    proc.wait(timeout=PROCESS_WAIT_TIMEOUT)


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
