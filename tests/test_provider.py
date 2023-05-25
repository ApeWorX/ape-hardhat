import tempfile
from pathlib import Path

import pytest
from ape.api import ReceiptAPI
from ape.api.accounts import ImpersonatedAccount
from ape.contracts import ContractContainer
from ape.exceptions import ContractLogicError
from ape.pytest.contextmanagers import RevertsContextManager as reverts
from ape.types import CallTreeNode, TraceFrame
from evm_trace import CallType
from hexbytes import HexBytes

from ape_hardhat.exceptions import HardhatNotInstalledError, HardhatProviderError
from ape_hardhat.provider import HARDHAT_CHAIN_ID

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"


def test_instantiation(disconnected_provider, name):
    assert disconnected_provider.name == name


def test_connect_and_disconnect(disconnected_provider):
    # Use custom port to prevent connecting to a port used in another test.

    disconnected_provider.port = 8555
    disconnected_provider.connect()

    try:
        assert disconnected_provider.is_connected
        assert disconnected_provider.chain_id == HARDHAT_CHAIN_ID
    finally:
        disconnected_provider.disconnect()

    assert not disconnected_provider.is_connected
    assert disconnected_provider.process is None


def test_gas_price(connected_provider):
    gas_price = connected_provider.gas_price
    assert gas_price > 1


def test_uri_disconnected(disconnected_provider):
    with pytest.raises(
        HardhatProviderError, match=r"Can't build URI before `connect\(\)` is called\."
    ):
        _ = disconnected_provider.uri


def test_uri(connected_provider):
    expected_uri = f"http://127.0.0.1:{connected_provider.port}"
    assert expected_uri in connected_provider.uri


def test_set_block_gas_limit(connected_provider):
    gas_limit = connected_provider.get_block("latest").gas_limit
    assert connected_provider.set_block_gas_limit(gas_limit) is True


def test_set_timestamp(connected_provider):
    start_time = connected_provider.get_block("pending").timestamp
    expected_timestamp = start_time + 5
    connected_provider.set_timestamp(expected_timestamp)
    new_time = connected_provider.get_block("pending").timestamp
    assert new_time == expected_timestamp


def test_mine(connected_provider):
    block_num = connected_provider.get_block("latest").number
    connected_provider.mine()
    next_block_num = connected_provider.get_block("latest").number

    # NOTE: Uses >= due to x-dist
    assert next_block_num >= block_num + 1


def test_mine_many_blocks(connected_provider):
    block_num = connected_provider.get_block("latest").number
    connected_provider.mine(12)
    next_block_num = connected_provider.get_block("latest").number

    # NOTE: Uses >= due to x-dist
    assert next_block_num >= block_num + 12


def test_revert_failure(connected_provider):
    assert connected_provider.revert(0xFFFF) is False


def test_get_balance(connected_provider, owner):
    assert connected_provider.get_balance(owner.address)


def test_snapshot_and_revert(connected_provider):
    snap = connected_provider.snapshot()

    block_1 = connected_provider.get_block("latest")
    connected_provider.mine()
    block_2 = connected_provider.get_block("latest")
    assert block_2.number > block_1.number
    assert block_1.hash != block_2.hash

    connected_provider.revert(snap)
    block_3 = connected_provider.get_block("latest")
    assert block_1.number == block_3.number
    assert block_1.hash == block_3.hash


def test_unlock_account(connected_provider, owner, contract_a, accounts):
    # This first statement is not needed but testing individually anyway.
    assert connected_provider.unlock_account(TEST_WALLET_ADDRESS) is True
    # This is the real way to get an impersonated account.
    impersonated_account = accounts[TEST_WALLET_ADDRESS]
    assert isinstance(impersonated_account, ImpersonatedAccount)

    # Ensure can transact.
    receipt = contract_a.methodWithoutArguments(sender=impersonated_account)
    assert not receipt.failed


def test_get_transaction_trace(connected_provider, sender, receiver):
    transfer = sender.transfer(receiver, 1)
    frame_data = connected_provider.get_transaction_trace(transfer.txn_hash)
    for frame in frame_data:
        assert isinstance(frame, TraceFrame)


def test_get_call_tree(connected_provider, sender, receiver):
    transfer = sender.transfer(receiver, 1)
    call_tree = connected_provider.get_call_tree(transfer.txn_hash)
    assert isinstance(call_tree, CallTreeNode)
    assert call_tree.call_type == CallType.CALL.value
    assert repr(call_tree) == "0xc89D42189f0450C2b2c3c61f58Ec5d628176A1E7.0x()"


def test_request_timeout(connected_provider, config):
    # Test value set in `ape-config.yaml`
    expected = 29
    actual = connected_provider.web3.provider._request_kwargs["timeout"]
    assert actual == expected

    # Test default behavior
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        with config.using_project(temp_dir):
            assert connected_provider.timeout == 30


