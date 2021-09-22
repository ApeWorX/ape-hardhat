import time
from pathlib import Path

import pytest
from conftest import get_network_config
from hexbytes import HexBytes

from ape_hardhat import HardhatProvider

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"
TEST_CUSTOM_PORT = 8555  # vs. Hardhat's default of 8545
TEST_CUSTOM_URI = f"http://localhost:{TEST_CUSTOM_PORT}"


def test_instantiation(network_api, network_config):
    h = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    assert h.name == "hardhat"


def test_connect(network_api, network_config):
    h = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    h.connect()
    assert h.chain_id == 31337


def test_disconnect(hh_provider):
    p = hh_provider.process
    assert p
    hh_provider.disconnect()
    for i in range(15):
        if hh_provider.process is None and p.poll() is not None:
            return True  # this means the process exited
        time.sleep(0.1)
    raise RuntimeError("hardhat process didn't exit in time")


def test_gas_price(hh_provider):
    gas_price = hh_provider.gas_price
    assert gas_price > 1


def test_uri(hh_provider):
    assert f"http://localhost:{hh_provider.port}" in hh_provider.uri


@pytest.mark.parametrize(
    "method,args,expected",
    [
        (HardhatProvider.get_nonce, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_balance, [TEST_WALLET_ADDRESS], 0),
        (HardhatProvider.get_code, [TEST_WALLET_ADDRESS], HexBytes("")),
    ],
)
def test_rpc_methods(hh_provider, method, args, expected):
    assert method(hh_provider, *args) == expected


def test_custom_port(network_api, network_config):
    network_config.port = TEST_CUSTOM_PORT
    network_config.ethereum.development["uri"] = TEST_CUSTOM_URI
    h = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    h.connect()
    assert TEST_CUSTOM_URI in h.uri
    assert h.config.port == TEST_CUSTOM_PORT


def test_two_hardhat_instances(network_api):
    """
    Validate the somewhat tricky internal logic of running two Hardhat subprocesses
    under a single parent process.
    """
    # configure the first provider with a custom port
    nc1 = get_network_config()
    nc1.port = TEST_CUSTOM_PORT
    nc1.ethereum.development["uri"] = TEST_CUSTOM_URI

    # configure the second provider with the default port
    nc2 = get_network_config()

    # instantiate the providers (which will start the subprocesses) and validate the ports
    h1 = HardhatProvider("hardhat", network_api, nc1, {}, Path("."), "")
    h2 = HardhatProvider("hardhat", network_api, nc2, {}, Path("."), "")
    h1.connect()
    h2.connect()

    # validate the two instances, subprocesses, and blockchains are independent of each other
    assert TEST_CUSTOM_URI in h1.uri
    assert h1.config.port == h1.port == TEST_CUSTOM_PORT
    # the web3 clients must be different in the HH provider instances (compared to the
    # behavior of the EthereumProvider base class, where it's a shared classvar)
    assert h1._web3 != h2._web3
    assert h2.port > h1.port  # h2 will have a higher port number in the ephemeral port range
    h1.mine()
    h2.mine()
    hash1 = h1._web3.eth.get_block("latest").hash
    hash2 = h2._web3.eth.get_block("latest").hash
    assert hash1 != hash2


def test_set_block_gas_limit(hh_provider):
    gas_limit = hh_provider._web3.eth.get_block("latest").gasLimit
    assert hh_provider.set_block_gas_limit(gas_limit) is True


def test_sleep(hh_provider):
    seconds = 5
    t1 = hh_provider.sleep(seconds)
    t2 = hh_provider.sleep(seconds)
    assert t2 - t1 == seconds


def test_mine(hh_provider):
    block1 = hh_provider._web3.eth.get_block("latest")
    assert hh_provider.mine() == "0x0"
    block2 = hh_provider._web3.eth.get_block("latest")
    assert hh_provider.mine() == "0x0"
    assert block1.hash != block2.hash and block2.number > block1.number


def test_revert_failure(hh_provider):
    assert hh_provider.revert(0xFFFF) is False


def test_snapshot_and_revert(hh_provider):
    snap = hh_provider.snapshot()
    assert snap == 1
    block1 = hh_provider._web3.eth.get_block("latest")
    hh_provider.mine()
    block2 = hh_provider._web3.eth.get_block("latest")
    assert block1.hash != block2.hash and block2.number > block1.number
    hh_provider.revert(snap)
    block3 = hh_provider._web3.eth.get_block("latest")
    assert block1.hash == block3.hash and block1.number == block3.number


def test_unlock_account(hh_provider):
    assert hh_provider.unlock_account(TEST_WALLET_ADDRESS) is True
