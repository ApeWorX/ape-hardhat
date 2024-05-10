from pathlib import Path

import pytest
from ape.api.networks import LOCAL_NETWORK_NAME
from ape.contracts import ContractInstance
from ape.exceptions import ContractLogicError
from ape.utils import create_tempdir
from ape_ethereum.ecosystem import NETWORKS

from ape_hardhat.provider import HardhatForkProvider

TESTS_DIRECTORY = Path(__file__).parent
TEST_ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"


@pytest.fixture
def mainnet_fork_contract_instance(owner, contract_container, mainnet_fork_provider):
    return owner.deploy(contract_container)


@pytest.mark.fork
def test_multiple_providers(
    name, networks, connected_provider, mainnet_fork_port, sepolia_fork_port
):
    default_host = "http://127.0.0.1:8545"
    assert networks.active_provider.name == name
    assert networks.active_provider.network.name == LOCAL_NETWORK_NAME
    assert networks.active_provider.uri == default_host
    mainnet_fork_host = f"http://127.0.0.1:{mainnet_fork_port}"

    with networks.ethereum.mainnet_fork.use_provider(
        name, provider_settings={"host": mainnet_fork_host}
    ):
        assert networks.active_provider.name == name
        assert networks.active_provider.network.name == "mainnet-fork"
        assert networks.active_provider.uri == mainnet_fork_host
        sepolia_fork_host = f"http://127.0.0.1:{sepolia_fork_port}"

        with networks.ethereum.sepolia_fork.use_provider(
            name, provider_settings={"host": sepolia_fork_host}
        ):
            assert networks.active_provider.name == name
            assert networks.active_provider.network.name == "sepolia-fork"
            assert networks.active_provider.uri == sepolia_fork_host

        assert networks.active_provider.name == name
        assert networks.active_provider.network.name == "mainnet-fork"
        assert networks.active_provider.uri == mainnet_fork_host

    assert networks.active_provider.name == name
    assert networks.active_provider.network.name == LOCAL_NETWORK_NAME
    assert networks.active_provider.uri == default_host


@pytest.mark.parametrize("network", [k for k in NETWORKS.keys() if k != "goerli"])
def test_fork_config(name, config, network):
    plugin_config = config.get_config(name)
    network_config = plugin_config["fork"].get("ethereum", {}).get(network, {})
    assert network_config.get("upstream_provider") == "alchemy", "config not registered"


@pytest.mark.fork
def test_sepolia_impersonate(accounts, sepolia_fork_provider):
    impersonated_account = accounts[TEST_ADDRESS]
    other_account = accounts[0]
    receipt = impersonated_account.transfer(other_account, "1 wei")
    assert receipt.receiver == other_account
    assert receipt.sender == impersonated_account


@pytest.mark.fork
def test_mainnet_impersonate(accounts, mainnet_fork_provider):
    impersonated_account = accounts[TEST_ADDRESS]
    other_account = accounts[0]
    receipt = impersonated_account.transfer(other_account, "1 wei")
    assert receipt.receiver == other_account
    assert receipt.sender == impersonated_account


@pytest.mark.fork
def test_request_timeout(networks, config, mainnet_fork_provider):
    actual = mainnet_fork_provider.web3.provider._request_kwargs["timeout"]
    expected = 360  # Value set in `ape-config.yaml`
    assert actual == expected

    # Test default behavior
    with create_tempdir() as temp_dir:
        with config.using_project(temp_dir):
            assert networks.active_provider.timeout == 300


@pytest.mark.fork
def test_reset_fork_no_fork_block_number(networks, sepolia_fork_provider):
    sepolia_fork_provider.mine(5)
    prev_block_num = sepolia_fork_provider.get_block("latest").number
    sepolia_fork_provider.reset_fork()
    block_num_after_reset = sepolia_fork_provider.get_block("latest").number
    assert block_num_after_reset < prev_block_num


@pytest.mark.fork
def test_reset_fork_specify_block_number_via_argument(networks, sepolia_fork_provider):
    sepolia_fork_provider.mine(5)
    prev_block_num = sepolia_fork_provider.get_block("latest").number
    new_block_number = prev_block_num - 1
    sepolia_fork_provider.reset_fork(block_number=new_block_number)
    block_num_after_reset = sepolia_fork_provider.get_block("latest").number
    assert block_num_after_reset == new_block_number


@pytest.mark.fork
def test_reset_fork_specify_block_number_via_config(mainnet_fork_provider):
    mainnet_fork_provider.mine(5)
    mainnet_fork_provider.reset_fork()
    block_num_after_reset = mainnet_fork_provider.get_block("latest").number
    assert block_num_after_reset == 17040366  # Specified in ape-config.yaml


