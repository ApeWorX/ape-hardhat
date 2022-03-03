import pytest
from hexbytes import HexBytes

from ape_hardhat.exceptions import HardhatProviderError
from ape_hardhat.process import HARDHAT_CHAIN_ID
from ape_hardhat.providers import HardhatProvider
from tests.conftest import create_hardhat_provider, get_network_config

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"


def test_instantiation(hardhat):
    assert hardhat.name == "hardhat"


def test_connect(hardhat):
    hardhat.connect()
    try:
        assert hardhat.chain_id == HARDHAT_CHAIN_ID
    finally:
        hardhat.disconnect()


def test_disconnect(network_api, network_config):
    # Use custom port to prevent connecting to a port used in another test.
    network_config.port = 8555
    provider = create_hardhat_provider(network_api, network_config)
    provider.connect()
    provider.disconnect()
    assert provider.process is None


def test_gas_price(hardhat_connected):
    gas_price = hardhat_connected.gas_price
    assert gas_price > 1


def test_uri_not_connected(hardhat):
    with pytest.raises(HardhatProviderError) as err:
        _ = hardhat.uri

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


def test_multiple_hardhat_instances(network_api):
    """
    Validate the somewhat tricky internal logic of running multiple Hardhat subprocesses
    under a single parent process.
    """
    network_config_1 = get_network_config()
    network_config_1.port = 8556
    network_config_2 = get_network_config()
    network_config_2.port = 8557
    network_config_3 = get_network_config()
    network_config_3.port = 8558

    # instantiate the providers (which will start the subprocesses) and validate the ports
    provider_1 = create_hardhat_provider(network_api, network_config_1)
    provider_2 = create_hardhat_provider(network_api, network_config_2)
    provider_3 = create_hardhat_provider(network_api, network_config_3)
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
    assert 4 <= new_time - start_time <= 5


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


def test_double_connect(hardhat_connected):
    # connect has already been called once as part of the fixture, so connecting again should fail
    with pytest.raises(HardhatProviderError):
        hardhat_connected.connect()
