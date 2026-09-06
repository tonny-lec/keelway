#!/usr/bin/env python3
"""Install/remove the repository-scoped Codex Delivery Harness without dependencies."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parent
SKILL = Path(".agents/skills/delivery-harness")
spec = importlib.util.spec_from_file_location("delivery_harness", PACKAGE / SKILL / "scripts/harness.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
BEGIN = "<!-- BEGIN CODEX DELIVERY HARNESS v1 -->"
END = "<!-- END CODEX DELIVERY HARNESS v1 -->"
BLOCK = f"""{BEGIN}
## Delivery Harness

ソフトウェア開発の作業では `.agents/skills/delivery-harness/SKILL.md` を読み、
目的・リスク・受入条件・証拠を結び付けて進める。既存の実装と適用範囲の指示を先に確認する。
軽微で可逆な作業は短い経路を使い、必要な参照だけを読む。
承認済みの範囲は自律的に完了する。ユーザーの明示指示を優先し、この手順から権限を推測しない。
{END}"""


def raw_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".harness-install-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def payload():
    files = {}
    for path in sorted((PACKAGE / SKILL).rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        h.require(not path.is_symlink(), "Package must not contain symlinks")
        if path.is_file():
            files[path.relative_to(PACKAGE).as_posix()] = path.read_bytes()
    return files


def apply_transaction(root, writes):
    completed = []
    try:
        for relative, before, after in writes:
            path = h.safe_path(root, relative)
            actual = path.read_bytes() if path.exists() else None
            h.require(actual == before, f"Concurrent edit detected: {relative}")
            if after is None:
                path.unlink()
            else:
                raw_write(path, after)
            completed.append((relative, before, after))
    except BaseException:
        for relative, before, after in reversed(completed):
            path = h.safe_path(root, relative)
            actual = path.read_bytes() if path.exists() else None
            if actual == after:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    raw_write(path, before)
        raise


def install(root, dry_run=False):
    manifest_path = h.safe_path(root, ".harness/install.json")
    if manifest_path.exists():
        manifest = h.read_json(manifest_path)
        h.require(manifest.get("version") == h.VERSION, "Installed version differs; review an upgrade separately")
        changes = []
        for relative, expected in manifest.get("files", {}).items():
            path = h.safe_path(root, relative)
            if not path.is_file() or sha(path.read_bytes()) != expected:
                changes.append(relative)
        print(json.dumps({"status": "already-installed", "local_edits_preserved": changes}, ensure_ascii=False, indent=2))
        return
    defaults = {
        ".harness/project.json": (json.dumps(h.default_project(root.name), ensure_ascii=False, indent=2) + "\n").encode(),
        ".harness/context.md": (PACKAGE / SKILL / "assets/context.md").read_bytes(),
        ".harness/.gitignore": b"tasks/\n.install.lock\n",
    }
    writes = []
    owned = {}
    for relative, data in {**payload(), **defaults}.items():
        path = h.safe_path(root, relative)
        if path.exists():
            h.require(path.is_file(), f"Not a regular file: {relative}")
            if relative in defaults:
                continue  # Project-specific settings always belong to the project.
            h.require(path.read_bytes() == data, f"Collision: {relative}; no files changed")
            continue  # An identical pre-existing file is not ours to remove.
        writes.append((relative, None, data))
        owned[relative] = sha(data)
    override = h.safe_path(root, "AGENTS.override.md")
    agents_name = "AGENTS.override.md" if override.is_file() and override.stat().st_size else "AGENTS.md"
    agents = h.safe_path(root, agents_name)
    before = agents.read_bytes() if agents.exists() else None
    existing = (before or b"").decode("utf-8")
    h.require(BEGIN not in existing and END not in existing, "Existing harness marker without manifest; inspect manually")
    newline = "\r\n" if "\r\n" in existing else "\n"
    addition = ((newline * 2 if before else "") + BLOCK.replace("\n", newline) + newline).encode()
    writes.append((agents_name, before, (before or b"") + addition))
    manifest = {"version": h.VERSION, "files": owned, "agents_path": agents_name,
                "agents_addition": addition.decode(), "agents_created": before is None}
    writes.append((".harness/install.json", None, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()))
    print(json.dumps({"status": "preview" if dry_run else "installing", "root": str(root),
                      "files": [p for p, _, _ in writes]}, ensure_ascii=False, indent=2))
    if not dry_run:
        apply_transaction(root, writes)
        print("Installed. Configure .harness/project.json with actual project checks, then run doctor.")


def uninstall(root, dry_run=False):
    manifest_path = h.safe_path(root, ".harness/install.json", exists=True)
    manifest = h.read_json(manifest_path)
    allowed = set(payload()) | {".harness/project.json", ".harness/context.md", ".harness/.gitignore"}
    h.require(isinstance(manifest.get("files"), dict) and set(manifest["files"]) <= allowed, "Invalid install manifest")
    writes, preserved, remaining = [], [], {}
    for relative, expected in manifest["files"].items():
        path = h.safe_path(root, relative)
        if not path.exists():
            continue
        data = path.read_bytes()
        if sha(data) == expected:
            # Keep the ignore file while retained work evidence still exists.
            tasks = h.safe_path(root, ".harness/tasks")
            if relative == ".harness/.gitignore" and tasks.exists() and any(tasks.iterdir()):
                preserved.append(relative)
                continue
            writes.append((relative, data, None))
        else:
            preserved.append(relative)
            remaining[relative] = expected
    name = manifest.get("agents_path")
    h.require(name in {"AGENTS.md", "AGENTS.override.md"}, "Invalid instruction file in manifest")
    agents = h.safe_path(root, name)
    addition = manifest.get("agents_addition", "").encode()
    h.require(addition and BEGIN.encode() in addition and END.encode() in addition, "Invalid instruction addition")
    agents_pending = False
    if agents.exists():
        data = agents.read_bytes()
        if data.count(addition) == 1:
            after = data.replace(addition, b"", 1)
            writes.append((name, data, None if not after and manifest.get("agents_created") else after))
        elif BEGIN.encode() in data or END.encode() in data:
            preserved.append(name)
            agents_pending = True
    if remaining or agents_pending:
        manifest["files"] = remaining
        after = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    else:
        after = None
    writes.append((".harness/install.json", manifest_path.read_bytes(), after))
    print(json.dumps({"status": "preview" if dry_run else "uninstalling", "preserved": preserved,
                      "files": [p for p, _, _ in writes]}, ensure_ascii=False, indent=2))
    if not dry_run:
        apply_transaction(root, writes)
        print("Removed unchanged harness files. Local edits and task evidence are preserved.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Existing repository/component directory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(args.target).resolve()
        h.require(root.is_dir(), "Target must be an existing directory")
        if args.dry_run:
            (uninstall if args.uninstall else install)(root, True)
            return 0
        lock = h.safe_path(root, ".harness/.install.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise h.HarnessError("Another installer owns .harness/.install.lock; inspect it before retrying") from exc
        try:
            os.close(fd)
            (uninstall if args.uninstall else install)(root)
        finally:
            lock.unlink(missing_ok=True)
        return 0
    except (h.HarnessError, OSError, ValueError, TypeError) as exc:
        print(f"install: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