@pytest.mark.fork
def test_transaction(owner, mainnet_fork_contract_instance):
    receipt = mainnet_fork_contract_instance.setNumber(6, sender=owner)
    assert receipt.sender == owner

    value = mainnet_fork_contract_instance.myNumber()
    assert value == 6


@pytest.mark.fork
def test_revert(sender, mainnet_fork_contract_instance):
    # 'sender' is not the owner so it will revert (with a message)
    with pytest.raises(ContractLogicError, match="!authorized"):
        mainnet_fork_contract_instance.setNumber(6, sender=sender)


@pytest.mark.fork
def test_contract_revert_no_message(owner, mainnet_fork_contract_instance, mainnet_fork_provider):
    # The Contract raises empty revert when setting number to 5.
    with pytest.raises(ContractLogicError, match="Transaction failed."):
        mainnet_fork_contract_instance.setNumber(5, sender=owner)


@pytest.mark.fork
def test_transaction_contract_as_sender(
    mainnet_fork_contract_instance, mainnet_fork_provider, convert
):
    # Set balance so test wouldn't normally fail from lack of funds
    mainnet_fork_provider.set_balance(mainnet_fork_contract_instance.address, "1000 ETH")

    with pytest.raises(ContractLogicError, match="!authorized"):
        # Task failed successfully
        mainnet_fork_contract_instance.setNumber(10, sender=mainnet_fork_contract_instance)


@pytest.mark.fork
def test_transaction_unknown_contract_as_sender(accounts, mainnet_fork_provider):
    account = "0xFEB4acf3df3cDEA7399794D0869ef76A6EfAff52"
    multi_sig = accounts[account]
    multi_sig.balance += accounts.conversion_manager.convert("1000 ETH", int)
    receipt = multi_sig.transfer(accounts[0], "100 gwei")
    assert not receipt.failed


@pytest.mark.fork
def test_get_receipt(mainnet_fork_provider, mainnet_fork_contract_instance, owner):
    receipt = mainnet_fork_contract_instance.setAddress(owner.address, sender=owner)
    actual = mainnet_fork_provider.get_receipt(receipt.txn_hash)
    assert receipt.txn_hash == actual.txn_hash
    assert actual.receiver == mainnet_fork_contract_instance.address
    assert actual.sender == receipt.sender


@pytest.mark.fork
@pytest.mark.parametrize(
    "upstream_network,port,enable_hardhat_deployments,fork_block_number,has_hardhat_deploy",
    [
        ("mainnet", 8994, False, 15_964_699, False),
        ("mainnet", 8995, False, 15_932_345, True),
        ("mainnet", 8996, True, 15_900_000, False),
        ("sepolia", 8997, False, 7_948_861, False),
        ("sepolia", 8998, False, 7_424_430, True),
        ("sepolia", 8999, True, 7_900_000, False),
    ],
)
def test_hardhat_command(
    temp_config,
    networks,
    port,
    upstream_network,
    enable_hardhat_deployments,
    fork_block_number,
    has_hardhat_deploy,
    name,
    data_folder,
):
    eth_config = {
        name: {
            "fork": {
                "ethereum": {
                    upstream_network: {
                        "enable_hardhat_deployments": enable_hardhat_deployments,
                        "block_number": fork_block_number,
                    }
                }
            },
        },
    }
    package_json = {
        "name": "contracts",
        "version": "0.1.0",
        "dependencies": {
            "hardhat": "^2.13.1",
            "hardhat-ethers": "^2.0.2",
        },
    }

    if has_hardhat_deploy:
        package_json["devDependencies"] = {"hardhat-deploy": "^0.8.10"}

    with temp_config(eth_config, package_json):
        network_api = networks.ethereum[f"{upstream_network}-fork"]
        provider = HardhatForkProvider(
            name=name,
            network=network_api,
            request_header={},
            data_folder=Path("."),
            provider_settings={},
        )
        provider._host = f"http://127.0.0.1:{port}"
        actual = provider.build_command()
        expected = [
            "node",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            "--config",
            str(data_folder / "hardhat" / "hardhat.config.js"),
            "--fork",
            provider.fork_url,
        ]
        if not enable_hardhat_deployments and has_hardhat_deploy:
            expected.append("--no-deploy")
        if fork_block_number:
            expected.extend(("--fork-block-number", str(fork_block_number)))

        assert actual[0].endswith("npx")
        assert actual[1].endswith("hardhat")
        assert actual[2:] == expected


@pytest.mark.fork
def test_connect_to_polygon(networks, owner, contract_container):
    """
    Ensures we don't get PoA middleware issue.
    Also, ensure that we using a different host (via config).
    """
    with networks.polygon.mumbai_fork.use_provider("hardhat"):
        contract = owner.deploy(contract_container)
        assert isinstance(contract, ContractInstance)  # Didn't fail
