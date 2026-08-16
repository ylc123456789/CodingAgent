"""inspect/prune entry point tests (dry-run only)."""
from __future__ import annotations

import datetime
import json
import socket
from pathlib import Path

from coding_agent.resources import inspect_environments, prune_environments


def _write_manifest(root: Path, env_id: str, *, pinned=False, last_used_at=None,
                    manager="codingagent", state="ready") -> None:
    manifest = {
        "schema": "ENVIRONMENT_MANIFEST_V1",
        "env_id": env_id,
        "state": state,
        "certification": "verification",
        "spec_fingerprint": "0" * 64,
        "resolved_fingerprint": "1" * 64,
        "prefix": f"{root}/conda-envs/{env_id}",
        "manager": manager,
        "created_by": {"module": manager},
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "last_used_at": last_used_at,
        "pinned": pinned,
    }
    path = root / "environments" / env_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))


def test_inspect_lists_only_codingagent_envs(tmp_path):
    root = tmp_path / "resources"
    _write_manifest(root, "resenv_a_000000000000")
    _write_manifest(root, "resenv_b_111111111111", manager="reproagent")
    entries = inspect_environments(root)
    assert [e["env_id"] for e in entries] == ["resenv_a_000000000000"]


def test_inspect_unreadable_manifest(tmp_path):
    root = tmp_path / "resources"
    (root / "environments" / "resenv_bad_000000000000").mkdir(parents=True)
    (root / "environments" / "resenv_bad_000000000000" / "manifest.json").write_text("{not json")
    entries = inspect_environments(root)
    assert entries[0]["state"] == "unreadable"


def test_prune_skips_pinned(tmp_path):
    root = tmp_path / "resources"
    old = "2020-01-01T00:00:00Z"
    _write_manifest(root, "resenv_pinned_000000000000", pinned=True, last_used_at=old)
    _write_manifest(root, "resenv_stale_000000000001", last_used_at=old)
    candidates = prune_environments(root, min_unused_days=30)
    ids = [c["env_id"] for c in candidates]
    assert "resenv_pinned_000000000000" not in ids
    assert "resenv_stale_000000000001" in ids
    # dry-run never deletes
    assert (root / "environments" / "resenv_stale_000000000001" / "manifest.json").exists()


def test_prune_skips_active_lease(tmp_path):
    root = tmp_path / "resources"
    old = "2020-01-01T00:00:00Z"
    _write_manifest(root, "resenv_leased_000000000000", last_used_at=old)
    (root / "leases").mkdir(parents=True)
    lease = {
        "schema": "RESOURCE_LEASE_V1",
        "lease_id": "lease-1",
        "env_id": "resenv_leased_000000000000",
        "run_id": "res-1",
        "task_id": "task-1",
        "host": socket.gethostname(),
        "pid": __import__("os").getpid(),
        "acquired_at": "2026-08-16T00:00:00Z",
        "heartbeat_at": "2026-08-16T00:00:00Z",
        "released_at": None,
    }
    (root / "leases" / "lease-1.json").write_text(json.dumps(lease))
    candidates = prune_environments(root, min_unused_days=30)
    assert [c["env_id"] for c in candidates] == []


def test_prune_skips_recently_used(tmp_path):
    root = tmp_path / "resources"
    recent = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _write_manifest(root, "resenv_recent_000000000000", last_used_at=recent)
    candidates = prune_environments(root, min_unused_days=30)
    assert candidates == []


def test_prune_reports_dry_run_flag(tmp_path):
    root = tmp_path / "resources"
    _write_manifest(root, "resenv_x_000000000000", last_used_at="2020-01-01T00:00:00Z")
    candidates = prune_environments(root, min_unused_days=30)
    assert candidates[0]["dry_run"] is True


def test_delete_environment_guards_and_apply(tmp_path):
    from coding_agent.resources import delete_environment

    root = tmp_path / "resources"
    env_id = "resenv_x_000000000000"
    _write_manifest(root, env_id)
    prefix = root / "conda-envs" / env_id
    prefix.mkdir(parents=True)
    (prefix / "marker.txt").write_text("x")

    # wrong manager refused
    other = "resenv_y_000000000000"
    _write_manifest(root, other, manager="reproagent")
    refused = delete_environment(root, other)
    assert refused["deleted"] is False and "not_managed" in refused["reason"]

    # containment: outside prefix never deleted, manifest kept
    out = "resenv_z_000000000000"
    _write_manifest(root, out)
    manifest_file = root / "environments" / out / "manifest.json"
    data = json.loads(manifest_file.read_text())
    data["prefix"] = str(tmp_path / "outside-env")
    manifest_file.write_text(json.dumps(data))
    refused = delete_environment(root, out)
    assert refused["deleted"] is False
    assert refused["reason"] == "prefix_outside_resource_root"
    assert manifest_file.exists()

    # happy path
    result = delete_environment(root, env_id)
    assert result["deleted"] is True
    assert not prefix.exists()
    assert not (root / "environments" / env_id).exists()
