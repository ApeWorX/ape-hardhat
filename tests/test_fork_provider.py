import tempfile
from pathlib import Path

import pytest
from ape.contracts import ContractContainer, ContractInstance
from ape.exceptions import ContractLogicError
from ethpm_types import ContractType

from ape_hardhat.providers import HardhatMainnetForkProvider

TESTS_DIRECTORY = Path(__file__).parent
alchemy_xfail = pytest.mark.xfail(strict=False, reason="Fails to establish connection with Alchemy")


@pytest.fixture(scope="module")
def mainnet_fork_provider(networks):
    network_api = networks.ecosystems["ethereum"]["mainnet-fork"]
    provider = create_mainnet_fork_provider(network_api)
    provider.port = 9001
    provider.connect()
    networks.active_provider = provider
    yield provider
    provider.disconnect()
    networks.active_provider = None


def create_mainnet_fork_provider(network_api):
    return HardhatMainnetForkProvider(
        name="hardhat",
        network=network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )


@pytest.fixture(scope="module")
def contract_type(raw_contract_type) -> ContractType:
    return ContractType.parse_obj(raw_contract_type)


@pytest.fixture(scope="module")
def contract_container(contract_type) -> ContractContainer:
    return ContractContainer(contract_type=contract_type)


@pytest.fixture(scope="module")
def contract_instance(owner, contract_container, mainnet_fork_provider) -> ContractInstance:
    return owner.deploy(contract_container)


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


@alchemy_xfail
def test_request_timeout(mainnet_fork_provider, config, network_api):
    actual = mainnet_fork_provider.web3.provider._request_kwargs["timeout"]  # type: ignore
    expected = 360  # Value set in `ape-config.yaml`
    assert actual == expected

    # Test default behavior
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        with config.using_project(temp_dir):
            provider = create_mainnet_fork_provider(network_api)
            assert provider.timeout == 300


@alchemy_xfail
def test_reset_fork(networks, mainnet_fork_provider):
    mainnet_fork_provider.mine()
    prev_block_num = mainnet_fork_provider.get_block("latest").number
    mainnet_fork_provider.reset_fork()
    block_num_after_reset = mainnet_fork_provider.get_block("latest").number
    assert block_num_after_reset < prev_block_num


@alchemy_xfail
def test_transaction_contract_as_sender(networks, network_api, contract_instance):
    with pytest.raises(ContractLogicError):
        # Task failed successfully
        contract_instance.set_number(10, sender=contract_instance)
