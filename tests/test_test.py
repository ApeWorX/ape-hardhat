import pytest

CONFTEST = """
import pytest


@pytest.fixture(scope="session")
def owner(accounts):
    return accounts[0]
"""
TEST_FILE = """
def test_provider(networks):
    # The default gets set in `ape-config.yaml`
    assert networks.provider.name == "hardhat"

def test_contract_interaction(owner, project):
    contract = project.TestContractVy.deploy(sender=owner)
    contract.setNumber(123, sender=owner)
    assert contract.myNumber() == 123

def test_transfer(accounts):
    # Useful for seeing transfer gas
    accounts[0].transfer(accounts[1], "100 gwei")

def test_using_contract_with_same_name(owner, project):
    contract = project.TestContractVy.deploy(sender=owner)
    contract.setNumber(123, sender=owner)
    assert contract.myNumber() == 123

def test_two_contracts_with_same_symbol(owner, accounts, project):
    # Tests against scenario when using 2 tokens with same symbol.
    # There was almost a bug where the contract IDs clashed.
    # This is to help prevent future bugs related to this.

    receiver = accounts[1]
    token_a = project.TokenA.deploy(sender=owner)
    token_b = project.TokenB.deploy(sender=owner)
    token_a.transfer(receiver, 5, sender=owner)
    token_b.transfer(receiver, 6, sender=owner)
    assert token_a.balanceOf(receiver) == 5
    assert token_b.balanceOf(receiver) == 6
"""
NUM_TESTS = len([x for x in TEST_FILE.split("\n") if x.startswith("def test_")])
EXPECTED_GAS_REPORT = r"""
                   vyper_contract.json Gas

  Method      Times called    Min.    Max.    Mean   Median
 ───────────────────────────────────────────────────────────
  setNumber              2   51021   51021   51021    51021

                    Transferring ETH Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  to:dev_1              1   21000   21000   21000    21000

                       TokenA.vy Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  transfer              1   50911   50911   50911    50911

                       TokenB.vy Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  transfer              1   50911   50911   50911    50911
"""


@pytest.fixture
def ape_pytester(project, pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(TEST_FILE)
    return pytester


# def test_gas_flag_in_tests(ape_pytester):
#     result = ape_pytester.runpytest("--gas")
#     result.assert_outcomes(passed=NUM_TESTS), "\n".join(result.outlines)
#
#     gas_header_line_index = None
#     for index, line in enumerate(result.outlines):
#         if "Gas Profile" in line:
#             gas_header_line_index = index
#
#     assert gas_header_line_index is not None, "'Gas Profile' not in output."
#     expected = EXPECTED_GAS_REPORT.split("\n")[1:]
#     start_index = gas_header_line_index + 1
#     end_index = start_index + len(expected)
#     actual = [x.rstrip() for x in result.outlines[start_index:end_index]]
#     assert len(actual) == len(expected)
#     for actual_line, expected_line in zip(actual, expected):
#         assert actual_line == expected_line
