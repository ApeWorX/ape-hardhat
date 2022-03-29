from pathlib import Path

import pytest  # type: ignore
from ape import Project, accounts, networks
from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from ape.managers.project import ProjectManager

from ape_hardhat import HardhatProvider


def get_project() -> ProjectManager:
    return Project(Path(__file__).parent)


def get_hardhat_provider(network_api: NetworkAPI):
    return HardhatProvider(
        name="hardhat",
        network=network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )


@pytest.fixture
def test_accounts():
    return accounts.test_accounts


@pytest.fixture
def sender(test_accounts):
    return test_accounts[0]


@pytest.fixture
def receiver(test_accounts):
    return test_accounts[1]


@pytest.fixture
def owner(test_accounts):
    return test_accounts[2]


@pytest.fixture(scope="session")
def project():
    return get_project()


@pytest.fixture(scope="session")
def network_api():
    return networks.ecosystems["ethereum"][LOCAL_NETWORK_NAME]


@pytest.fixture(scope="session")
def hardhat_disconnected(network_api):
    provider = get_hardhat_provider(network_api)
    return provider


@pytest.fixture(scope="session")
def hardhat_connected(network_api):
    provider = get_hardhat_provider(network_api)
    provider.port = "auto"  # For better multi-processing support
    provider.connect()
    networks.active_provider = provider
    try:
        yield provider
    finally:
        provider.disconnect()
