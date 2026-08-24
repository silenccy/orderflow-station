"""pytest entry point: one test per suite, each in its own process.

    pytest                # everything
    pytest -k reconnect   # one suite

See _runner.py for why they are subprocesses and not plain imports.
"""
import pytest

from _runner import run_suite, suite_names


@pytest.mark.parametrize("suite", suite_names())
def test_suite(suite, tmp_path):
    code, output = run_suite(suite, tmp_path)
    if code != 0:
        pytest.fail("suite %r failed (exit %d)\n%s" % (suite, code, output[-6000:]),
                    pytrace=False)
