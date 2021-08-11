from pathlib import Path

import pytest  # type: ignore
from ape import Project, networks

from ape_hardhat import HardhatProvider


@pytest.fixture
def project():
    return Project(Path(__file__).parent)


@pytest.fixture
def network_api():
    return networks.ecosystems["ethereum"]["development"]


@pytest.fixture
def network_config(project):
    config_classes = [
        klass for (name, klass) in project.config.plugin_manager.config_class if name == "hardhat"
    ]
    assert len(config_classes) == 1
    return config_classes[0]()


@pytest.fixture
def hh_provider(network_api, network_config):
    hh = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    hh.connect()
    return hh
