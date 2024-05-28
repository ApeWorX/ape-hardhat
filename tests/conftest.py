import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp
from typing import Optional

import ape
import pytest
from _pytest.runner import pytest_runtest_makereport as orig_pytest_runtest_makereport
from ape.contracts import ContractContainer
from ape.exceptions import APINotImplementedError, UnknownSnapshotError
from ethpm_types import ContractType

from ape_hardhat import HardhatProvider

# NOTE: Ensure that we don't use local paths for the DATA FOLDER
DATA_FOLDER = Path(mkdtemp()).resolve()
ape.config.DATA_FOLDER = DATA_FOLDER

BASE_CONTRACTS_PATH = Path(__file__).parent / "data" / "contracts"
LOCAL_CONTRACTS_PATH = BASE_CONTRACTS_PATH / "ethereum" / "local"
NAME = "hardhat"

# Needed to test tracing support in core `ape test` command.
pytest_plugins = ["pytester"]
MAINNET_FORK_PORT = 9001
SEPOLIA_FORK_PORT = 9002


def pytest_runtest_makereport(item, call):
    tr = orig_pytest_runtest_makereport(item, call)
    if call.excinfo is not None and "too many requests" in str(call.excinfo).lower():
        tr.outcome = "skipped"
        tr.wasxfail = "reason: Alchemy requests overloaded (likely in CI)"

    return tr


@pytest.fixture(autouse=True, scope="session")
def project():
    return ape.project


@pytest.fixture(scope="session")
def name():
    return NAME


@pytest.fixture(scope="session")
def data_folder():
    return DATA_FOLDER


@contextmanager
def _isolation():
    if ape.networks.active_provider is None:
        raise AssertionError("Isolation should only be used with a connected provider.")

    init_network_name = ape.chain.provider.network.name
    init_provider_name = ape.chain.provider.name

    try:
        snapshot = ape.chain.snapshot()
    except APINotImplementedError:
        # Provider not used or connected in test.
        snapshot = None

    yield

    if (
        snapshot is None
        or ape.networks.active_provider is None
        or ape.chain.provider.network.name != init_network_name
        or ape.chain.provider.name != init_provider_name
    ):
        return

    try:
        ape.chain.restore(snapshot)
    except UnknownSnapshotError:
        # Assume snapshot removed for testing reasons
        # or the provider was not needed to be connected for the test.
        pass


@pytest.fixture(autouse=True)
def main_provider_isolation(connected_provider):
    with _isolation():
        yield


@pytest.fixture(scope="session")
def config():
    return ape.config


@pytest.fixture(scope="session")
def accounts():
    return ape.accounts.test_accounts


@pytest.fixture(scope="session")
def convert():
    return ape.convert


@pytest.fixture(scope="session")
def networks():
    return ape.networks


@pytest.fixture(params=("solidity", "vyper"))
def contract_type(request, get_contract_type) -> ContractType:
    return get_contract_type(f"{request.param}_contract")


@pytest.fixture
def get_contract_type():
    def fn(name: str):
        path = LOCAL_CONTRACTS_PATH / f"{name}.json"
        return ContractType.model_validate_json(path.read_text())

    return fn


@pytest.fixture
def contract_container(contract_type) -> ContractContainer:
    return ContractContainer(contract_type=contract_type)


@pytest.fixture
def contract_instance(owner, contract_container, connected_provider):
    return owner.deploy(contract_container)


@pytest.fixture
def error_contract_container(get_contract_type):
    ct = get_contract_type("has_error")
    return ContractContainer(ct)


@pytest.fixture
def error_contract(owner, error_contract_container):
    return owner.deploy(error_contract_container)


@pytest.fixture(scope="session")
def sender(accounts):
    return accounts[0]


@pytest.fixture(scope="session")
def receiver(accounts):
    return accounts[1]


@pytest.fixture(scope="session")
def owner(accounts):
    return accounts[2]


@pytest.fixture(scope="session")
def not_owner(accounts):
    return accounts[3]


@pytest.fixture(scope="session")
def local_network_api(networks):
    return networks.ethereum.local


@pytest.fixture
def connected_provider(name, networks, local_network_api):
    """
    The main HH local-network (non-fork) instance.
    """
    with networks.ethereum.local.use_provider(name) as provider:
        yield provider


@pytest.fixture(scope="session")
def disconnected_provider(name, local_network_api):
    return HardhatProvider(
        name=name,
        network=local_network_api,
        request_header={},
        data_folder=Path("."),
        provider_settings={},
    )


@pytest.fixture
def mainnet_fork_port():
    return MAINNET_FORK_PORT


@pytest.fixture
def mainnet_fork_provider(name, networks, mainnet_fork_port):
    with networks.ethereum["mainnet-fork"].use_provider(
        name, provider_settings={"host": f"http://127.0.0.1:{mainnet_fork_port}"}
    ) as provider:
        yield provider


@pytest.fixture
def sepolia_fork_port():
    return SEPOLIA_FORK_PORT


@pytest.fixture
def sepolia_fork_provider(name, networks, sepolia_fork_port):
    with networks.ethereum.sepolia_fork.use_provider(
        name, provider_settings={"host": f"http://127.0.0.1:{sepolia_fork_port}"}
    ) as provider:
        yield provider


@pytest.fixture
def contract_a(owner, connected_provider, get_contract_type):
    contract_c = owner.deploy(ContractContainer(get_contract_type("contract_c")))
    contract_b = owner.deploy(
        ContractContainer(get_contract_type("contract_b")), contract_c.address
    )
    contract_a = owner.deploy(
        ContractContainer(get_contract_type("contract_a")), contract_b.address, contract_c.address
    )
    return contract_a


@pytest.fixture
def mock_web3(mocker):
    mock = mocker.MagicMock()
    factory = mocker.patch("ape_hardhat.provider._create_web3")
    factory.return_value = mock
    return mock


@pytest.fixture
def install_detection_fail(mocker):
    cmd = ["list", "hardhat", "--json"]

    def side_effect(cmd_ls):
        if cmd_ls[1:] == cmd:
            # mock
            raise subprocess.CalledProcessError(1, cmd)

        # Run normally.
        return subprocess.check_output(cmd_ls)

    check_output_mock = mocker.patch("ape_hardhat.provider.check_output")
    check_output_mock.side_effect = side_effect
    return check_output_mock


@pytest.fixture
def no_hardhat_bin(project):
    bin_path = project.path / "node_modules" / ".bin" / "hardhat"
    bin_copy = project.path / "node_modules" / ".bin" / "hardhat-2"
    shutil.move(bin_path, bin_copy)

    try:
        yield
    finally:
        shutil.move(bin_copy, bin_path)
