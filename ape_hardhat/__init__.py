"""
Ape network provider plugin for Hardhat (Ethereum development framework and network
implementation written in Node.js).
"""

from ape import plugins
from ape.api.networks import LOCAL_NETWORK_NAME
from ape_ethereum.ecosystem import NETWORKS

from .exceptions import HardhatProviderError, HardhatSubprocessError
from .provider import HardhatNetworkConfig, HardhatProvider, HardhatProviderForkProvider


@plugins.register(plugins.Config)
def config_class():
    return HardhatNetworkConfig


@plugins.register(plugins.ProviderPlugin)
def providers():
    yield "ethereum", LOCAL_NETWORK_NAME, HardhatProvider

    for network in NETWORKS:
        yield "ethereum", f"{network}-fork", HardhatProviderForkProvider

    yield "fantom", LOCAL_NETWORK_NAME, HardhatProvider
    yield "fantom", "opera-fork", HardhatProviderForkProvider
    yield "fantom", "testnet-fork", HardhatProviderForkProvider

    yield "arbitrum", LOCAL_NETWORK_NAME, HardhatProvider
    yield "arbitrum", "mainnet-fork", HardhatProviderForkProvider
    yield "arbitrum", "goerli-fork", HardhatProviderForkProvider

    yield "polygon", LOCAL_NETWORK_NAME, HardhatProvider
    yield "polygon", "mainnet-fork", HardhatProviderForkProvider
    yield "polygon", "mumbai-fork", HardhatProviderForkProvider


__all__ = [
    "HardhatNetworkConfig",
    "HardhatProvider",
    "HardhatProviderError",
    "HardhatSubprocessError",
]
