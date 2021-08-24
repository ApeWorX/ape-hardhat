import time
from pathlib import Path

import pytest
from hexbytes import HexBytes

from ape_hardhat import HardhatProvider

TEST_WALLET_ADDRESS = "0xD9b7fdb3FC0A0Aa3A507dCf0976bc23D49a9C7A3"
TEST_CUSTOM_PORT = 8555  # vs. the default of 8545
TEST_CUSTOM_URI = f"http://localhost:{TEST_CUSTOM_PORT}"


def test_instantiation(network_api, network_config):
    h = HardhatProvider("hardhat", network_api, network_config, {}, Path("."), "")
    assert h.name == "hardhat"


def test_connect(hh_provider):
    hh_provider.connect()
    assert hh_provider.chain_id == 31337


def test_disconnect(hh_provider):
    p = hh_provider._process
    assert p
    hh_provider.disconnect()
    for i in range(15):
        if hh_provider._process is None and p.poll() is not None:
            return True  # this means the process exited
        time.sleep(0.1)
    raise RuntimeError("hardhat process didn't exit in time")


def test_gas_price(hh_provider):
    gas_price = hh_provider.gas_price
    assert gas_price > 1


def test_uri(hh_provider):
    assert "http://localhost:8545" in hh_provider.uri  # default hostname & port


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
    assert TEST_CUSTOM_URI in h.uri
    assert h.config.port == TEST_CUSTOM_PORT


def test_set_block_gas_limit(hh_provider):
    gas_limit = hh_provider._web3.eth.get_block("latest").gasLimit
    assert hh_provider.set_block_gas_limit(gas_limit)["result"] is True


def test_sleep(hh_provider):
    seconds = 5
    assert hh_provider.sleep(seconds)["result"] == str(seconds - 1)


def test_mine(hh_provider):
    block1 = hh_provider._web3.eth.get_block("latest")
    assert hh_provider.mine()
    block2 = hh_provider._web3.eth.get_block("latest")
    assert block1.hash != block2.hash and block2.number > block1.number


def test_revert_failure(hh_provider):
    assert hh_provider.revert("0x9999")["result"] is False


def test_snapshot_and_revert(hh_provider):
    snap = hh_provider.snapshot()
    assert snap["result"].startswith("0x")
    block1 = hh_provider._web3.eth.get_block("latest")
    hh_provider.mine()
    block2 = hh_provider._web3.eth.get_block("latest")
    assert block1.hash != block2.hash and block2.number > block1.number
    hh_provider.revert(snap["result"])
    block3 = hh_provider._web3.eth.get_block("latest")
    assert block1.hash == block3.hash and block1.number == block3.number


def test_unlock_account(hh_provider):
    assert hh_provider.unlock_account(TEST_WALLET_ADDRESS)["result"] is True
