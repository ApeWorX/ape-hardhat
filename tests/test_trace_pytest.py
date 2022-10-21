from pathlib import Path

import pytest

BASE_DATA_PATH = Path(__file__).parent / "data" / "python"
CONFTEST = (BASE_DATA_PATH / "pytest_test_conftest.py").read_text()
TEST_FILE = (BASE_DATA_PATH / "pytest_tests.py").read_text()
NUM_TESTS = len([x for x in TEST_FILE.split("\n") if x.startswith("def test_")])
EXPECTED_GAS_REPORT = r"""
                   vyper_contract.json Gas

  Method      Times called    Min.    Max.    Mean   Median
 ───────────────────────────────────────────────────────────
  setNumber              3   51021   53821   51958    51033

                    Transferring ETH Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  to:dev_1              1   21000   21000   21000    21000

                      token_a.json Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  transfer              1   50911   50911   50911    50911

                      token_b.json Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  transfer              1   50911   50911   50911    50911
"""


@pytest.fixture
def ape_pytester(project, pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(TEST_FILE)
    return pytester
