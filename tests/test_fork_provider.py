from pathlib import Path

import ape
import pytest
from ape import networks
from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractContainer, ContractInstance
from ape.exceptions import SignatureError
from ethpm_types import ContractType

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


@pytest.fixture(scope="module")
def contract_type(raw_contract_type) -> ContractType:
    return ContractType.parse_obj(raw_contract_type)


@pytest.fixture(scope="module")
def contract_container(contract_type) -> ContractContainer:
    return ContractContainer(contract_type=contract_type)


@pytest.fixture()
def contract_instance(owner, contract_container, mainnet_fork_provider) -> ContractInstance:
    return owner.deploy(contract_container)

@pytest.fixture()
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


def test_transaction_contract_as_sender(contract_instance, mainnet_fork_provider):
    assert contract_instance
    # contract_instance.set_number(10, sender=contract_instance)
    # assert contract_instance.my_number() == 10
