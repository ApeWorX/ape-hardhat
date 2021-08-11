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
