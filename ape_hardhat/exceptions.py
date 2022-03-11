from ape.exceptions import ProviderError, SubprocessError


class HardhatProviderError(ProviderError):
    """
    An error related to the Hardhat network provider plugin.
    """


class HardhatSubprocessError(HardhatProviderError, SubprocessError):
    """
    An error related to launching subprocesses to run Hardhat.
    """


class HardhatNotInstalledError(HardhatSubprocessError):
    """
    Raised when Hardhat is not installed.
    """

    def __init__(self):
        super().__init__(
            "Missing local Hardhat NPM package. "
            "See ape-hardhat README for install steps. "
            "Note: global installation of Hardhat will not work and "
            "you must be in your project's directory."
        )
