from pathlib import Path

import pytest  # type: ignore
from hexbytes import HexBytes

from ape_hardhat.exceptions import HardhatProviderError
from ape_hardhat.providers import HardhatProvider

from .conftest import get_network_config  # type: ignore

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"


def test_instantiation(network_api, network_config):
    provider = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    assert provider.name == "hardhat"


def test_connect(network_api, network_config):
    provider = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    provider.connect()
    assert provider.chain_id == 31337


def test_disconnect(network_api, network_config):
    # Use custom port to prevent connecting to a port used in another test.
    network_config.port = 8555
    provider = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    provider.connect()
    provider.disconnect()
    assert not provider.process


def test_gas_price(hardhat_provider):
    gas_price = hardhat_provider.gas_price
    assert gas_price > 1


def test_uri(hardhat_provider):
    assert f"http://127.0.0.1:{hardhat_provider.port}" in hardhat_provider.uri


@pytest.mark.parametrize(
    "method,args,expected",
    [
        (HardhatProvider.get_nonce, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_balance, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_code, [TEST_WALLET_ADDRESS], HexBytes("")),
    ],
)
def test_rpc_methods(hardhat_provider, method, args, expected):
    assert method(hardhat_provider, *args) == expected


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
    provider_1 = HardhatProvider("hardhat", network_api, network_config_1, {}, Path("."), "")
    provider_2 = HardhatProvider("hardhat", network_api, network_config_2, {}, Path("."), "")
    provider_3 = HardhatProvider("hardhat", network_api, network_config_3, {}, Path("."), "")
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
    hash_1 = provider_1._web3.eth.get_block("latest").hash
    hash_2 = provider_2._web3.eth.get_block("latest").hash
    hash_3 = provider_3._web3.eth.get_block("latest").hash
    assert hash_1 != hash_2 != hash_3


def test_set_block_gas_limit(hardhat_provider):
    gas_limit = hardhat_provider._web3.eth.get_block("latest").gasLimit
    assert hardhat_provider.set_block_gas_limit(gas_limit) is True


def test_sleep(hardhat_provider):
    seconds = 5
    time_1 = hardhat_provider.sleep(seconds)
    time_2 = hardhat_provider.sleep(seconds)
    assert time_2 - time_1 == seconds


def test_mine(hardhat_provider):
    block1 = hardhat_provider._web3.eth.get_block("latest")
    assert hardhat_provider.mine() == "0x0"
    block2 = hardhat_provider._web3.eth.get_block("latest")
    assert hardhat_provider.mine() == "0x0"
    assert block1.hash != block2.hash and block2.number > block1.number


def test_revert_failure(hardhat_provider):
    assert hardhat_provider.revert(0xFFFF) is False


def test_snapshot_and_revert(hardhat_provider):
    snap = hardhat_provider.snapshot()
    assert snap == "1"

    block_1 = hardhat_provider._web3.eth.get_block("latest")
    hardhat_provider.mine()
    block_2 = hardhat_provider._web3.eth.get_block("latest")
    assert block_2.number > block_1.number
    assert block_1.hash != block_2.hash

    hardhat_provider.revert(snap)
    block_3 = hardhat_provider._web3.eth.get_block("latest")
    assert block_1.number == block_3.number
    assert block_1.hash == block_3.hash


def test_unlock_account(hardhat_provider):
    assert hardhat_provider.unlock_account(TEST_WALLET_ADDRESS) is True


def test_double_connect(hardhat_provider):
    # connect has already been called once as part of the fixture, so connecting again should fail
    with pytest.raises(HardhatProviderError):
        hardhat_provider.connect()
