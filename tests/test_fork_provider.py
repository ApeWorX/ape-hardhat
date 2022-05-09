from pathlib import Path

import ape
import pytest

from ape_hardhat.providers import HardhatMainnetForkProvider

TESTS_DIRECTORY = Path(__file__).parent


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
    provider = HardhatMainnetForkProvider(
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
    provider = HardhatMainnetForkProvider(
        name="hardhat",
        network=network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )
    provider.port = port
    return provider


def test_reset_fork(networks, mainnet_fork_provider):
    mainnet_fork_provider.mine()
    prev_block_num = mainnet_fork_provider.get_block("latest").number
    mainnet_fork_provider.reset_fork()
    block_num_after_reset = mainnet_fork_provider.get_block("latest").number
    assert block_num_after_reset < prev_block_num


def test_request_timeout(mainnet_fork_provider):
    actual = mainnet_fork_provider.web3.provider._request_kwargs["timeout"]  # type: ignore
    expected = 360  # Value set in `ape-config.yaml`
    assert actual == expected
