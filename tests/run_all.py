"""Run every suite without pytest.

    python tests/run_all.py
    python tests/run_all.py reconnect gaps      # just these
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _runner import run_suite, suite_names  # noqa: E402


def main(argv):
    wanted = argv or suite_names()
    unknown = [n for n in wanted if n not in suite_names()]
    if unknown:
        print("unknown suite(s): %s" % ", ".join(unknown), file=sys.stderr)
        print("available: %s" % ", ".join(suite_names()), file=sys.stderr)
        return 2
    failed = []
    for name in wanted:
        with tempfile.TemporaryDirectory(prefix="orderflow-test-") as tmp:
            code, output = run_suite(name, tmp)
        checks = sum(1 for ln in output.splitlines() if ln.startswith("PASS"))
        if code == 0:
            print("  PASS  %-14s %2d checks" % (name, checks))
        else:
            failed.append(name)
            print("  FAIL  %-14s exit %d" % (name, code))
            print("\n".join("        " + ln for ln in output.strip().splitlines()[-12:]))
    print("\n%d suite(s) passed, %d failed" % (len(wanted) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main([a for a in sys.argv[1:] if not a.startswith("-")]))
