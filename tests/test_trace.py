import shutil
from pathlib import Path

import pytest
from ape.contracts import ContractContainer
from ethpm_types import ContractType

from .expected_traces import (
    FAIL_TRACE,
    INTERNAL_TRANSFERS_TXN_0_TRACE,
    INTERNAL_TRANSFERS_TXN_1_TRACE,
    LOCAL_TRACE,
)

FAILED_TXN_HASH = "0x053cba5c12172654d894f66d5670bab6215517a94189a9ffc09bc40a589ec04d"
INTERNAL_TRANSFERS_TXN_HASH_0 = "0xb7d7f1d5ce7743e821d3026647df486f517946ef1342a1ae93c96e4a8016eab7"
INTERNAL_TRANSFERS_TXN_HASH_1 = "0x0537316f37627655b7fe5e50e23f71cd835b377d1cde4226443c94723d036e32"
EXPECTED_MAP = {
    FAILED_TXN_HASH: FAIL_TRACE,
    INTERNAL_TRANSFERS_TXN_HASH_0: INTERNAL_TRANSFERS_TXN_0_TRACE,
    INTERNAL_TRANSFERS_TXN_HASH_1: INTERNAL_TRANSFERS_TXN_1_TRACE,
}
BASE_CONTRACTS_PATH = Path(__file__).parent / "data" / "contracts" / "ethereum"


@pytest.fixture(autouse=True, scope="module")
def full_contracts_cache(config):
    destination = config.DATA_FOLDER / "ethereum"
    shutil.copytree(BASE_CONTRACTS_PATH, destination)


@pytest.fixture(
    params=(FAILED_TXN_HASH, INTERNAL_TRANSFERS_TXN_HASH_0, INTERNAL_TRANSFERS_TXN_HASH_1),
    scope="module",
)
def mainnet_receipt(request, connected_mainnet_fork_provider):
    yield connected_mainnet_fork_provider.get_transaction(request.param)


@pytest.fixture(scope="session")
def contract_a(owner, hardhat_connected):
    base_path = BASE_CONTRACTS_PATH / "local"

    def get_contract_type(suffix: str) -> ContractType:
        return ContractType.parse_raw((base_path / f"contract_{suffix}.json").read_text())

    contract_c = owner.deploy(ContractContainer(get_contract_type("c")))
    contract_b = owner.deploy(ContractContainer(get_contract_type("b")), contract_c.address)
    contract_a = owner.deploy(
        ContractContainer(get_contract_type("a")), contract_b.address, contract_c.address
    )
    return contract_a


@pytest.fixture(scope="session")
def local_receipt(contract_a, owner):
    return contract_a.methodWithoutArguments(sender=owner)


@pytest.fixture
def trace_capture(capsys):
    def get():
        output, _ = capsys.readouterr()
        return [s.strip() for s in output.split("\n")]

    return get


def test_local_transaction_traces(local_receipt, trace_capture, rpc_spy):
    local_receipt.show_trace()
    assert all([x in LOCAL_TRACE for x in trace_capture()])

    # Verify can happen more than once.
    local_receipt.show_trace()
    assert all([x in LOCAL_TRACE for x in trace_capture()])

    # Verify only a single RPC was made.
    rpc_spy.assert_rpc_called("debug_traceTransaction", [local_receipt.txn_hash], num_times=1)


@pytest.mark.fork
def test_mainnet_transaction_traces(mainnet_receipt, trace_capture):
    mainnet_receipt.show_trace()
    assert all([x in EXPECTED_MAP[mainnet_receipt.txn_hash] for x in trace_capture()])
