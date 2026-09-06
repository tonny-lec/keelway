#!/usr/bin/env python3
"""Local delivery evidence runner. Python 3.10+, standard library only.

This is a workflow aid, not a sandbox, an authorization service, or an attestation
authority. Configured commands run with the caller's existing permissions.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

VERSION = "1.0.0"
RISKS = ("low", "medium", "high", "critical")
KINDS = ("planning", "research", "design", "feature", "bug", "refactor", "docs",
         "review", "test", "release", "incident", "migration", "security",
         "performance", "data", "ml", "platform", "decommission")
EXECUTABLE_KINDS = {"feature", "bug", "refactor", "test", "migration", "security",
                    "performance", "data", "ml", "platform"}
SIGNALS = {"auth": "high", "payments": "high", "schema": "high",
           "public-api": "high", "sensitive-data": "high", "production": "high",
           "irreversible": "critical", "wide-blast-radius": "critical"}
CONTROLS = ("recovery", "observability", "compatibility", "security", "data-integrity")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".next", "dist", "build", "coverage"}
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")


class HarnessError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise HarnessError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def safe_path(root: Path, relative: str, *, exists=False):
    """Reject parent traversal and any symlink component before reading/writing."""
    require(isinstance(relative, str) and relative and "\\" not in relative,
            "Use a nonempty repository-relative path with forward slashes")
    p = PurePosixPath(relative)
    require(not p.is_absolute() and ".." not in p.parts and ":" not in relative,
            f"Path must stay inside the repository: {relative}")
    candidate = root
    for part in p.parts:
        candidate = candidate / part
        require(not candidate.is_symlink(), f"Symlink path is not supported: {relative}")
    require(candidate.resolve().is_relative_to(root.resolve()), "Path escapes repository")
    if exists:
        require(candidate.exists(), f"Missing path: {relative}")
    return candidate


def read_json(path):
    try:
        # Duplicate keys otherwise silently replace gate requirements.
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, f"Duplicate JSON key: {key}")
                result[key] = value
            return result
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, ValueError) as exc:
        raise HarnessError(f"Cannot read JSON {path.name}: {exc}") from exc


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".harness-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def default_project(name="project"):
    return {
        "schema_version": 1, "name": name, "checks": {},
        "fingerprint_exclude": [],
        "max_snapshot_bytes": 536870912,
        "risk_rules": [
            {"patterns": ["migrations/**", "**/migrations/**"], "signal": "schema"},
            {"patterns": ["auth/**", "**/auth/**"], "signal": "auth"},
            {"patterns": ["payments/**", "**/payments/**"], "signal": "payments"},
            {"patterns": [".github/workflows/**", "terraform/**"], "signal": "production"}
        ]
    }


def validate_config(config):
    require(isinstance(config, dict) and config.get("schema_version") == 1,
            "project.json requires schema_version: 1")
    allowed = {"schema_version", "name", "checks", "fingerprint_exclude",
               "max_snapshot_bytes", "risk_rules"}
    require(not set(config) - allowed, f"Unknown project keys: {set(config) - allowed}")
    require(nonempty(config.get("name")), "Project name is required")
    require(isinstance(config.get("checks"), dict), "checks must be an object")
    for name, check in config["checks"].items():
        require(bool(IDENTIFIER.fullmatch(name)), f"Invalid check id: {name}")
        require(isinstance(check, dict), f"Invalid check: {name}")
        require(not set(check) - {"argv", "cwd", "timeout_seconds", "category", "risks", "kinds"},
                f"Unknown fields in check: {name}")
        argv = check.get("argv")
        require(isinstance(argv, list) and argv and all(nonempty(x) for x in argv),
                f"{name}.argv must be a nonempty string array")
        require(check.get("category") in {"test", "static", "build", "other"},
                f"{name}.category must be test/static/build/other")
        seconds = check.get("timeout_seconds", 120)
        require(type(seconds) in (int, float) and 0 < seconds <= 3600,
                f"{name}.timeout_seconds must be between 0 and 3600")
        for field, valid, default in (("risks", RISKS, list(RISKS)), ("kinds", KINDS, list(KINDS))):
            values = check.get(field, default)
            require(isinstance(values, list) and values and all(x in valid for x in values),
                    f"Invalid {name}.{field}")
        require(nonempty(check.get("cwd", ".")), f"Invalid {name}.cwd")
    excludes = config.get("fingerprint_exclude", [])
    require(isinstance(excludes, list) and all(nonempty(x) for x in excludes),
            "fingerprint_exclude must be a string array")
    limit = config.get("max_snapshot_bytes", 536870912)
    require(type(limit) is int and limit > 0, "max_snapshot_bytes must be positive")
    rules = config.get("risk_rules", [])
    require(isinstance(rules, list), "risk_rules must be an array")
    for rule in rules:
        require(isinstance(rule, dict) and set(rule) == {"patterns", "signal"}, "Invalid risk rule")
        require(rule["signal"] in SIGNALS, "Unknown risk signal")
        require(isinstance(rule["patterns"], list) and rule["patterns"] and
                all(nonempty(x) for x in rule["patterns"]), "Invalid risk patterns")
    return config


def config_for(root):
    config = validate_config(read_json(safe_path(root, ".harness/project.json", exists=True)))
    for name, check in config["checks"].items():
        require(safe_path(root, check.get("cwd", "."), exists=True).is_dir(),
                f"Check cwd is not a directory: {name}")
    return config


def git_result(root, args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              timeout=15, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def excluded(name, config, *, tracked=False):
    # The task ledger is excluded from its own snapshot; explicit evidence files
    # are hashed separately. Config and instruction files can never be excluded.
    if name.startswith(".harness/tasks/"):
        return True
    if not tracked and any(p in SKIP_DIRS for p in PurePosixPath(name).parts):
        return True
    if name in {"AGENTS.md", "AGENTS.override.md", ".harness/project.json"} or name.startswith(".agents/"):
        return False
    return any(fnmatch.fnmatchcase(name, pat) for pat in config.get("fingerprint_exclude", []))


def file_digest(path):
    require(path.is_file(), f"Evidence must be a regular file: {path}")
    require(stat.S_ISREG(path.stat().st_mode), f"Special file is unsupported: {path}")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def snapshot(root, config):
    tracked = set()
    probe = git_result(root, ["rev-parse", "--show-toplevel"])
    is_git = bool(probe and probe.returncode == 0)
    names = set()
    if is_git:
        for args, is_tracked in ((["ls-files", "-z", "--cached"], True),
                                 (["ls-files", "-z", "--others", "--exclude-standard"], False)):
            result = git_result(root, args)
            require(result is not None and result.returncode == 0, "Cannot enumerate Git inputs")
            found = {os.fsdecode(x) for x in result.stdout.split(b"\0") if x}
            names.update(found)
            if is_tracked:
                tracked.update(found)
    else:
        for current, directories, files in os.walk(root, followlinks=False):
            rel = Path(current).relative_to(root)
            kept = []
            for directory in directories:
                name = (rel / directory).as_posix()
                if not excluded(name + "/", config):
                    require(not (Path(current) / directory).is_symlink(), f"Directory symlink: {name}")
                    kept.append(directory)
            directories[:] = kept
            names.update((rel / name).as_posix() for name in files)
    # Even gitignored policy inputs must participate in evidence freshness.
    for name in (".harness/project.json", "AGENTS.md", "AGENTS.override.md"):
        if (root / name).exists():
            names.add(name)
    skill = safe_path(root, ".agents/skills/delivery-harness")
    if skill.exists():
        for current, directories, skill_files in os.walk(skill, followlinks=False):
            directories[:] = [d for d in directories if d != "__pycache__"]
            for directory in directories:
                safe_path(root, (Path(current) / directory).relative_to(root).as_posix())
            names.update((Path(current) / name).relative_to(root).as_posix()
                         for name in skill_files if not name.endswith(".pyc"))
    files = {}
    size = 0
    for name in sorted(names):
        if excluded(name, config, tracked=name in tracked):
            continue
        path = safe_path(root, name)
        if not path.exists():
            files[name] = "deleted"
            continue
        require(path.is_file() and stat.S_ISREG(path.stat().st_mode),
                f"Unsupported input (including submodule directories): {name}")
        size += path.stat().st_size
        require(size <= config.get("max_snapshot_bytes", 536870912),
                "Snapshot byte limit exceeded; scope to a component or exclude generated inputs explicitly")
        files[name] = file_digest(path) + ":" + str(path.stat().st_mode & 0o111)
    return {"digest": digest(files), "files": files, "bytes": size, "mode": "git" if is_git else "filesystem"}


def task_dir(root, task_id):
    require(bool(IDENTIFIER.fullmatch(task_id)), "Task id: lowercase letters, digits and hyphens, max 64")
    return safe_path(root, f".harness/tasks/{task_id}")


def read_task(root, task_id):
    path = safe_path(root, f".harness/tasks/{task_id}/task.json", exists=True)
    task = read_json(path)
    require(isinstance(task, dict) and task.get("schema_version") == 1 and task.get("id") == task_id,
            "Invalid task contract")
    require(task.get("kind") in KINDS and task.get("risk") in RISKS, "Invalid kind/risk")
    for key in ("title", "goal", "owner", "risk_reason"):
        require(nonempty(task.get(key)), f"Task requires {key}")
    require(isinstance(task.get("signals"), list) and all(x in SIGNALS for x in task["signals"]),
            "Invalid task signals")
    acceptance = task.get("acceptance")
    require(isinstance(acceptance, list) and acceptance, "At least one acceptance criterion is required")
    ids = set()
    for item in acceptance:
        require(isinstance(item, dict) and nonempty(item.get("id")) and nonempty(item.get("statement")),
                "Acceptance requires id and statement")
        require(item["id"] not in ids, "Duplicate acceptance id")
        ids.add(item["id"])
    require(isinstance(task.get("blockers"), list) and all(nonempty(x) for x in task["blockers"]),
            "blockers must be a string array")
    return task


@contextmanager
def locked(root, task_id):
    folder = task_dir(root, task_id)
    require(folder.is_dir(), "Task does not exist; run init first")
    lock = safe_path(root, f".harness/tasks/{task_id}/.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise HarnessError("Task is busy. If the owning process has ended, remove its .lock file manually") from exc
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def risk_context(root, config, task, snap):
    baseline = read_json(safe_path(root, f".harness/tasks/{task['id']}/baseline.json", exists=True))
    require(isinstance(baseline, dict) and isinstance(baseline.get("files"), dict), "Invalid baseline")
    changed = {p for p in set(baseline["files"]) | set(snap["files"])
               if baseline["files"].get(p) != snap["files"].get(p)}
    existing = git_result(root, ["diff", "HEAD", "--name-only", "-z", "--", "."])
    if existing and existing.returncode == 0:
        changed.update(os.fsdecode(x) for x in existing.stdout.split(b"\0") if x)
    untracked = git_result(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if untracked and untracked.returncode == 0:
        changed.update(os.fsdecode(x) for x in untracked.stdout.split(b"\0") if x)
    signals = set(task["signals"])
    # High-consequence work stays high even if the initial task label is low.
    if task["kind"] == "migration":
        signals.add("schema")
    if task["kind"] in {"release", "incident", "decommission"}:
        signals.add("production")
    for rule in config.get("risk_rules", []):
        if any(fnmatch.fnmatchcase(p, pattern) for p in changed for pattern in rule["patterns"]):
            signals.add(rule["signal"])
    rank = max([RISKS.index(task["risk"])] + [RISKS.index(SIGNALS[s]) for s in signals])
    risk = RISKS[rank]
    controls = set()
    if rank >= 2:
        controls.add("recovery")
    if "production" in signals or rank == 3:
        controls.add("observability")
    if "schema" in signals:
        controls.update({"compatibility", "data-integrity"})
    if "public-api" in signals:
        controls.add("compatibility")
    if {"auth", "sensitive-data", "payments"} & signals or rank == 3:
        controls.add("security")
    if "payments" in signals:
        controls.add("data-integrity")
    required_checks = sorted(name for name, check in config["checks"].items()
                             if risk in check.get("risks", RISKS) and task["kind"] in check.get("kinds", KINDS))
    return {"risk": risk, "signals": sorted(signals), "controls": sorted(controls),
            "required_checks": required_checks, "changed_paths": sorted(changed)}


def seal(task, snap, context):
    return {"contract": digest(task), "snapshot": snap["digest"],
            "runner": file_digest(Path(__file__)),
            "requirements": digest({k: v for k, v in context.items() if k != "changed_paths"})}


def current_state(root, config, task):
    snap = snapshot(root, config)
    context = risk_context(root, config, task, snap)
    return snap, context, seal(task, snap, context)


def append_record(root, task_id, record):
    record = {"schema_version": 1, "at": utc_now(), **record}
    folder = safe_path(root, f".harness/tasks/{task_id}/records")
    # Writers hold the task lock. Preserve event order if the system clock moves backwards.
    previous = [int(p.name.split("-", 1)[0]) for p in folder.glob("*.json")
                if p.name.split("-", 1)[0].isdigit()] if folder.exists() else []
    sequence = max(time.time_ns(), max(previous, default=0) + 1)
    name = f"{sequence:020d}-{uuid.uuid4().hex}.json"
    record["record_path"] = f".harness/tasks/{task_id}/records/{name}"
    path = safe_path(root, record["record_path"])
    atomic_json(path, record)
    return record


def records_for(root, task_id):
    folder = safe_path(root, f".harness/tasks/{task_id}/records")
    if not folder.exists():
        return []
    records = []
    for path in sorted(folder.glob("*.json")):
        record = read_json(safe_path(root, path.relative_to(root).as_posix(), exists=True))
        require(isinstance(record, dict) and record.get("schema_version") == 1
                and isinstance(record.get("seal"), dict), f"Malformed evidence record: {path.name}")
        kind = record.get("type")
        require(kind in {"check", "review", "control", "evidence"}, "Unknown record type")
        if kind == "check":
            require(nonempty(record.get("check")) and record.get("status") in
                    {"passed", "failed", "timeout", "error", "input-changed"}, "Malformed check record")
        else:
            require(nonempty(record.get("summary")) and isinstance(record.get("refs"), dict), "Malformed evidence")
        if kind == "review":
            require(nonempty(record.get("reviewer")) and record.get("basis") in {"self", "independent"}
                    and record.get("verdict") in {"pass", "request-changes"}, "Malformed review")
        records.append(record)
    return records


def evidence_refs(root, paths):
    require(paths, "At least one local evidence path is required")
    return {name: file_digest(safe_path(root, name, exists=True)) for name in paths}


def references_current(root, refs):
    if not isinstance(refs, dict) or not refs:
        return False
    try:
        return all(file_digest(safe_path(root, name, exists=True)) == sha for name, sha in refs.items())
    except (HarnessError, OSError):
        return False


def redact(value):
    for key, secret in os.environ.items():
        if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL", key, re.I) and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    value = re.sub(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,})\b", "[REDACTED]", value)
    return value


def execute_check(root, definition):
    """Capture a bounded output tail; terminate descendants on timeout."""
    argv = [sys.executable if x == "{python}" else x for x in definition["argv"]]
    cwd = safe_path(root, definition.get("cwd", "."), exists=True)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=(os.name != "nt"))
    except OSError as exc:
        return {"status": "error", "exit_code": None, "seconds": 0,
                "log_tail": redact(str(exc)), "output_sha256": None}
    tail = deque()
    output_hash = hashlib.sha256()
    output_size = 0

    def drain():
        nonlocal output_size
        while True:
            block = proc.stdout.read(4096)
            if not block:
                break
            output_hash.update(block)
            output_size += len(block)
            tail.append(block)
            while sum(map(len, tail)) > 65536:
                tail.popleft()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    def terminate_tree():
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
            if proc.poll() is None:
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    status = "failed"
    try:
        proc.wait(timeout=definition.get("timeout_seconds", 120))
        reader.join(timeout=1)
        if reader.is_alive():
            # A background descendant retaining stdout is still part of the check.
            terminate_tree()
            status = "error"
        else:
            status = "passed" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        terminate_tree()
        status = "timeout"
    except BaseException:
        terminate_tree()
        proc.wait(timeout=10)
        raise
    finally:
        proc.wait(timeout=10)
        reader.join(timeout=10)
    require(not reader.is_alive(), "Check output process did not terminate")
    proc.stdout.close()
    return {"status": status, "exit_code": proc.returncode,
            "seconds": round(time.monotonic() - started, 3),
            "log_tail": redact(b"".join(tail).decode("utf-8", errors="replace")),
            "output_sha256": output_hash.hexdigest(), "output_bytes": output_size,
            "output_truncated": output_size > 65536}


def assess(root, config, task):
    snap, context, current = current_state(root, config, task)
    records = records_for(root, task["id"])
    latest_checks = {}
    latest_reviews = {}
    for record in records:
        if record.get("type") == "check":
            latest_checks[record.get("check")] = record
        elif record.get("type") == "review":
            latest_reviews[record.get("reviewer")] = record
    issues = [f"blocker: {x}" for x in task["blockers"]]
    check_status = {}
    for name in context["required_checks"]:
        record = latest_checks.get(name)
        status = "missing" if not record else (record.get("status") if record.get("seal") == current else "stale")
        check_status[name] = status
        if status != "passed":
            issues.append(f"check {name}: {status}")
    if task["kind"] in EXECUTABLE_KINDS and not any(
            config["checks"][name]["category"] == "test" for name in context["required_checks"]):
        issues.append("No applicable behavioral test check configured for executable work")

    def usable(record):
        if record.get("seal") != current or not references_current(root, record.get("refs")):
            return False
        check = record.get("check")
        if check:
            latest = latest_checks.get(check, {})
            return latest.get("seal") == current and latest.get("status") == "passed"
        return True

    acceptance = {}
    for criterion in task["acceptance"]:
        ok = any(r.get("type") == "evidence" and r.get("criterion") == criterion["id"] and usable(r) for r in records)
        acceptance[criterion["id"]] = "recorded" if ok else "missing-or-stale"
        if not ok:
            issues.append(f"acceptance {criterion['id']}: missing or stale evidence")
    for control in context["controls"]:
        if not any(r.get("type") == "control" and r.get("control") == control and usable(r) for r in records):
            issues.append(f"control {control}: missing or stale evidence")
    current_reviews = [r for r in latest_reviews.values() if usable(r)]
    # A requested change must be dispositioned by that reviewer; stale findings
    # are not silently erased by unrelated source edits.
    for reviewer, r in latest_reviews.items():
        if r.get("verdict") == "request-changes":
            issues.append(f"review {reviewer}: changes requested; record a resolution review")
    if context["risk"] != "low" and not any(r.get("verdict") == "pass" for r in current_reviews):
        issues.append("Current review is missing")
    if context["risk"] in {"high", "critical"} and not any(
            r.get("verdict") == "pass" and r.get("basis") == "independent"
            and r.get("reviewer") != task["owner"] for r in current_reviews):
        issues.append("Independent review is missing (identity is declared, not authenticated)")
    return {"schema_version": 1, "task": task["id"], "ready": not issues,
            "meaning": "Local evidence readiness only; not deployment authorization or proof of correctness",
            "risk": context["risk"], "signals": context["signals"],
            "required_controls": context["controls"], "checks": check_status,
            "acceptance": acceptance, "issues": issues, "seal": current,
            "snapshot_mode": snap["mode"], "snapshot_files": len(snap["files"])}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".", help="Repository/component root (default: cwd)")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Validate configuration and enumerate inputs; execute no project command")
    init = sub.add_parser("init", help="Create a work contract and a source baseline")
    init.add_argument("--id", required=True)
    init.add_argument("--kind", required=True, choices=KINDS)
    init.add_argument("--risk", default="medium", choices=RISKS)
    init.add_argument("--risk-reason", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--goal", required=True)
    init.add_argument("--owner", default="codex-author")
    init.add_argument("--acceptance", action="append", required=True)
    init.add_argument("--signal", action="append", choices=sorted(SIGNALS), default=[])
    for name in ("check", "inspect", "evidence", "control", "review", "gate", "close"):
        command = sub.add_parser(name)
        command.add_argument("--id", required=True)
        if name == "inspect":
            command.add_argument("--check", required=True, help="Read the latest check result and its bounded output tail")
        if name == "check":
            command.add_argument("--only", action="append")
            command.add_argument("--list", action="store_true", help="Show applicable commands without running them")
        if name in {"evidence", "control", "review"}:
            command.add_argument("--summary", required=True)
            command.add_argument("--path", action="append", required=True)
        if name in {"evidence", "control"}:
            command.add_argument("--check")
        if name == "evidence":
            command.add_argument("--criterion", required=True)
        if name == "control":
            command.add_argument("--name", required=True, choices=CONTROLS)
        if name == "review":
            command.add_argument("--reviewer", required=True)
            command.add_argument("--basis", required=True, choices=("self", "independent"))
            command.add_argument("--verdict", required=True, choices=("pass", "request-changes"))
        if name == "gate":
            command.add_argument("--json", action="store_true")
        if name == "close":
            command.add_argument("--summary", required=True)
    return p


def run(args):
    root = Path(args.root).resolve()
    require(root.is_dir(), "Root must be an existing directory")
    config = config_for(root)
    if args.command == "doctor":
        snap = snapshot(root, config)
        warnings = []
        if not config["checks"]:
            warnings.append("Checks are not configured. Executable tasks cannot pass the gate yet.")
        if (root / "AGENTS.override.md").is_file():
            warnings.append("AGENTS.override.md shadows AGENTS.md in this directory; inspect the instruction chain.")
        for name in ("AGENTS.md", "AGENTS.override.md"):
            if (root / name).is_file() and (root / name).stat().st_size > 24576:
                warnings.append(f"{name} is large; inspect combined Codex instruction limits.")
        print(json.dumps({"version": VERSION, "project": config["name"], "root": str(root),
                          "snapshot_mode": snap["mode"], "snapshot_files": len(snap["files"]),
                          "checks": list(config["checks"]), "warnings": warnings}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "init":
        for value in (args.title, args.goal, args.owner, args.risk_reason, *args.acceptance):
            require(nonempty(value), "Contract fields cannot be empty")
        folder = task_dir(root, args.id)
        require(not folder.exists(), f"Task already exists: {args.id}")
        snap = snapshot(root, config)
        folder.mkdir(parents=True, exist_ok=False)
        task = {"schema_version": 1, "id": args.id, "title": args.title, "goal": args.goal,
                "owner": args.owner, "kind": args.kind, "risk": args.risk,
                "risk_reason": args.risk_reason, "signals": sorted(set(args.signal)),
                "acceptance": [{"id": f"A{i}", "statement": text}
                               for i, text in enumerate(args.acceptance, 1)],
                "blockers": [], "created_at": utc_now()}
        atomic_json(folder / "task.json", task)
        atomic_json(folder / "baseline.json", snap)
        print(f"Created {args.id}: {folder / 'task.json'}")
        return 0
    with locked(root, args.id):
        task = read_task(root, args.id)
        if args.command == "inspect":
            matches = [r for r in records_for(root, args.id)
                       if r.get("type") == "check" and r.get("check") == args.check]
            require(matches, "No recorded run for this check")
            print(json.dumps(matches[-1], ensure_ascii=False, indent=2))
            return 0
        if args.command in {"gate", "close"}:
            result = assess(root, config, task)
            if args.command == "close":
                require(nonempty(args.summary), "Completion summary cannot be empty")
                if result["ready"]:
                    atomic_json(safe_path(root, f".harness/tasks/{args.id}/completion.json"),
                                {"at": utc_now(), "summary": args.summary, "gate": result})
                else:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 1
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"{args.id}: {'READY' if result['ready'] else 'NOT READY'}; risk={result['risk']}")
                for issue in result["issues"]:
                    print(f"- {issue}")
                if result["ready"]:
                    print(result["meaning"])
            return 0 if result["ready"] else 1
        snap, context, current = current_state(root, config, task)
        if args.command == "check":
            names = args.only if args.only is not None else context["required_checks"]
            require(names, "No checks selected. Configure .harness/project.json first")
            require(len(names) == len(set(names)), "Duplicate check selection")
            require(all(n in config["checks"] for n in names), "Unknown check id")
            if args.list:
                print(json.dumps({n: config["checks"][n] for n in names}, ensure_ascii=False, indent=2))
                return 0
            failed = False
            for name in names:
                # Re-read between checks; a command must not rewrite requirements.
                before_config = config_for(root)
                before_task = read_task(root, args.id)
                _, _, before_seal = current_state(root, before_config, before_task)
                result = execute_check(root, before_config["checks"][name])
                try:
                    _, _, after_seal = current_state(root, config_for(root), read_task(root, args.id))
                except HarnessError:
                    after_seal = None
                if before_seal != after_seal:
                    result["status"] = "input-changed"
                receipt = append_record(root, args.id, {"type": "check", "check": name,
                                                       "seal": before_seal, **result})
                print(f"{name}: {result['status']} ({result['seconds']}s, exit={result['exit_code']})")
                print(f"  record: {receipt['record_path']}")
                failed |= result["status"] != "passed"
                if before_seal != after_seal:
                    break
            return 1 if failed else 0
        require(nonempty(args.summary), "Evidence summary cannot be empty")
        refs = evidence_refs(root, args.path)
        record = {"type": args.command, "seal": current, "summary": args.summary, "refs": refs}
        if args.command == "evidence":
            require(args.criterion in {x["id"] for x in task["acceptance"]}, "Unknown acceptance id")
            record["criterion"] = args.criterion
        if args.command == "control":
            record["control"] = args.name
        if args.command in {"evidence", "control"}:
            if args.check:
                require(args.check in config["checks"], "Unknown check")
                checks = [r for r in records_for(root, args.id) if r.get("type") == "check" and r.get("check") == args.check]
                require(checks and checks[-1].get("seal") == current and checks[-1].get("status") == "passed",
                        "Referenced check must have a current passing run")
            record["check"] = args.check
        if args.command == "review":
            require(nonempty(args.reviewer), "Reviewer is required")
            require(args.basis != "independent" or args.reviewer != task["owner"],
                    "Author cannot be their own independent reviewer")
            record.update(reviewer=args.reviewer, basis=args.basis, verdict=args.verdict)
        # Detect concurrent input edits while reading referenced artifacts.
        _, _, after = current_state(root, config_for(root), read_task(root, args.id))
        require(after == current, "Inputs changed while recording evidence; retry against stable inputs")
        append_record(root, args.id, record)
        print(f"Recorded {args.command} for {args.id}")
        return 0


def main(argv=None):
    try:
        return run(parser().parse_args(argv))
    except (HarnessError, OSError, KeyError, TypeError) as exc:
        print(f"harness: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
