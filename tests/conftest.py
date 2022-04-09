from pathlib import Path

import pytest  # type: ignore
from ape import Project, accounts, networks
from ape.api.networks import LOCAL_NETWORK_NAME, NetworkAPI
from ape.contracts import ContractContainer, ContractInstance
from ape.managers.project import ProjectManager
from ethpm_types import ContractType

from ape_hardhat import HardhatProvider

RAW_CONTRACT_TYPE = {
    "contractName": "TestContract",
    "sourceId": "TestContract.vy",
    "deploymentBytecode": {
        "bytecode": "0x3360005561012656600436101561000d57610113565b600035601c52600051346101195763d6d1ee148114156100c9576000543314610075576308c379a061014052602061016052600b610180527f21617574686f72697a65640000000000000000000000000000000000000000006101a05261018050606461015cfd5b60056004351815610119576001546002556004356001556004357f2295d5ec33e3af0d43cc4b73aa3cd7d784150fe365cbdb4b4fd338220e4f135761014080808060025481525050602090509050610140a2005b638da5cb5b8114156100e15760005460005260206000f35b63be23d7b98114156100f95760015460005260206000f35b632b3979478114156101115760025460005260206000f35b505b60006000fd5b600080fd5b61000861012603610008600039610008610126036000f3"  # noqa: E501
    },
    "runtimeBytecode": {
        "bytecode": "0x600436101561000d57610113565b600035601c52600051346101195763d6d1ee148114156100c9576000543314610075576308c379a061014052602061016052600b610180527f21617574686f72697a65640000000000000000000000000000000000000000006101a05261018050606461015cfd5b60056004351815610119576001546002556004356001556004357f2295d5ec33e3af0d43cc4b73aa3cd7d784150fe365cbdb4b4fd338220e4f135761014080808060025481525050602090509050610140a2005b638da5cb5b8114156100e15760005460005260206000f35b63be23d7b98114156100f95760015460005260206000f35b632b3979478114156101115760025460005260206000f35b505b60006000fd5b600080fd"  # noqa: E501
    },
    "abi": [
        {
            "type": "event",
            "name": "NumberChange",
            "inputs": [
                {"name": "prev_num", "type": "uint256", "indexed": False},
                {"name": "new_num", "type": "uint256", "indexed": True},
            ],
            "anonymous": False,
        },
        {"type": "constructor", "stateMutability": "nonpayable", "inputs": []},
        {
            "type": "function",
            "name": "set_number",
            "stateMutability": "nonpayable",
            "inputs": [{"name": "num", "type": "uint256"}],
            "outputs": [],
        },
        {
            "type": "function",
            "name": "owner",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [{"name": "", "type": "address"}],
        },
        {
            "type": "function",
            "name": "my_number",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [{"name": "", "type": "uint256"}],
        },
        {
            "type": "function",
            "name": "prev_number",
            "stateMutability": "view",
            "inputs": [],
            "outputs": [{"name": "", "type": "uint256"}],
        },
    ],
    "userdoc": {},
    "devdoc": {},
}


@pytest.fixture
def hardhat_connected_from_ape():
    with networks.parse_network_choice(f"ethereum:{LOCAL_NETWORK_NAME}:hardhat") as provider:
        yield provider


@pytest.fixture
def contract_type() -> ContractType:
    return ContractType.parse_obj(RAW_CONTRACT_TYPE)


@pytest.fixture
def contract_container(contract_type) -> ContractContainer:
    return ContractContainer(contract_type=contract_type)


@pytest.fixture
def contract_instance(owner, contract_container, hardhat_connected_from_ape) -> ContractInstance:
    return owner.deploy(contract_container)


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
