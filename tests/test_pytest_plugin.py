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


@pytest.mark.sync
def test_gas_flag_in_tests(ape_pytester, project):
    result = ape_pytester.runpytest("--gas")
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
