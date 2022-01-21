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

from .exceptions import (
    HardhatNotInstalledError,
    HardhatSubprocessError,
    HardhatTimeoutError,
    NonLocalHardhatError,
    RPCTimeoutError,
)

HARDHAT_CHAIN_ID = 31337
PROCESS_WAIT_TIMEOUT = 15  # seconds to wait for process to terminate
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
        ).lstrip()

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

        hardhat_install_path_str = shutil.which("hardhat")
        if hardhat_install_path_str:
            hardhat_install_path = Path(hardhat_install_path_str)
            expected_install_path = base_path / "node_modules" / ".bin" / "hardhat"
            if hardhat_install_path != expected_install_path:
                # If we get here, we know that `hardhat` is at least installed
                # and therefore, 'actual_install_path' is not None.
                raise NonLocalHardhatError(hardhat_install_path, expected_install_path)

        if not self._npx_bin:
            raise HardhatSubprocessError(
                "Could not locate NPM executable. See ape-hardhat README for install steps."
            )
        elif _call(self._npx_bin, "--version") != 0:
            raise HardhatSubprocessError(
                "NPM executable returned error code. See ape-hardhat README for install steps."
            )
        elif _call(self._npx_bin, "hardhat", "--version") != 0:
            raise HardhatNotInstalledError()

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

    def start(self, timeout: int = 20):
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
            cmd.extend(("--fork-block-number", str(self._fork_block_number)))

        if self.is_rpc_ready:
            logger.info(f"Connecting to existing Hardhat node at port '{self._port}'.")
            process = None  # Not managing the process.
        else:
            logger.info(f"Started Hardhat node at port '{self._port}'.")

            pre_exec_fn = _linux_set_death_signal if platform.uname().system == "Linux" else None
            process = _popen(*cmd, preexec_fn=pre_exec_fn)  # Starts Hardhat if it not running.

            with RPCTimeoutError(self, seconds=timeout) as _timeout:
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
        self._kill_process()

    def _kill_process(self):
        if platform.uname().system == "Windows":
            _windows_taskkill(self._process.pid)
            return

        warn_prefix = "Trying to close Hardhat node process."

        def _try_close(warn_message):
            try:
                self._process.send_signal(signal.SIGINT)
                self._wait_for_popen(PROCESS_WAIT_TIMEOUT)
            except KeyboardInterrupt:
                logger.warning(warn_message)

        try:
            if self._process.poll() is None:
                _try_close(f"{warn_prefix}. Press Ctrl+C 1 more times to force quit")

            if self._process.poll() is None:
                self._process.kill()
                self._wait_for_popen(2)

        except KeyboardInterrupt:
            self._process.kill()

        self._process = None

    def _wait_for_popen(self, timeout: int = 30):
        if not self._process:
            # Mostly just to make mypy happy.
            raise HardhatSubprocessError("Unable to wait for process. It is not set yet.")

        try:
            with HardhatTimeoutError(self, seconds=timeout) as _timeout:
                while self._process.poll() is None:
                    time.sleep(0.1)
                    _timeout.check()
        except HardhatTimeoutError:
            pass


def _popen(*cmd, preexec_fn: Optional[Callable] = None):
    return Popen([*cmd], stdin=PIPE, stdout=PIPE, stderr=PIPE, preexec_fn=preexec_fn)


def _call(*args):
    return call([*args], stderr=PIPE, stdout=PIPE, stdin=PIPE)


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
