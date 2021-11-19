from pathlib import Path

import pytest  # type: ignore
from ape import Project, networks
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
    return networks.ecosystems["ethereum"]["development"]


@pytest.fixture
def network_config(project):
    return get_network_config()


@pytest.fixture
def hardhat_provider(network_api, network_config):
    provider = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    provider.connect()
    return provider
