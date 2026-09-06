#!/usr/bin/env python3
"""Exercise failed check -> fix -> READY -> stale evidence in an isolated fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]
BROKEN = '''def retry(operation, attempts):
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    for _ in range(attempts + 1):
        try:
            return operation()
        except OSError:
            pass
    raise RuntimeError("attempts exhausted")
'''
FIXED = BROKEN.replace("range(attempts + 1)", "range(attempts)")
TESTS = '''import unittest
from retry import retry

class RetryTests(unittest.TestCase):
    def test_exact_attempt_limit(self):
        calls = []
        def fail():
            calls.append(1)
            raise OSError("temporary")
        with self.assertRaises(RuntimeError):
            retry(fail, 3)
        self.assertEqual(len(calls), 3)

    def test_success_returns_without_more_calls(self):
        calls = []
        def succeed():
            calls.append(1)
            return "result"
        self.assertEqual(retry(succeed, 3), "result")
        self.assertEqual(len(calls), 1)

    def test_invalid_limit(self):
        with self.assertRaises(ValueError):
            retry(lambda: None, 0)
'''


def run_demo(target):
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("Use an empty fixture directory; existing work is preserved")
    target.mkdir(parents=True, exist_ok=True)
    (target / "tests").mkdir(exist_ok=True)
    (target / "retry.py").write_text(BROKEN, encoding="utf-8")
    (target / "tests/test_retry.py").write_text(TESTS, encoding="utf-8")
    (target / "AGENTS.md").write_text("# Fixture convention\nKeep public function signatures stable.\n", encoding="utf-8")

    def command(argv, expected=0):
        result = subprocess.run(argv, cwd=target, capture_output=True, text=True, timeout=30)
        if result.returncode != expected:
            raise RuntimeError(f"Unexpected exit {result.returncode}: {result.stdout}\n{result.stderr}")
        return result.stdout

    command([sys.executable, str(PACKAGE / "install.py"), "--target", str(target)])
    runner = [sys.executable, str(target / ".agents/skills/delivery-harness/scripts/harness.py")]
    config_path = target / ".harness/project.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["checks"] = {"unit": {"argv": ["{python}", "-m", "unittest", "discover", "-s", "tests", "-v"],
                                 "category": "test", "timeout_seconds": 20}}
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    command(runner + ["doctor"])
    command(runner + ["init", "--id", "retry-bound", "--kind", "bug", "--risk", "medium",
                      "--risk-reason", "Local retry-count behavior with no production side effect",
                      "--title", "Honor retry limit", "--goal", "Bound dependency calls",
                      "--acceptance", "Failing operations execute exactly attempts times",
                      "--acceptance", "Success returns immediately and invalid limits are rejected"])
    before = command(runner + ["check", "--id", "retry-bound"], expected=1)
    (target / "retry.py").write_text(FIXED, encoding="utf-8")
    after = command(runner + ["check", "--id", "retry-bound"])
    for criterion in ("A1", "A2"):
        command(runner + ["evidence", "--id", "retry-bound", "--criterion", criterion,
                          "--check", "unit", "--path", "tests/test_retry.py",
                          "--summary", "Three fixture tests exercised attempt limit, success and invalid input"])
    note = target / ".harness/tasks/retry-bound/review.md"
    note.write_text("Synthetic demo review record: the single loop-bound change is covered by three tests.\n"
                    "This exercises record mechanics and does not claim an independent review.\n", encoding="utf-8")
    command(runner + ["review", "--id", "retry-bound", "--reviewer", "codex-author", "--basis", "self",
                      "--verdict", "pass", "--path", ".harness/tasks/retry-bound/review.md",
                      "--summary", "Synthetic example of recording a local self review"])
    ready = json.loads(command(runner + ["gate", "--id", "retry-bound", "--json"]))
    command(runner + ["close", "--id", "retry-bound", "--summary", "Fixture fix validated"])
    (target / "retry.py").write_text(BROKEN, encoding="utf-8")
    stale = json.loads(command(runner + ["gate", "--id", "retry-bound", "--json"], expected=1))
    (target / "retry.py").write_text(FIXED, encoding="utf-8")
    command(runner + ["gate", "--id", "retry-bound"])
    assert ready["ready"] and stale["checks"]["unit"] == "stale"
    return {"fixture": str(target), "before_fix": before.strip(), "after_fix": after.strip(),
            "ready_after_evidence": ready["ready"], "source_edit_invalidates_evidence": not stale["ready"],
            "fixture_test_count": 3, "final_source": "fixed", "external_actions": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    print(json.dumps(run_demo(args.target), ensure_ascii=False, indent=2))
