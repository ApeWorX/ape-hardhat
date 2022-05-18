import json
from pathlib import Path

import ape
import pytest  # type: ignore
from _pytest.runner import pytest_runtest_makereport as orig_pytest_runtest_makereport
from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from ape.contracts import ContractContainer
from ape.managers.project import ProjectManager
from ethpm_types import ContractType

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


BASE_CONTRACTS_PATH = Path(__file__).parent / "data" / "contracts"


def pytest_runtest_makereport(item, call):
    tr = orig_pytest_runtest_makereport(item, call)
    if call.excinfo is not None:
        if "too many requests" in str(call.excinfo):
            tr.outcome = "skipped"
            tr.wasxfail = "reason: Alchemy requests overloaded (likely in CI)"

    return tr


@pytest.fixture(scope="session", params=("solidity", "vyper"))
def raw_contract_type(request):
    path = BASE_CONTRACTS_PATH / f"{request.param}_contract.json"
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def contract_type(raw_contract_type) -> ContractType:
    return ContractType.parse_obj(raw_contract_type)


@pytest.fixture(scope="session")
def contract_container(contract_type) -> ContractContainer:
    return ContractContainer(contract_type=contract_type)


@pytest.fixture(scope="session")
def contract_instance(owner, contract_container, hardhat_connected):
    return owner.deploy(contract_container)


@pytest.fixture(scope="session")
def config():
    return ape.config


@pytest.fixture(scope="session", autouse=True)
def in_tests_dir(config):
    with config.using_project(Path(__file__).parent):
        yield


@pytest.fixture(scope="session")
def accounts():
    return ape.accounts.test_accounts


@pytest.fixture(scope="session")
def sender(accounts):
    return accounts[0]


@pytest.fixture(scope="session")
def receiver(accounts):
    return accounts[1]


@pytest.fixture(scope="session")
def owner(accounts):
    return accounts[2]


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
def hardhat_connected(networks, local_network_api):
    provider = get_hardhat_provider(local_network_api)
    provider.connect()
    original_provider = networks.active_provider
    networks.active_provider = provider
    yield provider
    provider.disconnect()
    networks.active_provider = original_provider
