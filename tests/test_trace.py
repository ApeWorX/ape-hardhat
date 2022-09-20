import shutil
from pathlib import Path
from typing import List

import pytest
from ape.api import ReceiptAPI
from ape.contracts import ContractContainer
from ethpm_types import ContractType

from .expected_traces import LOCAL_GAS_REPORT, LOCAL_TRACE, MAINNET_FAIL_TRACE, MAINNET_TRACE

MAINNET_FAIL_TXN_HASH = "0x053cba5c12172654d894f66d5670bab6215517a94189a9ffc09bc40a589ec04d"
MAINNET_TXN_HASH = "0xb7d7f1d5ce7743e821d3026647df486f517946ef1342a1ae93c96e4a8016eab7"
EXPECTED_MAP = {
    MAINNET_TXN_HASH: MAINNET_TRACE,
    MAINNET_FAIL_TXN_HASH: MAINNET_FAIL_TRACE,
}
BASE_CONTRACTS_PATH = Path(__file__).parent / "data" / "contracts" / "ethereum"
TEMP_FILE_NAME = "temp"


@pytest.fixture(autouse=True, scope="module")
def full_contracts_cache(config):
    destination = config.DATA_FOLDER / "ethereum"
    shutil.copytree(BASE_CONTRACTS_PATH, destination)


@pytest.fixture
def show_and_get_trace():
    def f(receipt: ReceiptAPI, method="show_trace") -> List[str]:
        with open(TEMP_FILE_NAME, "w+") as temp_file:
            getattr(receipt, method)(file=temp_file)

        with open("temp", "r") as temp_file:
            lines = temp_file.readlines()

        return lines

    return f


@pytest.fixture(
    params=(MAINNET_TXN_HASH, MAINNET_FAIL_TXN_HASH),
    scope="module",
)
def mainnet_receipt(request, networks):
    with networks.parse_network_choice("ethereum:mainnet-fork:hardhat") as provider:
        yield provider.get_receipt(request.param)


@pytest.fixture(scope="session")
def contract_a(owner, connected_provider):
    base_path = BASE_CONTRACTS_PATH / "local"

    def get_contract_type(suffix: str) -> ContractType:
        return ContractType.parse_raw((base_path / f"contract_{suffix}.json").read_text())

    contract_c = owner.deploy(ContractContainer(get_contract_type("c")))
    contract_b = owner.deploy(ContractContainer(get_contract_type("b")), contract_c.address)
    contract_a = owner.deploy(
        ContractContainer(get_contract_type("a")), contract_b.address, contract_c.address
    )
    return contract_a


@pytest.fixture(scope="module")
def local_receipt(contract_a, owner):
    return contract_a.methodWithoutArguments(sender=owner)


@pytest.fixture(autouse=True)
def clean_temp_file():
    temp_path = Path(TEMP_FILE_NAME)
    if temp_path.is_file():
        temp_path.unlink()

    yield

    if temp_path.is_file():
        temp_path.unlink()


def test_local_transaction_traces(local_receipt, show_and_get_trace, clean_temp_file):
    # NOTE: Strange bug in Rich where we can't use sys.stdout for testing tree output.
    # And we have to write to a file, close it, and then re-open it to see output.
    def run_test():
        lines = show_and_get_trace(local_receipt)
        assert_rich_output(lines, LOCAL_TRACE)

    # Verify can happen more than once.
    run_test()
    run_test()


def test_local_transaction_gas_report(local_receipt, show_and_get_trace):
    def run_test():
        lines = show_and_get_trace(local_receipt, method="show_gas_report")
        assert_rich_output(lines, LOCAL_GAS_REPORT)

    # Verify can happen more than once.
    run_test()
    run_test()


@pytest.mark.manual
def test_mainnet_transaction_traces(mainnet_receipt, show_and_get_trace):
    lines = show_and_get_trace(mainnet_receipt)
    assert_rich_output(lines, EXPECTED_MAP[mainnet_receipt.txn_hash])


def assert_rich_output(rich_capture: List[str], expected: str):
    expected_lines = [x.strip() for x in expected.split("\n") if x.strip()]
    actual_lines = [x.strip() for x in rich_capture if x.strip()]
    assert actual_lines, "No output."

    for actual, expected in zip(actual_lines, expected_lines):
        assert actual == expected

    actual_len = len(actual_lines)
    expected_len = len(expected_lines)
    if expected_len > actual_len:
        rest = "\n".join(expected_lines[actual_len:])
        pytest.fail(f"Missing expected lines: {rest}")
