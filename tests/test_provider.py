import tempfile
from pathlib import Path

import pytest
from ape.exceptions import ContractLogicError, SignatureError
from ape.utils import DEFAULT_TEST_MNEMONIC
from evm_trace import CallTreeNode, CallType, TraceFrame
from hexbytes import HexBytes

from ape_hardhat.exceptions import HardhatProviderError
from ape_hardhat.provider import HARDHAT_CHAIN_ID, HARDHAT_CONFIG_FILE_NAME, HardhatProvider

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"


def test_instantiation(disconnected_provider):
    assert disconnected_provider.name == "hardhat"


def test_connect_and_disconnect(create_provider):
    # Use custom port to prevent connecting to a port used in another test.

    hardhat = create_provider()
    hardhat.port = 8555
    hardhat.connect()

    # Verify config file got created
    config = Path(HARDHAT_CONFIG_FILE_NAME)
    config_text = config.read_text()
    assert config.exists()
    assert DEFAULT_TEST_MNEMONIC in config_text

    try:
        assert hardhat.is_connected
        assert hardhat.chain_id == HARDHAT_CHAIN_ID
    finally:
        hardhat.disconnect()

    assert not hardhat.is_connected
    assert hardhat.process is None


def test_gas_price(connected_provider):
    gas_price = connected_provider.gas_price
    assert gas_price > 1


def test_uri_disconnected(disconnected_provider):
    with pytest.raises(HardhatProviderError) as err:
        _ = disconnected_provider.uri

    assert "Can't build URI before `connect()` is called." in str(err.value)


def test_uri(connected_provider):
    expected_uri = f"http://127.0.0.1:{connected_provider.port}"
    assert expected_uri in connected_provider.uri


@pytest.mark.parametrize(
    "method,args,expected",
    [
        (HardhatProvider.get_nonce, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_balance, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_code, [TEST_WALLET_ADDRESS], HexBytes("")),
    ],
)
def test_rpc_methods(connected_provider, method, args, expected):
    assert method(connected_provider, *args) == expected


def test_multiple_instances(create_provider):
    """
    Validate the somewhat tricky internal logic of running multiple Hardhat subprocesses
    under a single parent process.
    """
    # instantiate the providers (which will start the subprocesses) and validate the ports
    provider_1 = create_provider()
    provider_2 = create_provider()
    provider_3 = create_provider()
    provider_1.port = 8556
    provider_2.port = 8557
    provider_3.port = 8558
    provider_1.connect()
    provider_2.connect()
    provider_3.connect()

    # The web3 clients must be different in the HH provider instances (compared to the
    # behavior of the EthereumProvider base class, where it's a shared classvar)
    assert provider_1._web3 != provider_2._web3 != provider_3._web3

    assert provider_1.port == 8556
    assert provider_2.port == 8557
    assert provider_3.port == 8558

    provider_1.mine()
    provider_2.mine()
    provider_3.mine()
    hash_1 = provider_1.get_block("latest").hash
    hash_2 = provider_2.get_block("latest").hash
    hash_3 = provider_3.get_block("latest").hash
    assert hash_1 != hash_2 != hash_3


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


def test_unlock_account(connected_provider):
    assert connected_provider.unlock_account(TEST_WALLET_ADDRESS) is True
    assert TEST_WALLET_ADDRESS in connected_provider.unlocked_accounts


def test_get_transaction_trace(connected_provider, sender, receiver):
    transfer = sender.transfer(receiver, 1)
    frame_data = connected_provider.get_transaction_trace(transfer.txn_hash)
    for frame in frame_data:
        assert isinstance(frame, TraceFrame)


def test_get_call_tree(connected_provider, sender, receiver):
    transfer = sender.transfer(receiver, 1)
    call_tree = connected_provider.get_call_tree(transfer.txn_hash)
    assert isinstance(call_tree, CallTreeNode)
    assert call_tree.call_type == CallType.CALL
    assert repr(call_tree) == "CALL: 0xc89D42189f0450C2b2c3c61f58Ec5d628176A1E7 [0 gas]"


def test_request_timeout(connected_provider, config, create_provider):
    actual = connected_provider.web3.provider._request_kwargs["timeout"]
    expected = 29  # Value set in `ape-config.yaml`
    assert actual == expected

    # Test default behavior
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        with config.using_project(temp_dir):
            provider = create_provider()
            assert provider.timeout == 30


def test_send_transaction(contract_instance, owner):
    contract_instance.setNumber(10, sender=owner)
    assert contract_instance.myNumber() == 10

    # Have to be in the same test because of X-dist complications
    with pytest.raises(SignatureError):
        contract_instance.setNumber(20)


def test_contract_revert_no_message(owner, contract_instance):
    # The Contract raises empty revert when setting number to 5.
    with pytest.raises(ContractLogicError, match="Transaction failed."):
        contract_instance.setNumber(5, sender=owner)


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
    assert receipt.return_value == 123


def test_get_receipt(connected_provider, contract_instance, owner):
    receipt = contract_instance.setAddress(owner.address, sender=owner)
    actual = connected_provider.get_receipt(receipt.txn_hash)
    assert receipt.txn_hash == actual.txn_hash
    assert actual.receiver == contract_instance.address
    assert actual.sender == receipt.sender
