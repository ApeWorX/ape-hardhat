import tempfile
from pathlib import Path

import pytest
from ape.exceptions import ContractLogicError, SignatureError
from ape.utils import DEFAULT_TEST_MNEMONIC
from evm_trace import TraceFrame
from hexbytes import HexBytes

from ape_hardhat.exceptions import HardhatProviderError
from ape_hardhat.providers import HARDHAT_CHAIN_ID, HARDHAT_CONFIG_FILE_NAME, HardhatProvider

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"


def test_instantiation(hardhat_disconnected):
    assert hardhat_disconnected.name == "hardhat"


def test_connect_and_disconnect(get_hardhat_provider):
    # Use custom port to prevent connecting to a port used in another test.

    hardhat = get_hardhat_provider()
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


def test_gas_price(hardhat_connected):
    gas_price = hardhat_connected.gas_price
    assert gas_price > 1


def test_uri_disconnected(hardhat_disconnected):
    with pytest.raises(HardhatProviderError) as err:
        _ = hardhat_disconnected.uri

    assert "Can't build URI before `connect()` is called." in str(err.value)


def test_uri(hardhat_connected):
    expected_uri = f"http://127.0.0.1:{hardhat_connected.port}"
    assert expected_uri in hardhat_connected.uri


@pytest.mark.parametrize(
    "method,args,expected",
    [
        (HardhatProvider.get_nonce, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_balance, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_code, [TEST_WALLET_ADDRESS], HexBytes("")),
    ],
)
def test_rpc_methods(hardhat_connected, method, args, expected):
    assert method(hardhat_connected, *args) == expected


def test_multiple_hardhat_instances(get_hardhat_provider):
    """
    Validate the somewhat tricky internal logic of running multiple Hardhat subprocesses
    under a single parent process.
    """
    # instantiate the providers (which will start the subprocesses) and validate the ports
    provider_1 = get_hardhat_provider()
    provider_2 = get_hardhat_provider()
    provider_3 = get_hardhat_provider()
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


def test_set_block_gas_limit(hardhat_connected):
    gas_limit = hardhat_connected.get_block("latest").gas_data.gas_limit
    assert hardhat_connected.set_block_gas_limit(gas_limit) is True


def test_set_timestamp(hardhat_connected):
    start_time = hardhat_connected.get_block("pending").timestamp
    hardhat_connected.set_timestamp(start_time + 5)  # Increase by 5 seconds
    new_time = hardhat_connected.get_block("pending").timestamp

    # Adding 5 seconds but seconds can be weird so give it a 1 second margin.
    assert 4 <= new_time - start_time <= 6


def test_mine(hardhat_connected):
    block_num = hardhat_connected.get_block("latest").number
    hardhat_connected.mine()
    next_block_num = hardhat_connected.get_block("latest").number
    assert next_block_num > block_num


def test_revert_failure(hardhat_connected):
    assert hardhat_connected.revert(0xFFFF) is False


def test_snapshot_and_revert(hardhat_connected):
    snap = hardhat_connected.snapshot()

    block_1 = hardhat_connected.get_block("latest")
    hardhat_connected.mine()
    block_2 = hardhat_connected.get_block("latest")
    assert block_2.number > block_1.number
    assert block_1.hash != block_2.hash

    hardhat_connected.revert(snap)
    block_3 = hardhat_connected.get_block("latest")
    assert block_1.number == block_3.number
    assert block_1.hash == block_3.hash


def test_unlock_account(hardhat_connected):
    assert hardhat_connected.unlock_account(TEST_WALLET_ADDRESS) is True
    assert TEST_WALLET_ADDRESS in hardhat_connected.unlocked_accounts


def test_get_transaction_trace(hardhat_connected, sender, receiver):
    transfer = sender.transfer(receiver, 1)
    logs = hardhat_connected.get_transaction_trace(transfer.txn_hash)
    for log in logs:
        assert isinstance(log, TraceFrame)


def test_request_timeout(hardhat_connected, config, get_hardhat_provider):
    actual = hardhat_connected.web3.provider._request_kwargs["timeout"]  # type: ignore
    expected = 29  # Value set in `ape-config.yaml`
    assert actual == expected

    # Test default behavior
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        with config.using_project(temp_dir):
            provider = get_hardhat_provider()
            assert provider.timeout == 30


def test_send_transaction(contract_instance, owner, hardhat_connected):
    contract_instance.setNumber(10, sender=owner)
    assert contract_instance.myNumber() == 10

    # Have to be in the same test because of X-dist complications
    with pytest.raises(SignatureError):
        contract_instance.setNumber(20)


def test_contract_revert_no_message(owner, contract_instance):
    # The Contract raises empty revert when setting number to 5.
    with pytest.raises(ContractLogicError) as err:
        contract_instance.setNumber(5, sender=owner)

    assert str(err.value) == "Transaction failed."


def test_transaction_contract_as_sender(contract_instance):
    with pytest.raises(ContractLogicError) as err:
        # Task failed successfully
        contract_instance.setNumber(10, sender=contract_instance)

    assert str(err.value) == "!authorized"
