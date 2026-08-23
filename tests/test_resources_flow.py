"""Create/reuse flow, lock, and audit tests (conda mocked)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from coding_agent.resources import (
    EnvironmentBlockedError,
    _acquire_creation_lock,
    bind_existing_environment,
    create_or_reuse_environment,
    env_id,
    env_prefix,
    recertify_environment,
    read_manifest,
    resolved_fingerprint,
    run_verification_audit,
    spec_fingerprint,
    write_manifest_atomic,
)

RESOLVED = {
    "python": "3.11.9",
    "conda_inventory_sha256": "aa" * 32,
    "pip_inventory_sha256": "bb" * 32,
    "frameworks": {},
    "abi_summary": "",
}
RESOLVED_FP = resolved_fingerprint(RESOLVED)


def _spec() -> dict:
    return {
        "schema": "ENVIRONMENT_SPEC_V1",
        "python": "3.11",
        "os": "linux",
        "arch": "x86_64",
        "accelerator": {"type": "cpu", "variant": ""},
        "dependency_files": [],
        "channels": [],
        "pip_index_profile": "",
        "framework_constraints": [],
        "notes": "",
    }


def _fake_env(prefix: Path) -> None:
    """Create a fake conda prefix with python/pip/conda executables."""
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for exe in ("python", "pip", "conda"):
        (prefix / "bin" / exe).write_text("#!/bin/sh\n")
        (prefix / "bin" / exe).chmod(0o755)


def _patch_env(monkeypatch, resolved=None):
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: dict(resolved or RESOLVED),
    )
    monkeypatch.setattr(
        "coding_agent.resources.create_environment_at",
        lambda prefix, spec, ws=None: _fake_env(prefix),
    )


def _pass_audit(prefix, spec, creator):
    audit = run_verification_audit(prefix, spec, creator)
    audit["outcome"] = "pass"
    return audit


def test_create_writes_ready_verification_manifest(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", _pass_audit
    )
    root = tmp_path / "resources"
    spec = _spec()
    manifest = create_or_reuse_environment(root, spec, "myproj")
    assert manifest["state"] == "ready"
    assert manifest["certification"] == "verification"  # never experiment
    assert manifest["manager"] == "codingagent"
    assert manifest["resolved_fingerprint"] == RESOLVED_FP
    eid = env_id("myproj", spec_fingerprint(spec))
    assert manifest["env_id"] == eid
    assert (root / "environments" / eid / "manifest.json").exists()
    audits_dir = root / "environments" / eid / "audits"
    assert any(audits_dir.glob("*.json"))


def test_second_create_reuses_zero_creation(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", _pass_audit
    )
    created = 0

    def counting_create(prefix, spec, ws=None):
        nonlocal created
        created += 1
        _fake_env(prefix)

    monkeypatch.setattr("coding_agent.resources.create_environment_at", counting_create)

    root = tmp_path / "resources"
    spec = _spec()
    first = create_or_reuse_environment(root, spec, "myproj")
    second = create_or_reuse_environment(root, spec, "myproj")
    assert first["env_id"] == second["env_id"]
    assert created == 1  # second call reused; zero creation


def test_drifted_ready_blocks_reuse(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", _pass_audit
    )
    root = tmp_path / "resources"
    spec = _spec()
    create_or_reuse_environment(root, spec, "myproj")
    # now the inventory changes -> drift
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: {"python": "3.11.9", "conda_inventory_sha256": "cc" * 32,
                        "pip_inventory_sha256": "bb" * 32, "frameworks": {},
                        "abi_summary": ""},
    )
    with pytest.raises(EnvironmentBlockedError, match="drifted"):
        create_or_reuse_environment(root, spec, "myproj")


def test_recertify_updates_allowed_environment_change(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", _pass_audit,
    )
    root = tmp_path / "resources"
    manifest = create_or_reuse_environment(root, _spec(), "myproj")
    changed = dict(RESOLVED, pip_inventory_sha256="cc" * 32)
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: changed,
    )

    updated = recertify_environment(root, manifest["env_id"])

    assert updated["state"] == "ready"
    assert updated["certification"] == "verification"
    assert updated["resolved_fingerprint"] == resolved_fingerprint(changed)
    assert len(updated["audits"]) == 2
    assert bind_existing_environment(
        root, manifest["env_id"], _spec(), "reuse_only",
    )["state"] == "ready"


def test_recertify_quarantines_failed_environment_change(tmp_path, monkeypatch):
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", _pass_audit,
    )
    root = tmp_path / "resources"
    manifest = create_or_reuse_environment(root, _spec(), "myproj")
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: dict(RESOLVED, pip_inventory_sha256="dd" * 32),
    )
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit",
        lambda prefix, spec, creator: dict(_pass_audit(prefix, spec, creator), outcome="fail"),
    )

    with pytest.raises(EnvironmentBlockedError, match="failed recertification"):
        recertify_environment(root, manifest["env_id"])

    quarantined = read_manifest(root, manifest["env_id"])
    assert quarantined["state"] == "drifted"
    assert quarantined["certification"] == "none"


def test_audit_fail_marks_failed(tmp_path, monkeypatch):
    _patch_env(monkeypatch)

    def fail_audit(prefix, spec, creator):
        audit = run_verification_audit(prefix, spec, creator)
        audit["outcome"] = "fail"
        audit["checks"].append({"name": "framework_import", "outcome": "fail"})
        return audit

    monkeypatch.setattr("coding_agent.resources.run_verification_audit", fail_audit)
    root = tmp_path / "resources"
    with pytest.raises(EnvironmentBlockedError, match="audit failed"):
        create_or_reuse_environment(root, _spec(), "myproj")
    eid = env_id("myproj", spec_fingerprint(_spec()))
    manifest = read_manifest(root, eid)
    assert manifest["state"] == "failed"


def test_creation_failure_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coding_agent.resources.create_environment_at",
        lambda prefix, spec, ws=None: (_ for _ in ()).throw(
            EnvironmentBlockedError("boom", [])
        ),
    )
    root = tmp_path / "resources"
    with pytest.raises(EnvironmentBlockedError):
        create_or_reuse_environment(root, _spec(), "myproj")
    eid = env_id("myproj", spec_fingerprint(_spec()))
    assert read_manifest(root, eid)["state"] == "failed"


def test_lock_excludes_second_creator(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    spec = _spec()
    fp = spec_fingerprint(spec)
    lock = _acquire_creation_lock(root, fp)
    try:
        monkeypatch.setattr(
            "coding_agent.resources._acquire_creation_lock",
            lambda r, f, timeout=1.0: (_ for _ in ()).throw(
                EnvironmentBlockedError("lock held", [])
            ),
        )
        with pytest.raises(EnvironmentBlockedError, match="lock"):
            create_or_reuse_environment(root, spec, "myproj")
    finally:
        lock.release()


def test_dead_holder_lock_reclaimed(tmp_path):
    import socket
    root = tmp_path / "resources"
    (root / "locks").mkdir(parents=True, exist_ok=True)
    lock_path = root / "locks" / f"{'0'*64}.lock"
    lock_path.write_text(json.dumps({
        "host": socket.gethostname(),
        "pid": 999999999,  # certainly not alive
        "started_at": "x",
    }))
    lock = _acquire_creation_lock(root, "0" * 64, timeout=0.1)
    lock.release()
    assert not lock_path.exists()


def test_bind_unregistered_env_blocked(tmp_path):
    root = tmp_path / "resources"
    with pytest.raises(EnvironmentBlockedError, match="not registered"):
        bind_existing_environment(root, "resenv_x_000000000000", _spec(), "frozen")


def test_bind_spec_mismatch_blocked(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    other = _spec()
    other["python"] = "3.9"
    manifest = {
        "schema": "ENVIRONMENT_MANIFEST_V1",
        "env_id": "resenv_x_000000000000",
        "state": "ready",
        "certification": "verification",
        "spec_fingerprint": spec_fingerprint(other),
        "resolved_fingerprint": RESOLVED_FP,
        "prefix": str(tmp_path / "env"),
        "manager": "codingagent",
        "created_by": {"module": "codingagent"},
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "pinned": False,
        "spec": other,
        "resolved": RESOLVED,
    }
    write_manifest_atomic(root, manifest)
    with pytest.raises(EnvironmentBlockedError, match="does not match"):
        bind_existing_environment(root, manifest["env_id"], _spec(), "reuse_only")


def test_bind_clean_env_ok(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    spec = _spec()
    prefix = tmp_path / "env"
    _fake_env(prefix)
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda p: dict(RESOLVED),
    )
    manifest = {
        "schema": "ENVIRONMENT_MANIFEST_V1",
        "env_id": "resenv_x_000000000000",
        "state": "ready",
        "certification": "verification",
        "spec_fingerprint": spec_fingerprint(spec),
        "resolved_fingerprint": RESOLVED_FP,
        "prefix": str(prefix),
        "manager": "codingagent",
        "created_by": {"module": "codingagent"},
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "pinned": False,
        "spec": spec,
        "resolved": RESOLVED,
    }
    write_manifest_atomic(root, manifest)
    result = bind_existing_environment(root, manifest["env_id"], spec, "frozen")
    assert result["env_id"] == manifest["env_id"]


def test_manifest_provenance_fields_filled(tmp_path, monkeypatch):
    import subprocess
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", _pass_audit
    )
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "train.py").write_text("print(1)\n")
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "train.py"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)

    root = tmp_path / "resources"
    manifest = create_or_reuse_environment(
        root, _spec(), "myproj", ws, {"module": "codingagent"},
        repo_origin="https://example.invalid/org/repo.git",
    )
    prov = manifest["provenance"]
    assert prov["repo_path"] == str(ws)
    assert prov["repo_origin"] == "https://example.invalid/org/repo.git"
    assert prov["repo_commit"]  # non-empty git HEAD


def test_verification_audit_never_experiment(tmp_path):
    prefix = tmp_path / "env"
    _fake_env(prefix)
    audit = run_verification_audit(prefix, _spec(), {"module": "codingagent"})
    assert audit["level"] == "verification"
    assert audit["outcome"] == "pass"  # no framework constraints -> trivially pass
