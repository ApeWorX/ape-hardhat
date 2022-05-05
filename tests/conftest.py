from pathlib import Path

import ape
import pytest  # type: ignore
from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from ape.managers.project import ProjectManager

from ape_hardhat import HardhatProvider


def get_project() -> ProjectManager:
    return ape.Project(Path(__file__).parent)


def get_hardhat_provider(network_api: NetworkAPI):
    return HardhatProvider(
        name="hardhat",
        network=network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )


@pytest.fixture(scope="session")
def test_accounts():
    return ape.accounts.test_accounts


@pytest.fixture(scope="session")
def sender(test_accounts):
    return test_accounts[0]


@pytest.fixture(scope="session")
def receiver(test_accounts):
    return test_accounts[1]


@pytest.fixture(scope="session")
def owner(test_accounts):
    return test_accounts[2]


@pytest.fixture(scope="session")
def project():
    return get_project()


@pytest.fixture(scope="session")
def networks():
    return ape.networks


@pytest.fixture(scope="session")
def local_network_api(networks):
    return networks.ecosystems["ethereum"][LOCAL_NETWORK_NAME]


@pytest.fixture(scope="session")
def hardhat_disconnected(local_network_api):
    provider = get_hardhat_provider(local_network_api)
    return provider


@pytest.fixture(scope="session")
def hardhat_connected(local_network_api):
    provider = get_hardhat_provider(local_network_api)
    provider.port = "auto"  # For better multi-processing support
    provider.connect()
    ape.networks.active_provider = provider
    try:
        yield provider
    finally:
        provider.disconnect()
