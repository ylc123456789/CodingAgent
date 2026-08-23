"""Integration coverage for post-task environment recertification."""
from pathlib import Path

import yaml

from coding_agent import CodeTaskSpec, run_code_task
from coding_agent.resources import (
    collect_environment_spec,
    env_id,
    read_manifest,
    resolved_fingerprint,
    spec_fingerprint,
)


INITIAL = {
    "python": "3.11.9",
    "conda_inventory_sha256": "aa" * 32,
    "pip_inventory_sha256": "bb" * 32,
    "frameworks": {},
    "abi_summary": "",
}
CHANGED = dict(INITIAL, pip_inventory_sha256="cc" * 32)


class _FinishClient:
    def __init__(self, *args, **kwargs):
        pass

    def complete_json(self, system, user):
        return {"action": "finish", "status": "completed", "summary": "done"}


def _fake_env(prefix: Path) -> None:
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for name in ("python", "pip", "conda"):
        executable = prefix / "bin" / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)


def test_run_code_task_recertifies_changed_auto_environment(tmp_path, monkeypatch):
    inventories = iter([INITIAL, CHANGED])
    audit_count = 0

    def passing_audit(prefix, spec, creator):
        nonlocal audit_count
        audit_count += 1
        return {
            "schema": "ENVIRONMENT_AUDIT_V1",
            "audit_id": f"audit_{audit_count}",
            "env_id": "",
            "level": "verification",
            "outcome": "pass",
            "resolved_fingerprint": "",
            "audited_by": creator,
            "at": "2026-08-23T00:00:00Z",
            "checks": [{"name": "policy", "outcome": "pass"}],
            "notes": "",
        }

    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _FinishClient)
    monkeypatch.setattr(
        "coding_agent.resources.create_environment_at",
        lambda prefix, spec, ws=None: _fake_env(prefix),
    )
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: dict(next(inventories)),
    )
    monkeypatch.setattr(
        "coding_agent.resources.run_verification_audit", passing_audit,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "train.py").write_text("print(1)\n", encoding="utf-8")
    output = tmp_path / "output"
    root = tmp_path / "resources"
    report = run_code_task(CodeTaskSpec(
        workspace_path=workspace,
        output_dir=output,
        task_goal="finish",
        max_steps=2,
        env_policy="auto",
        resource_root=str(root),
    ))

    spec = collect_environment_spec(workspace)
    manifest = read_manifest(root, env_id("workspace", spec_fingerprint(spec)))
    assert report.status == "completed"
    assert manifest["state"] == "ready"
    assert manifest["resolved_fingerprint"] == resolved_fingerprint(CHANGED)
    assert len(manifest["audits"]) == 2
    binding = yaml.safe_load((output / "session.yaml").read_text())["bindings"]["environment"]
    assert binding["resolved_fingerprint"] == manifest["resolved_fingerprint"]
