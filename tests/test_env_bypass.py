"""Regression tests for env policy bypass + resume contract restoration."""
from pathlib import Path

import pytest

from coding_agent.models import CodeTaskSpec
from coding_agent.runtime.safety import validate_env_command, SafetyError


# ---- Bug 1a: restricted policy requires env_name ----
def test_frozen_requires_env_name(tmp_path):
    with pytest.raises(ValueError, match="env_name"):
        CodeTaskSpec(
            workspace_path=tmp_path,
            output_dir=tmp_path / "out",
            task_goal="x",
            env_policy="frozen",
        )


def test_reuse_only_requires_env_name(tmp_path):
    with pytest.raises(ValueError, match="env_name"):
        CodeTaskSpec(
            workspace_path=tmp_path,
            output_dir=tmp_path / "out",
            task_goal="x",
            env_policy="reuse_only",
        )


def test_auto_allows_empty_env_name(tmp_path):
    spec = CodeTaskSpec(
        workspace_path=tmp_path,
        output_dir=tmp_path / "out",
        task_goal="x",
        env_policy="auto",
    )
    assert spec.env_name == ""


# ---- Bug 1b: bypass vectors ----
@pytest.mark.parametrize("command", [
    "python -m pip install torch",
    "python3 -m pip install torch",
    "/opt/conda/bin/pip install torch",
    "pip3 install torch",
    "mamba install pytorch",
    "micromamba install jax",
    "uv pip install torch",
    "conda env update -f environment.yml",
    "pip install torch && python train.py",
    "cd /tmp && pip install torch",
])
def test_frozen_blocks_bypass_vectors(command):
    with pytest.raises(SafetyError, match="frozen"):
        validate_env_command(command, "frozen")


@pytest.mark.parametrize("command", [
    "python -m pip install torch",
    "/opt/conda/bin/pip install torch",
    "mamba install pytorch",
    "uv pip install torch",
    "conda env update",
])
def test_reuse_only_blocks_heavy_bypass_vectors(command):
    with pytest.raises(SafetyError):
        validate_env_command(command, "reuse_only")


def test_frozen_rejects_unparseable():
    with pytest.raises(SafetyError, match="frozen"):
        validate_env_command('pip install "unbalanced', "frozen")


@pytest.mark.parametrize("command", [
    "conda run -n resenv_x python train.py",
    "conda env list",
    "conda list",
    "pip list",
    "python -m pip list",
    "grep install train.py",
    "python train.py",
])
def test_non_mutation_commands_allowed(command):
    validate_env_command(command, "frozen")  # no raise
    validate_env_command(command, "reuse_only")  # no raise


def test_reuse_only_allows_small_install():
    validate_env_command("pip install requests", "reuse_only")  # no raise
    validate_env_command("python -m pip install numpy", "reuse_only")  # no raise


# ---- Bug 2: resume restores execution contract ----
def test_resume_restores_env_contract(tmp_path, monkeypatch):
    import json
    import yaml
    from coding_agent import resume_code_task

    out = tmp_path / "out"
    out.mkdir()

    # Simulate a previous frozen run
    state = {
        "task": {
            "workspace_path": str(tmp_path / "ws"),
            "task_goal": "old goal",
            "constraints": [],
            "verify_commands": ["python train.py"],
            "allowed_paths": ["train.py"],
            "repo_url": "https://example.invalid/org/repo.git",
            "branch": "main",
            "env_policy": "frozen",
            "env_name": "resenv_x",
            "max_steps": 24,
            "max_extra_steps_after_progress": 11,
            "patch_repair_attempts": 4,
            "max_context_tokens": 120000,
            "model_context_window_tokens": 200000,
            "context_margin_ratio": 0.15,
            "context_output_reserve_tokens": 12000,
            "resource_root": str(tmp_path / "resources"),
            "requires_gpu": True,
            "accelerator_variant": "cu124",
            "pip_index_profile": "autodl",
            "dataset_cache_dir": str(tmp_path / "datasets"),
            "mirror_profile": "autodl",
            "project_ref": "resume-project",
            "parent_run": {"module": "resagent", "run_id": "res-1"},
        },
        "steps": [],
        "report": {"summary": "previous run done"},
    }
    (out / "state.json").write_text(json.dumps(state))
    (out / "session.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "session_id": "code-test-1",
        "module": "codingagent",
        "kind": "task_session",
        "status": "completed",
        "created_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
        "summary": "previous run",
        "project_path": str(tmp_path / "ws"),
    }))

    captured = {}

    def fake_resume(spec, output_dir):
        captured["spec"] = spec
        from coding_agent.models import PatchReport
        return PatchReport(
            status="completed", changed_files=[], diff_path=None,
            verification_results=[], summary="resumed",
        )

    monkeypatch.setattr("coding_agent.agent._run_code_task_resume", fake_resume)
    monkeypatch.setattr("coding_agent.agent._prepare_workspace", lambda spec: None)

    resume_code_task(out, "continue the task")

    spec = captured["spec"]
    assert spec.env_policy == "frozen"
    assert spec.env_name == "resenv_x"
    assert spec.repo_url == "https://example.invalid/org/repo.git"
    assert spec.branch == "main"
    assert spec.verify_commands == ["python train.py"]
    assert spec.allowed_paths == ["train.py"]
    assert spec.max_extra_steps_after_progress == 11
    assert spec.patch_repair_attempts == 4
    assert spec.max_context_tokens == 120000
    assert spec.model_context_window_tokens == 200000
    assert spec.context_margin_ratio == 0.15
    assert spec.context_output_reserve_tokens == 12000
    assert spec.resource_root == str(tmp_path / "resources")
    assert spec.requires_gpu is True
    assert spec.accelerator_variant == "cu124"
    assert spec.pip_index_profile == "autodl"
    assert spec.dataset_cache_dir == str(tmp_path / "datasets")
    assert spec.mirror_profile == "autodl"
    assert spec.project_ref == "resume-project"
    assert spec.parent_run == {"module": "resagent", "run_id": "res-1"}
    assert "previous run" in spec.task_goal
    assert "continue the task" in spec.task_goal


def test_resume_reenters_environment_lifecycle(tmp_path, monkeypatch):
    from coding_agent.agent import _run_code_task_resume
    from coding_agent.models import AgentState, PatchReport

    out = tmp_path / "out"
    out.mkdir()
    spec = CodeTaskSpec(
        workspace_path=tmp_path / "ws",
        output_dir=out,
        task_goal="continue",
        resource_root=str(tmp_path / "resources"),
    )
    (out / "state.json").write_text(AgentState(task=spec).model_dump_json())

    captured = {}

    def fake_prepare(task_spec):
        captured["prepared"] = task_spec.resource_root
        return None

    def fake_controller(task_spec, resume_state=None):
        captured["resume_state"] = resume_state
        return PatchReport(
            status="completed",
            changed_files=[],
            diff_path=None,
            verification_results=[],
            summary="resumed",
        )

    monkeypatch.setattr("coding_agent.agent._prepare_environment", fake_prepare)
    monkeypatch.setattr("coding_agent.agent.run_step_controller", fake_controller)

    _run_code_task_resume(spec, out)

    assert captured["prepared"] == str(tmp_path / "resources")
    assert isinstance(captured["resume_state"], AgentState)
