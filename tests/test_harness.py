import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("harness", PACKAGE / ".agents/skills/delivery-harness/scripts/harness.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".harness").mkdir()
        self.config = h.default_project("test")
        self.config["checks"] = {"unit": {"argv": ["{python}", "-c", "assert 2 + 2 == 4; print('1 assertion passed')"],
                                           "category": "test", "timeout_seconds": 10}}
        self.save_config()
        (self.root / "source.txt").write_text("original", encoding="utf-8")

    def save_config(self):
        h.atomic_json(self.root / ".harness/project.json", self.config)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = h.main(["--root", str(self.root), *args])
        return code, out.getvalue() + err.getvalue()

    def ok(self, *args):
        code, output = self.call(*args)
        self.assertEqual(code, 0, output)
        return output

    def init(self, kind="bug", risk="medium", *extra):
        self.ok("init", "--id", "case", "--kind", kind, "--risk", risk,
                "--risk-reason", "bounded local behavior", "--title", "Handle input",
                "--goal", "Return the documented result", "--acceptance", "Normal input returns the result", *extra)

    def evidence(self, criterion="A1", path="source.txt", check="unit"):
        args = ["evidence", "--id", "case", "--criterion", criterion, "--path", path,
                "--summary", "Confirmed the result against the criterion"]
        if check:
            args += ["--check", check]
        self.ok(*args)

    def review(self, reviewer="codex-author", basis="self", verdict="pass"):
        self.ok("review", "--id", "case", "--reviewer", reviewer, "--basis", basis,
                "--verdict", verdict, "--path", "source.txt", "--summary", "Checked input and failure behavior")

    def ready(self):
        self.init()
        self.ok("check", "--id", "case")
        self.evidence()
        self.review()
        self.ok("gate", "--id", "case")

    def report(self):
        code, output = self.call("gate", "--id", "case", "--json")
        self.assertIn(code, (0, 1), output)
        return json.loads(output)

    def test_complete_local_workflow(self):
        self.ready()
        self.ok("close", "--id", "case", "--summary", "Delivered locally with evidence")
        completion = h.read_json(self.root / ".harness/tasks/case/completion.json")
        self.assertTrue(completion["gate"]["ready"])

    def test_empty_checks_do_not_pass_executable_work(self):
        self.config["checks"] = {}
        self.save_config()
        self.init()
        self.evidence(check=None)
        self.review()
        self.assertIn("No applicable behavioral", " ".join(self.report()["issues"]))

    def test_document_work_needs_no_invented_tests(self):
        self.config["checks"] = {}
        self.save_config()
        self.init("docs", "low")
        self.evidence(check=None)
        self.assertTrue(self.report()["ready"])

    def test_source_edit_invalidates_and_exact_restore_reuses_evidence(self):
        self.ready()
        source = self.root / "source.txt"
        source.write_text("changed", encoding="utf-8")
        self.assertEqual(self.report()["checks"]["unit"], "stale")
        source.write_text("original", encoding="utf-8")
        self.assertTrue(self.report()["ready"])

    def test_contract_change_invalidates_existing_evidence(self):
        self.ready()
        path = self.root / ".harness/tasks/case/task.json"
        task = h.read_json(path)
        task["acceptance"][0]["statement"] = "Different required behavior"
        h.atomic_json(path, task)
        self.assertFalse(self.report()["ready"])
        self.assertEqual(self.report()["checks"]["unit"], "stale")

    def test_config_change_invalidates_existing_evidence(self):
        self.ready()
        self.config["checks"]["unit"]["timeout_seconds"] = 15
        self.save_config()
        self.assertEqual(self.report()["checks"]["unit"], "stale")

    def test_check_cannot_pass_when_it_changes_source(self):
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", "from pathlib import Path; Path('source.txt').write_text('changed')"]
        self.save_config()
        self.init()
        code, output = self.call("check", "--id", "case")
        self.assertEqual(code, 1)
        self.assertIn("input-changed", output)

    def test_partial_run_does_not_drop_other_required_checks(self):
        self.config["checks"]["static"] = {"argv": ["{python}", "-c", "pass"], "category": "static"}
        self.save_config()
        self.init()
        self.ok("check", "--id", "case", "--only", "unit")
        self.assertEqual(self.report()["checks"]["static"], "missing")

    def test_failed_latest_run_supersedes_a_previous_pass(self):
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", "import os; assert os.environ.get('DELIVERY_TEST_FAIL') != 'yes'"]
        self.save_config()
        self.ready()
        with mock.patch.dict(os.environ, {"DELIVERY_TEST_FAIL": "yes"}):
            self.assertEqual(self.call("check", "--id", "case")[0], 1)
        self.assertEqual(self.report()["checks"]["unit"], "failed")
        self.assertFalse(self.report()["ready"])

    def test_clock_rollback_does_not_reuse_an_older_success(self):
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", "import os; assert os.environ.get('DELIVERY_TEST_FAIL') != 'yes'"]
        self.save_config()
        self.ready()
        with mock.patch.dict(os.environ, {"DELIVERY_TEST_FAIL": "yes"}), mock.patch.object(h.time, "time_ns", return_value=1):
            self.assertEqual(self.call("check", "--id", "case")[0], 1)
        self.assertEqual(self.report()["checks"]["unit"], "failed")

    def test_generated_harness_bytecode_does_not_stale_source(self):
        self.ready()
        cache = self.root / ".agents/skills/delivery-harness/scripts/__pycache__/harness.cpython-312.pyc"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(b"synthetic generated bytecode")
        self.assertTrue(self.report()["ready"])

    def test_missing_executable_is_a_recorded_error(self):
        self.config["checks"]["unit"]["argv"] = ["delivery-harness-nonexistent-tool-87654"]
        self.save_config()
        self.init()
        self.assertEqual(self.call("check", "--id", "case")[0], 1)
        self.assertEqual(self.report()["checks"]["unit"], "error")

    def test_timeout_is_not_success(self):
        self.config["checks"]["unit"].update(argv=["{python}", "-c", "import time; time.sleep(5)"], timeout_seconds=0.1)
        self.save_config()
        self.init()
        code, output = self.call("check", "--id", "case")
        self.assertEqual(code, 1)
        self.assertIn("timeout", output)

    @unittest.skipIf(os.name == "nt", "POSIX process-group check")
    def test_timeout_terminates_descendants(self):
        child = "import time; from pathlib import Path; time.sleep(1); Path('.harness/tasks/case/child-survived').touch()"
        parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(5)"
        self.config["checks"]["unit"].update(argv=["{python}", "-c", parent], timeout_seconds=0.1)
        self.save_config()
        self.init()
        self.assertEqual(self.call("check", "--id", "case")[0], 1)
        time.sleep(1.1)
        self.assertFalse((self.root / ".harness/tasks/case/child-survived").exists())

    def test_log_tail_is_bounded_and_known_secrets_are_redacted(self):
        command = "import os; print('x' * 100000); print(os.environ['DELIVERY_TEST_API_KEY']); print('tail-marker')"
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", command]
        self.save_config()
        self.init()
        with mock.patch.dict(os.environ, {"DELIVERY_TEST_API_KEY": "synthetic-secret-value-for-test"}):
            self.ok("check", "--id", "case")
        record = h.records_for(self.root, "case")[-1]
        self.assertTrue(record["output_truncated"])
        self.assertLessEqual(len(record["log_tail"]), 65536)
        self.assertNotIn("synthetic-secret-value-for-test", record["log_tail"])
        self.assertIn("tail-marker", record["log_tail"])

    def test_shell_metacharacters_remain_literal_arguments(self):
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", "import sys; assert sys.argv[1] == 'a; touch injected'", "a; touch injected"]
        self.save_config()
        self.init()
        self.ok("check", "--id", "case")
        self.assertFalse((self.root / "injected").exists())

    def test_doctor_does_not_execute_project_commands(self):
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", "from pathlib import Path; Path('executed').touch()"]
        self.save_config()
        self.ok("doctor")
        self.assertFalse((self.root / "executed").exists())

    def test_command_preview_does_not_execute(self):
        self.init()
        self.ok("check", "--id", "case", "--list")
        self.assertEqual(h.records_for(self.root, "case"), [])

    def test_inspect_shows_latest_failure_without_rerunning(self):
        self.config["checks"]["unit"]["argv"] = ["{python}", "-c", "raise ValueError('diagnostic marker')"]
        self.save_config()
        self.init()
        code, output = self.call("check", "--id", "case")
        self.assertEqual(code, 1)
        self.assertIn("record:", output)
        records_before = h.records_for(self.root, "case")
        inspected = json.loads(self.ok("inspect", "--id", "case", "--check", "unit"))
        self.assertIn("diagnostic marker", inspected["log_tail"])
        self.assertEqual(h.records_for(self.root, "case"), records_before)

    def test_missing_evidence_file_is_rejected(self):
        self.init()
        code, _ = self.call("evidence", "--id", "case", "--criterion", "A1", "--path", "missing", "--summary", "test")
        self.assertEqual(code, 2)

    def test_referenced_check_must_have_run(self):
        self.init()
        code, _ = self.call("evidence", "--id", "case", "--criterion", "A1", "--path", "source.txt", "--summary", "test", "--check", "unit")
        self.assertEqual(code, 2)

    def test_explicit_task_artifacts_have_their_own_freshness(self):
        self.init()
        note = self.root / ".harness/tasks/case/screenshot.png"
        note.write_bytes(b"synthetic-image-content")
        self.ok("check", "--id", "case")
        self.evidence(path=".harness/tasks/case/screenshot.png")
        self.review()
        self.assertTrue(self.report()["ready"])
        note.write_bytes(b"different-image-content")
        result = self.report()
        self.assertEqual(result["checks"]["unit"], "passed")
        self.assertFalse(result["ready"])

    def test_all_acceptance_criteria_need_evidence(self):
        self.init("bug", "medium", "--acceptance", "An invalid input fails clearly")
        self.ok("check", "--id", "case")
        self.evidence()
        self.review()
        self.assertEqual(self.report()["acceptance"]["A2"], "missing-or-stale")

    def test_migration_cannot_be_downgraded_by_low_label(self):
        self.init("migration", "low")
        report = self.report()
        self.assertEqual(report["risk"], "high")
        self.assertEqual(set(report["required_controls"]), {"recovery", "compatibility", "data-integrity"})

    def test_changed_risk_paths_raise_risk(self):
        self.init("bug", "low")
        (self.root / "migrations").mkdir()
        (self.root / "migrations/001.sql").write_text("alter table accounts add column note text;", encoding="utf-8")
        self.assertEqual(self.report()["risk"], "high")

    def test_high_risk_needs_independence_and_recovery(self):
        self.init("bug", "high")
        self.ok("check", "--id", "case")
        self.evidence()
        self.review()
        self.assertFalse(self.report()["ready"])
        self.ok("control", "--id", "case", "--name", "recovery", "--path", "source.txt", "--summary", "Validated recovery in fixture")
        self.review("separate-reviewer", "independent")
        self.assertTrue(self.report()["ready"])

    def test_author_cannot_self_declare_independence(self):
        self.init()
        code, _ = self.call("review", "--id", "case", "--reviewer", "codex-author", "--basis", "independent", "--verdict", "pass", "--path", "source.txt", "--summary", "review")
        self.assertEqual(code, 2)

    def test_change_request_survives_source_changes_until_disposition(self):
        self.ready()
        self.review("reviewer-b", "independent", "request-changes")
        (self.root / "source.txt").write_text("fixed", encoding="utf-8")
        self.ok("check", "--id", "case")
        self.evidence()
        self.review()
        self.assertIn("review reviewer-b: changes requested", " ".join(self.report()["issues"]))
        self.review("reviewer-b", "independent")
        self.assertTrue(self.report()["ready"])

    def test_blockers_prevent_completion(self):
        self.ready()
        path = self.root / ".harness/tasks/case/task.json"
        task = h.read_json(path)
        task["blockers"] = ["Integration environment unavailable"]
        h.atomic_json(path, task)
        self.assertEqual(self.call("close", "--id", "case", "--summary", "done")[0], 1)
        self.assertFalse((path.parent / "completion.json").exists())

    def test_duplicate_json_keys_fail_closed(self):
        (self.root / ".harness/project.json").write_text('{"schema_version": 1, "name": "x", "checks": {}, "checks": {}}')
        self.assertEqual(self.call("doctor")[0], 2)

    def test_unknown_config_field_fails_closed(self):
        self.config["checkz"] = {}
        self.save_config()
        self.assertEqual(self.call("doctor")[0], 2)

    def test_malformed_record_fails_closed_with_actionable_error(self):
        self.init()
        records = self.root / ".harness/tasks/case/records"
        records.mkdir()
        (records / "corrupt.json").write_text('["not a record"]')
        code, output = self.call("gate", "--id", "case")
        self.assertEqual(code, 2)
        self.assertIn("Malformed evidence record", output)

    @unittest.skipUnless(shutil.which("git"), "Git is optional")
    def test_gitignored_harness_instructions_still_invalidate_evidence(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text(".agents/\n")
        skill = self.root / ".agents/skills/delivery-harness/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Original instruction")
        self.ready()
        skill.write_text("Changed instruction")
        self.assertEqual(self.report()["checks"]["unit"], "stale")

    def test_invalid_working_directory_is_rejected(self):
        self.config["checks"]["unit"]["cwd"] = "../outside"
        self.save_config()
        self.assertEqual(self.call("doctor")[0], 2)

    def test_task_path_traversal_is_rejected(self):
        self.assertEqual(self.call("gate", "--id", "../escape")[0], 2)

    def test_evidence_path_traversal_is_rejected(self):
        self.init()
        self.assertEqual(self.call("evidence", "--id", "case", "--criterion", "A1", "--summary", "test", "--path", "../escape")[0], 2)

    def test_task_lock_prevents_concurrent_writes(self):
        self.init()
        with h.locked(self.root, "case"):
            self.assertEqual(self.call("gate", "--id", "case")[0], 2)
        self.assertEqual(self.call("gate", "--id", "case")[0], 1)

    def test_snapshot_limit_is_enforced(self):
        self.config["max_snapshot_bytes"] = 1
        self.save_config()
        self.assertEqual(self.call("doctor")[0], 2)

    @unittest.skipIf(os.name == "nt", "Symlink creation may need privileges on Windows")
    def test_symlink_evidence_is_rejected(self):
        self.init()
        link = self.root / ".harness/tasks/case/link"
        link.symlink_to(self.root / "source.txt")
        self.assertEqual(self.call("evidence", "--id", "case", "--criterion", "A1", "--summary", "test", "--path", ".harness/tasks/case/link")[0], 2)

    @unittest.skipUnless(shutil.which("git"), "Git is optional")
    def test_git_tracks_preexisting_dirty_sensitive_changes(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Harness Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "baseline"], check=True)
        (self.root / "auth").mkdir()
        (self.root / "auth/login.py").write_text("# fixture authentication change\n")
        self.init("bug", "low")
        self.assertEqual(self.report()["risk"], "high")

    @unittest.skipUnless(shutil.which("git"), "Git is optional")
    def test_git_worktree_untracked_inputs_are_fingerprinted(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.ready()
        (self.root / "new-source.txt").write_text("new input")
        self.assertEqual(self.report()["checks"]["unit"], "stale")


if __name__ == "__main__":
    unittest.main()
