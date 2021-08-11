"""
Ape network provider plugin for Hardhat (Ethereum development framework and network
implementation written in Node.js).
"""

from ape import plugins

from .providers import HardhatNetworkConfig, HardhatProvider


@plugins.register(plugins.Config)
def config_class():
    return HardhatNetworkConfig


@plugins.register(plugins.ProviderPlugin)
def providers():
    yield "ethereum", "development", HardhatProvider
