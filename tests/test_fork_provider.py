import os
from pathlib import Path

import pytest
from ape_ethereum.ecosystem import NETWORKS

TESTS_DIRECTORY = Path(__file__).parent


@pytest.fixture(autouse=True, scope="module")
def in_tests_dir():
    curr_dir = str(Path.cwd())
    os.chdir(TESTS_DIRECTORY)
    yield
    os.chdir(curr_dir)


@pytest.mark.parametrize("network", [k for k in NETWORKS.keys()])
def test_connect(network, networks):
    with networks.parse_network_choice(f"ethereum:{network}-fork:hardhat") as provider:
        assert provider.get_block("latest").number > 1
