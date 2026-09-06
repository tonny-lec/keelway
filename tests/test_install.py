import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", PACKAGE / "install.py")
i = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = i.main(["--target", str(self.root), *args])
        return code, out.getvalue() + err.getvalue()

    def ok(self, *args):
        code, output = self.call(*args)
        self.assertEqual(code, 0, output)
        return output

    def test_fresh_install_and_uninstall(self):
        self.ok()
        self.assertTrue((self.root / i.SKILL / "SKILL.md").is_file())
        self.assertIn(i.BEGIN, (self.root / "AGENTS.md").read_text())
        self.ok("--uninstall")
        self.assertFalse((self.root / i.SKILL / "SKILL.md").exists())
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_preserves_existing_instructions_byte_for_byte(self):
        original = b"# Local convention\r\nUse the existing test runner.\r\n"
        (self.root / "AGENTS.md").write_bytes(original)
        self.ok()
        self.assertTrue((self.root / "AGENTS.md").read_bytes().startswith(original))
        self.ok("--uninstall")
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), original)

    def test_uses_existing_nonempty_override(self):
        (self.root / "AGENTS.override.md").write_text("# Active override\n")
        (self.root / "AGENTS.md").write_text("# Ordinary instructions\n")
        self.ok()
        self.assertIn(i.BEGIN, (self.root / "AGENTS.override.md").read_text())
        self.assertEqual((self.root / "AGENTS.md").read_text(), "# Ordinary instructions\n")
        self.ok("--uninstall")
        self.assertEqual((self.root / "AGENTS.override.md").read_text(), "# Active override\n")

    def test_dry_run_does_not_write(self):
        self.ok("--dry-run")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_collision_is_preflighted(self):
        collision = self.root / i.SKILL / "SKILL.md"
        collision.parent.mkdir(parents=True)
        collision.write_text("unrelated preexisting skill")
        code, output = self.call()
        self.assertEqual(code, 2, output)
        self.assertEqual(collision.read_text(), "unrelated preexisting skill")
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertFalse((self.root / i.SKILL / "scripts/harness.py").exists())

    def test_reinstall_preserves_project_customization(self):
        self.ok()
        config = self.root / ".harness/project.json"
        value = json.loads(config.read_text())
        value["name"] = "customized-service"
        config.write_text(json.dumps(value))
        edited = config.read_bytes()
        self.ok()
        self.assertEqual(config.read_bytes(), edited)
        self.assertEqual((self.root / "AGENTS.md").read_text().count(i.BEGIN), 1)

    def test_existing_project_settings_are_not_owned(self):
        (self.root / ".harness").mkdir()
        original = b'{"custom": true}\n'
        (self.root / ".harness/project.json").write_bytes(original)
        self.ok()
        self.ok("--uninstall")
        self.assertEqual((self.root / ".harness/project.json").read_bytes(), original)

    def test_uninstall_preserves_local_edits_and_evidence(self):
        self.ok()
        skill = self.root / i.SKILL / "SKILL.md"
        edited = skill.read_text() + "\nLocal rule\n"
        skill.write_text(edited)
        evidence = self.root / ".harness/tasks/example/notes.md"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("Local evidence")
        output = self.ok("--uninstall")
        self.assertIn("preserved", output)
        self.assertEqual(skill.read_text(), edited)
        self.assertEqual(evidence.read_text(), "Local evidence")
        self.assertTrue((self.root / ".harness/.gitignore").exists())

    def test_identical_preexisting_file_is_not_deleted(self):
        path = self.root / i.SKILL / "SKILL.md"
        path.parent.mkdir(parents=True)
        original = (PACKAGE / i.SKILL / "SKILL.md").read_bytes()
        path.write_bytes(original)
        self.ok()
        self.ok("--uninstall")
        self.assertEqual(path.read_bytes(), original)

    def test_uninstall_preserves_new_text_outside_managed_block(self):
        self.ok()
        path = self.root / "AGENTS.md"
        path.write_text(path.read_text() + "\nNew local convention\n")
        self.ok("--uninstall")
        self.assertEqual(path.read_text(), "\nNew local convention\n")

    def test_install_rolls_back_completed_writes_on_failure(self):
        original_write = i.raw_write
        calls = 0

        def fail_second(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Simulated write failure")
            return original_write(path, data)

        with mock.patch.object(i, "raw_write", side_effect=fail_second):
            self.assertEqual(self.call()[0], 2)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])

    @unittest.skipIf(os.name == "nt", "Symlink creation may require privileges")
    def test_rejects_symlinked_managed_directory(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / ".agents").symlink_to(outside, target_is_directory=True)
            self.assertEqual(self.call()[0], 2)
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_invalid_manifest_cannot_delete_arbitrary_file(self):
        self.ok()
        path = self.root / ".harness/install.json"
        manifest = json.loads(path.read_text())
        (self.root / "valuable.txt").write_text("keep me")
        manifest["files"]["valuable.txt"] = i.sha(b"keep me")
        path.write_text(json.dumps(manifest))
        self.assertEqual(self.call("--uninstall")[0], 2)
        self.assertEqual((self.root / "valuable.txt").read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
