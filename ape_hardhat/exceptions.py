import time
from typing import Optional, TYPE_CHECKING

from ape.exceptions import ProviderError

if TYPE_CHECKING:
    from ape_hardhat.process import HardhatProcess


class HardhatProviderError(ProviderError):
    """
    An error related to the Hardhat network provider plugin.
    """


class HardhatSubprocessError(HardhatProviderError):
    """
    An error related to launching subprocesses to run Hardhat.
    """


class NonLocalHardhatError(HardhatSubprocessError):
    """
    An error raised when trying to use a non-local installation of Hardhat.
    """

    def __init__(self, install_path: Path, expected_path: Path):
        super().__init__(
            "Please use and run Hardhat from your 'ape' project directory. "
            "A non-local installation of Hardhat is not allowed. "
            f"(Actual Installation Path={install_path}, Expected Installation Path={expected_path})"
        )


class HardhatTimeoutError(HardhatSubprocessError):
    """
    A context-manager exception that raises if its operations exceed
    the given timeout seconds.

    This implementation was inspired from py-geth.
    """

    def __init__(
        self,
        process: "HardhatProcess",
        message: Optional[str] = None,
        seconds: Optional[int] = None,
        exception: Optional[Exception] = None,
        *args,
        **kwargs,
    ):
        self._process = process
        self._message = message or "Timed out waiting for process."
        self._seconds = seconds
        self._exception = exception
        self._start_time = None
        self._is_running = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def __str__(self):
        if self._seconds in [None, ""]:
            return ""

        return self._message

    @property
    def expire_at(self) -> float:
        if self._seconds is None:
            raise self._error(
                ValueError("Timeouts with 'seconds == None' do not have an expiration time.")
            )
        elif self._start_time is None:
            raise self._error(ValueError("Timeout has not been started."))

        return self._start_time + self._seconds

    def start(self):
        if self._is_running is not None:
            raise self._error(ValueError("Timeout has already been started."))

        self._start_time = time.time()
        self._is_running = True

    def check(self):
        if self._is_running is None:
            raise self._error(ValueError("Timeout has not been started."))

        elif self._is_running is False:
            raise self._error(ValueError("Timeout has already been cancelled."))

        elif self._seconds is None:
            return

        elif time.time() > self.expire_at:
            self._is_running = False

            if isinstance(self._exception, type):
                raise self._error(self._exception(str(self)))

            elif isinstance(self._exception, Exception):
                raise self._error(self._exception)

            raise self

    def cancel(self):
        self._is_running = False

    def _error(self, err: Exception) -> Exception:
        self._process.stop()
        return err


class RPCTimeoutError(HardhatTimeoutError):
    def __init__(
        self,
        process: "HardhatProcess",
        seconds: Optional[int] = None,
        exception: Optional[Exception] = None,
        *args,
        **kwargs,
    ):
        error_message = (
            "Timed out waiting for successful RPC connection to "
            f"the Hardhat node ({seconds} seconds) "
            "Try 'npx hardhat node' to debug."
        )
        super().__init__(
            process, message=error_message, seconds=seconds, exception=exception, *args, **kwargs
        )
