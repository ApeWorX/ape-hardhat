"""
Ape network provider plugin for Hardhat (Ethereum development framework and network
implementation written in Node.js).
"""

from ape import plugins


@plugins.register(plugins.Config)
def config_class():
    from .provider import HardhatNetworkConfig

    return HardhatNetworkConfig


@plugins.register(plugins.ProviderPlugin)
def providers():
    from ape.api.networks import LOCAL_NETWORK_NAME
    from ape_ethereum.ecosystem import NETWORKS

    from .provider import HardhatForkProvider, HardhatProvider

    yield "ethereum", LOCAL_NETWORK_NAME, HardhatProvider

    for network in NETWORKS:
        yield "ethereum", f"{network}-fork", HardhatForkProvider

    yield "arbitrum", LOCAL_NETWORK_NAME, HardhatProvider
    yield "arbitrum", "mainnet-fork", HardhatForkProvider
    yield "arbitrum", "sepolia-fork", HardhatForkProvider

    yield "avalanche", LOCAL_NETWORK_NAME, HardhatProvider
    yield "avalanche", "mainnet-fork", HardhatForkProvider
    yield "avalanche", "fuji-fork", HardhatForkProvider

    yield "bsc", LOCAL_NETWORK_NAME, HardhatProvider
    yield "bsc", "mainnet-fork", HardhatForkProvider
    yield "bsc", "testnet-fork", HardhatForkProvider

    yield "fantom", LOCAL_NETWORK_NAME, HardhatProvider
    yield "fantom", "opera-fork", HardhatForkProvider
    yield "fantom", "testnet-fork", HardhatForkProvider

    yield "optimism", LOCAL_NETWORK_NAME, HardhatProvider
    yield "optimism", "mainnet-fork", HardhatForkProvider
    yield "optimism", "sepolia-fork", HardhatForkProvider

    yield "base", LOCAL_NETWORK_NAME, HardhatProvider
    yield "base", "mainnet-fork", HardhatForkProvider
    yield "base", "sepolia-fork", HardhatForkProvider

    yield "polygon", LOCAL_NETWORK_NAME, HardhatProvider
    yield "polygon", "mainnet-fork", HardhatForkProvider
    yield "polygon", "mumbai-fork", HardhatForkProvider
    yield "polygon", "amoy-fork", HardhatForkProvider

    yield "gnosis", LOCAL_NETWORK_NAME, HardhatProvider
    yield "gnosis", "mainnet-fork", HardhatForkProvider
    yield "gnosis", "chaido-fork", HardhatForkProvider


def __getattr__(name: str):
    if name.endswith("Error"):
        import ape_hardhat.exceptions as err_module

        return getattr(err_module, name)

    import ape_hardhat.provider as module

    return getattr(module, name)


__all__ = [
    "HardhatNetworkConfig",
    "HardhatProvider",
    "HardhatProviderError",
    "HardhatSubprocessError",
]
