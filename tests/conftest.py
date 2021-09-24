from pathlib import Path

import pytest  # type: ignore
from ape import Project, networks
from ape_http.providers import EthereumNetworkConfig

from ape_hardhat import HardhatProvider


def get_project():
    return Project(Path(__file__).parent)


def get_network_config():
    p = get_project()
    config_classes = [
        klass for (name, klass) in p.config.plugin_manager.config_class if name == "hardhat"
    ]
    assert len(config_classes) == 1
    config = config_classes[0]()

    # need to instantiate a new instance of this otherwise it's shared across HH instances
    config.ethereum = EthereumNetworkConfig()

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
    hh = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    hh.connect()
    return hh
