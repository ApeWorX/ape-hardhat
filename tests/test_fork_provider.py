import tempfile
from pathlib import Path

import pytest
from ape.exceptions import ContractLogicError

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
def test_transaction(owner, contract_instance):
    receipt = contract_instance.setNumber(6, sender=owner)
    assert receipt.sender == owner

    value = contract_instance.myNumber()
    assert value == 6


@alchemy_xfail
def test_revert(sender, contract_instance):
    # 'sender' is not the owner so it will revert (with a message)
    with pytest.raises(ContractLogicError) as err:
        contract_instance.setNumber(6, sender=sender)

    assert str(err.value) == "!authorized"


@alchemy_xfail
def test_contract_revert_no_message(owner, contract_instance):
    # The Contract raises empty revert when setting number to 5.
    with pytest.raises(ContractLogicError) as err:
        contract_instance.setNumber(5, sender=owner)

    assert str(err.value) == "Transaction failed."  # Default message


@alchemy_xfail
def test_transaction_contract_as_sender(contract_instance, mainnet_fork_provider):
    with pytest.raises(ContractLogicError):
        # Task failed successfully
        contract_instance.setNumber(10, sender=contract_instance)


@alchemy_xfail
def test_transaction_unknown_contract_as_sender(accounts, mainnet_fork_provider):
    multi_sig = accounts["0xFEB4acf3df3cDEA7399794D0869ef76A6EfAff52"]
    multi_sig.transfer(accounts[0], "100 gwei")