def test_send_transaction(contract_instance, owner):
    contract_instance.setNumber(10, sender=owner)
    assert contract_instance.myNumber() == 10


def test_contract_revert_no_message(owner, contract_instance):
    # The Contract raises empty revert when setting number to 5.
    with pytest.raises(ContractLogicError, match="Transaction failed."):
        contract_instance.setNumber(5, sender=owner)


def test_contract_revert_custom_exception(owner, get_contract_type, accounts):
    ct = get_contract_type("has_error")
    contract = owner.deploy(ContractContainer(ct))

    # Hex match for backwards compat.
    # Will support the same custom
    with pytest.raises(contract.Unauthorized) as err:
        contract.withdraw(sender=accounts[7])

    assert err.value.inputs == {"addr": accounts[7].address, "counter": 123}


def test_contract_dev_message(owner, get_contract_type):
    sub_ct = get_contract_type("sub_reverts_contract")
    sub = owner.deploy(ContractContainer(sub_ct))
    container = ContractContainer(get_contract_type("reverts_contract"))
    contract = owner.deploy(container, sub)

    with reverts(dev_message="dev: one"):
        contract.revertStrings(1, sender=owner)
    with reverts(dev_message="dev: error"):
        contract.revertStrings(2, sender=owner)
    with reverts(dev_message="dev: such modifiable, wow"):
        contract.revertStrings(4, sender=owner)
    with reverts(dev_message="dev: foobarbaz"):
        contract.revertStrings(13, sender=owner)
    with reverts(dev_message="dev: great job"):
        contract.revertStrings(31337, sender=owner)
    with reverts(dev_message="dev: sub-zero"):
        contract.subRevertStrings(0, sender=owner)


def test_transaction_contract_as_sender(contract_instance, connected_provider):
    # Set balance so test wouldn't normally fail from lack of funds
    connected_provider.set_balance(contract_instance.address, "1000 ETH")

    with pytest.raises(ContractLogicError, match="!authorized"):
        # Task failed successfully
        contract_instance.setNumber(10, sender=contract_instance)


@pytest.mark.parametrize(
    "amount", ("50 ETH", int(50e18), "0x2b5e3af16b1880000", "50000000000000000000")
)
def test_set_balance(connected_provider, owner, convert, amount):
    connected_provider.set_balance(owner.address, amount)
    assert owner.balance == convert("50 ETH", int)


def test_set_code(connected_provider, contract_instance):
    provider = connected_provider
    code = provider.get_code(contract_instance.address)
    assert type(code) == HexBytes
    assert provider.set_code(contract_instance.address, "0x00") is True
    assert provider.get_code(contract_instance.address) != code
    assert provider.set_code(contract_instance.address, code) is True
    assert provider.get_code(contract_instance.address) == code


def test_return_value(connected_provider, contract_instance, owner):
    receipt = contract_instance.setAddress(owner.address, sender=owner)
    assert isinstance(receipt, ReceiptAPI)
    assert receipt.return_value == 123


def test_get_receipt(connected_provider, contract_instance, owner):
    receipt = contract_instance.setAddress(owner.address, sender=owner)
    actual = connected_provider.get_receipt(receipt.txn_hash)
    assert receipt.txn_hash == actual.txn_hash
    assert actual.receiver == contract_instance.address
    assert actual.sender == receipt.sender


def test_revert_error(error_contract, not_owner):
    """
    Test matching a revert custom Solidity error.
    """
    with pytest.raises(error_contract.Unauthorized):
        error_contract.withdraw(sender=not_owner)


def test_revert_error_from_impersonated_account(error_contract, accounts):
    account = accounts[TEST_WALLET_ADDRESS]
    with pytest.raises(error_contract.Unauthorized) as err:
        error_contract.withdraw(sender=account)

    # Before, this would fail because there would not be an associated txn
    # because the account is impersonated.
    assert err.value.txn.txn_hash.startswith("0x")


def test_use_different_config(temp_config, networks):
    data = {"hardhat": {"hardhat_config_file": "./hardhat.config.ts"}}
    with temp_config(data):
        provider = networks.ethereum.local.get_provider("hardhat")
        assert provider.hardhat_config_file.name == "hardhat.config.ts"
        assert "--config" in provider.build_command()


def test_connect_when_hardhat_not_installed(networks, mock_web3, install_detection_fail):
    """
    Verifies that if both Hardhat is sensed to not be installed correctly
    and Web3 doesn't connect, you get the custom error about installing
    Hardhat in the project.
    """

    provider = networks.ethereum.local.get_provider("hardhat")
    mock_web3.is_connected.return_value = False
    expected = (
        r"Missing local Hardhat NPM package\. "
        r"See ape-hardhat README for install steps\. "
        r"Note: global installation of Hardhat will not work and "
        r"you must be in your project's directory\."
    )
    with pytest.raises(HardhatNotInstalledError, match=expected):
        provider.connect()
