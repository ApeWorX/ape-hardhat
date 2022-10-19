"""
Ape network provider plugin for Hardhat (Ethereum development framework and network
implementation written in Node.js).
"""

from ape import plugins
from ape.api.networks import LOCAL_NETWORK_NAME
from ape_ethereum.ecosystem import NETWORKS

from .provider import (
    HardhatForkProvider,
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

    for network in NETWORKS:
        yield "ethereum", f"{network}-fork", HardhatForkProvider

    yield "fantom", LOCAL_NETWORK_NAME, HardhatProvider
    yield "fantom", "opera-fork", HardhatForkProvider

    yield "arbitrum", LOCAL_NETWORK_NAME, HardhatProvider
    yield "arbitrum", "mainnet-fork", HardhatForkProvider


__all__ = [
    "HardhatNetworkConfig",
    "HardhatProvider",
    "HardhatProviderError",
    "HardhatSubprocessError",
]
