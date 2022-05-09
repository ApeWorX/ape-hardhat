import pytest
from ape import networks
from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractContainer, ContractInstance
from ape.exceptions import SignatureError
from ethpm_types import ContractType


@pytest.fixture(scope="module")
def contract_type(raw_contract_type) -> ContractType:
    return ContractType.parse_obj(raw_contract_type)


@pytest.fixture(scope="module")
def contract_container(contract_type) -> ContractContainer:
    return ContractContainer(contract_type=contract_type)


@pytest.fixture(scope="module")
def contract_instance(owner, contract_container, hardhat_connected_from_ape) -> ContractInstance:
    return owner.deploy(contract_container)


@pytest.fixture(scope="module")
def hardhat_connected_from_ape():
    with networks.parse_network_choice(f"ethereum:{LOCAL_NETWORK_NAME}:hardhat") as provider:
        yield provider


def test_send_transaction(contract_instance, owner):
    contract_instance.set_number(10, sender=owner)
    assert contract_instance.my_number() == 10

    # Have to be in the same test because of X-dist complications
    with pytest.raises(SignatureError):
        contract_instance.set_number(20)
