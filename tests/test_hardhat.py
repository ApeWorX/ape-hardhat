import time
from pathlib import Path

import pytest  # type: ignore
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


def test_disconnect(hardhat_provider):
    p = hardhat_provider.process
    assert p
    hardhat_provider.disconnect()
    for i in range(15):
        if hardhat_provider.process is None and p.poll() is not None:
            return True  # this means the process exited
        time.sleep(0.1)
    raise RuntimeError("hardhat process didn't exit in time")


def test_gas_price(hardhat_provider):
    gas_price = hardhat_provider.gas_price
    assert gas_price > 1


def test_uri(hardhat_provider):
    assert f"http://localhost:{hardhat_provider.port}" in hardhat_provider.uri


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


def test_set_block_gas_limit(hardhat_provider):
    gas_limit = hardhat_provider._web3.eth.get_block("latest").gasLimit
    assert hardhat_provider.set_block_gas_limit(gas_limit) is True


def test_sleep(hardhat_provider):
    seconds = 5
    t1 = hardhat_provider.sleep(seconds)
    t2 = hardhat_provider.sleep(seconds)
    assert t2 - t1 == seconds


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
    assert snap == 1
    block1 = hardhat_provider._web3.eth.get_block("latest")
    hardhat_provider.mine()
    block2 = hardhat_provider._web3.eth.get_block("latest")
    assert block1.hash != block2.hash and block2.number > block1.number
    hardhat_provider.revert(snap)
    block3 = hardhat_provider._web3.eth.get_block("latest")
    assert block1.hash == block3.hash and block1.number == block3.number


def test_unlock_account(hardhat_provider):
    assert hardhat_provider.unlock_account(TEST_WALLET_ADDRESS) is True


def test_double_connect(hardhat_provider):
    # connect has already been called once as part of the fixture, so connecting again should fail
    with pytest.raises(RuntimeError):
        hardhat_provider.connect()
