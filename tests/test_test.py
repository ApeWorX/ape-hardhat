import pytest

SIMPLE_TEST = """
def test_provider(networks):
    # The default gets set in `ape-config.yaml`
    assert networks.provider.name == "hardhat"

def test_contract_interaction(accounts, project):
    owner = accounts[0]
    contract = project.TestContractVy.deploy(sender=owner)
    contract.setNumber(123, sender=owner)
    assert contract.myNumber() == 123

def test_transfer(accounts):
    # Useful for seeing transfer gas
    accounts[0].transfer(accounts[1], "100 gwei")
"""
NUM_TESTS = len([x for x in SIMPLE_TEST.split("\n") if x.startswith("def test_")])
EXPECTED_GAS_REPORT = r"""
                    TestContractVy.vy Gas

  Method      Times called    Min.    Max.    Mean   Median
 ───────────────────────────────────────────────────────────
  setNumber              1   51021   51021   51021    51021

                    Transferring ETH Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  to:dev_1              1   21000   21000   21000    21000
"""


@pytest.fixture
def simple_test(pytester):
    pytester.makepyfile(SIMPLE_TEST)


def test_gas_flag_in_tests(project, pytester, simple_test):
    result = pytester.runpytest("--gas")
    result.assert_outcomes(passed=NUM_TESTS), "\n".join(result.outlines)

    gas_header_line_index = None
    for index, line in enumerate(result.outlines):
        if "Gas Profile" in line:
            gas_header_line_index = index

    assert gas_header_line_index is not None, "'Gas Profile' not in output."
    expected = EXPECTED_GAS_REPORT.split("\n")[1:]
    start_index = gas_header_line_index + 1
    end_index = start_index + len(expected)
    actual = [x.rstrip() for x in result.outlines[start_index:end_index]]
    assert len(actual) == len(expected)
    for actual_line, expected_line in zip(actual, expected):
        assert actual_line == expected_line
