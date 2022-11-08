from pathlib import Path

import pytest

BASE_DATA_PATH = Path(__file__).parent / "data" / "python"
CONFTEST = (BASE_DATA_PATH / "pytest_test_conftest.py").read_text()
TEST_FILE = (BASE_DATA_PATH / "pytest_tests.py").read_text()
NUM_TESTS = len([x for x in TEST_FILE.split("\n") if x.startswith("def test_")])
TOKEN_B_GAS_REPORT = r"""
                         TokenB Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  transfer              1   50911   50911   50911    50911
"""
EXPECTED_GAS_REPORT = rf"""
                     TestContractVy Gas

  Method      Times called    Min.    Max.    Mean   Median
 ───────────────────────────────────────────────────────────
  setNumber              3   51021   53821   51958    51033
  fooAndBar              1   23430   23430   23430    23430

                         TokenA Gas

  Method     Times called    Min.    Max.    Mean   Median
 ──────────────────────────────────────────────────────────
  transfer              1   50911   50911   50911    50911
{TOKEN_B_GAS_REPORT}
"""


@pytest.fixture
def ape_pytester(project, pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(TEST_FILE)
    return pytester


def run_trace_test(result, expected_report: str = EXPECTED_GAS_REPORT):
    result.assert_outcomes(passed=NUM_TESTS), "\n".join(result.outlines)

    gas_header_line_index = None
    for index, line in enumerate(result.outlines):
        if "Gas Profile" in line:
            gas_header_line_index = index

    assert gas_header_line_index is not None, "'Gas Profile' not in output."
    expected = expected_report.split("\n")[1:]
    start_index = gas_header_line_index + 1
    end_index = start_index + len(expected)
    actual = [x.rstrip() for x in result.outlines[start_index:end_index]]
    assert len(actual) == len(expected)
    for actual_line, expected_line in zip(actual, expected):
        assert actual_line == expected_line


@pytest.mark.sync
def test_gas_flag_in_tests(ape_pytester):
    result = ape_pytester.runpytest("--gas")
    run_trace_test(result)


@pytest.mark.sync
def test_gas_flag_exclude_method_using_cli_option(ape_pytester):
    line = "\n  fooAndBar              1   23430   23430   23430    23430"
    expected = EXPECTED_GAS_REPORT.replace(line, "")
    result = ape_pytester.runpytest("--gas", "--gas-exclude", "*:fooAndBar")
    run_trace_test(result, expected_report=expected)


@pytest.mark.sync
def test_gas_flag_excluding_contracts(ape_pytester):
    result = ape_pytester.runpytest("--gas", "--gas-exclude", "TestContractVy,TokenA")
    run_trace_test(result, expected_report=TOKEN_B_GAS_REPORT)
