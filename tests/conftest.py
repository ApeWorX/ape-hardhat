from pathlib import Path

import pytest  # type: ignore
from ape import Project, networks
from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from ape.managers.project import ProjectManager

from ape_hardhat import HardhatNetworkConfig, HardhatProvider


def get_project() -> ProjectManager:
    return Project(Path(__file__).parent)


def get_network_config() -> HardhatNetworkConfig:
    config = HardhatNetworkConfig()

    # bump up the timeouts to decrease chance of tests flaking due to race conditions
    config.network_retries = [0.1, 0.2, 0.3, 0.5, 0.5, 1, 1, 1, 1, 1, 1, 5]
    config.process_attempts = 10

    return config


@pytest.fixture
def project():
    return get_project()


@pytest.fixture
def network_api():
    return networks.ecosystems["ethereum"][LOCAL_NETWORK_NAME]


@pytest.fixture
def network_config(project):
    return get_network_config()


@pytest.fixture
def hardhat(network_api, network_config):
    return create_hardhat_provider(network_api, network_config)


def create_hardhat_provider(network_api: NetworkAPI, config: HardhatNetworkConfig):
    return HardhatProvider(
        name="hardhat",
        network=network_api,
        config=config,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )


@pytest.fixture
def hardhat_connected(hardhat):
    hardhat.connect()
    try:
        yield hardhat
    finally:
        hardhat.disconnect()
