"""
Ape network provider plugin for Hardhat (Ethereum development framework and network
implementation written in Node.js).
"""

from ape import plugins
from ape.api.networks import LOCAL_NETWORK_NAME

from .providers import (
    HardhatMainnetForkProvider,
    HardhatNetworkConfig,
    HardhatProvider,
    HardhatProviderError,
    HardhatSubprocessError,
)


@plugins.register(plugins.Config)
def config_class():
    return HardhatNetworkConfig


@plugins.register(plugins.ProviderPlugin)
def providers():
    yield "ethereum", LOCAL_NETWORK_NAME, HardhatProvider
    yield "ethereum", "mainnet-fork", HardhatMainnetForkProvider
    yield "fantom", LOCAL_NETWORK_NAME, HardhatProvider
    yield "fantom", "opera-fork", HardhatMainnetForkProvider


__all__ = [
    "HardhatNetworkConfig",
    "HardhatProvider",
    "HardhatProviderError",
    "HardhatSubprocessError",
]
