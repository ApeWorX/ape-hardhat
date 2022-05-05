from pathlib import Path

import ape
import pytest
from ape_ethereum.ecosystem import NETWORKS

from ape_hardhat.providers import HardhatForkProvider

TESTS_DIRECTORY = Path(__file__).parent
TEST_ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"


@pytest.fixture(scope="module")
def config():
    return ape.config


@pytest.fixture(autouse=True, scope="module")
def in_tests_dir(config):
    with config.using_project(TESTS_DIRECTORY):
        yield


@pytest.fixture
def mainnet_fork_provider(networks):
    network_api = networks.ecosystems["ethereum"]["mainnet-fork"]
    provider = HardhatForkProvider(
        name="hardhat",
        network=network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )
    provider.port = 9001
    provider.connect()
    yield provider
    provider.disconnect()


def create_fork_provider(network_api, port):
    provider = HardhatForkProvider(
        name="hardhat",
        network=network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )
    provider.port = port
    return provider


@pytest.mark.parametrize("network", [k for k in NETWORKS.keys()])
def test_fork_config(config, network):
    plugin_config = config.get_config("hardhat")
    network_config = plugin_config["fork"].get("ethereum", {}).get(network, {})
    assert network_config["upstream_provider"] == "alchemy", "config not registered"


@pytest.mark.parametrize("upstream,port", [("mainnet", 9000), ("rinkeby", 9001)])
def test_impersonate(networks, test_accounts, upstream, port):
    network_api = networks.ecosystems["ethereum"][f"{upstream}-fork"]
    provider = create_fork_provider(network_api, port)
    provider.connect()
    orig_provider = networks.active_provider
    networks.active_provider = provider

    impersonated_account = test_accounts[TEST_ADDRESS]
    other_account = test_accounts[0]
    receipt = impersonated_account.transfer(other_account, "1 wei")
    assert receipt.receiver == other_account
    assert receipt.sender == impersonated_account

    provider.disconnect()
    networks.active_provider = orig_provider
