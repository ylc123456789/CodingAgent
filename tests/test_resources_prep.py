"""Integration tests: environment prep in run_code_task (conda mocked)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent import CodeTaskSpec, run_code_task
from coding_agent.resources import (
    EnvironmentBlockedError,
    env_id,
    read_manifest,
    resolved_fingerprint,
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


def _fake_env(prefix: Path) -> None:
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for exe in ("python", "pip", "conda"):
        (prefix / "bin" / exe).write_text("#!/bin/sh\n")
        (prefix / "bin" / exe).chmod(0o755)


class FakeClient:
    """Mock LLM: finish immediately."""
    def __init__(self, *args, **kwargs):
        pass

    def complete_json(self, system, user):
        return {"action": "finish", "status": "completed", "summary": "done"}


def test_auto_creates_env_and_binds_prefix(tmp_path, monkeypatch):
    (tmp_path / "train.py").write_text("print('hi')\n")
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", FakeClient)
    monkeypatch.setattr(
        "coding_agent.resources.create_environment_at",
        lambda prefix, spec, ws=None: _fake_env(prefix),
    )
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: dict(RESOLVED),
    )
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit",
        lambda prefix, spec, creator: {
            "schema": "ENVIRONMENT_AUDIT_V1",
            "audit_id": "audit_x",
            "env_id": "",
            "level": "verification",
            "outcome": "pass",
            "resolved_fingerprint": "",
            "audited_by": creator,
            "at": "2026-08-16T00:00:00Z",
            "checks": [{"name": "policy", "outcome": "pass"}],
            "notes": "",
        },
    )

    root = tmp_path / "resources"
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "train.py").write_text("print(1)\n")
    out = tmp_path / "out"

    report = run_code_task(CodeTaskSpec(
        workspace_path=ws, output_dir=out,
        task_goal="do nothing", max_steps=2,
        env_policy="auto", resource_root=str(root),
    ))

    assert report.status == "completed"
    spec_doc = __import__("coding_agent.resources", fromlist=["collect_environment_spec"]).collect_environment_spec(ws)
    eid = env_id("ws", spec_fingerprint(spec_doc))
    manifest = read_manifest(root, eid)
    assert manifest is not None
    assert manifest["certification"] == "verification"

    # session card carries content-addressed bindings
    import yaml
    card = yaml.safe_load((out / "session.yaml").read_text())
    env_binding = card["bindings"]["environment"]
    assert env_binding["spec_fingerprint"] == spec_fingerprint(spec_doc)
    assert env_binding["manifest_path"]
    assert env_binding["prefix"] == manifest["prefix"]


def test_frozen_drifted_blocks_before_loop(tmp_path, monkeypatch):
    root = tmp_path / "resources"
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "train.py").write_text("print(1)\n")
    prefix = tmp_path / "env"
    _fake_env(prefix)

    spec_doc = __import__("coding_agent.resources", fromlist=["collect_environment_spec"]).collect_environment_spec(ws)
    fp = spec_fingerprint(spec_doc)
    manifest = {
        "schema": "ENVIRONMENT_MANIFEST_V1",
        "env_id": "resenv_x_000000000000",
        "state": "ready",
        "certification": "verification",
        "spec_fingerprint": fp,
        "resolved_fingerprint": RESOLVED_FP,
        "prefix": str(prefix),
        "manager": "codingagent",
        "created_by": {"module": "codingagent"},
        "created_at": "2026-08-16T00:00:00Z",
        "updated_at": "2026-08-16T00:00:00Z",
        "pinned": False,
        "spec": spec_doc,
        "resolved": RESOLVED,
    }
    write_manifest_atomic(root, manifest)
    # drift: inventory changed
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda p: {"python": "3.11.9", "conda_inventory_sha256": "cc" * 32,
                   "pip_inventory_sha256": "bb" * 32, "frameworks": {}, "abi_summary": ""},
    )

    report = run_code_task(CodeTaskSpec(
        workspace_path=ws, output_dir=tmp_path / "out",
        task_goal="do nothing", max_steps=2,
        env_policy="frozen", env_name="resenv_x_000000000000",
        resource_root=str(root),
    ))

    assert report.status == "blocked"
    assert "drifted" in report.summary
    assert report.residual_risks  # required actions present
    assert report.changed_files == []
