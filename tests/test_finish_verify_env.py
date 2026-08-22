"""C1 regression tests: finish auto-verification inherits the task environment."""
from __future__ import annotations

from pathlib import Path

from coding_agent.controller.actions import _run_missing_finish_verification
from coding_agent.models import CodeTaskSpec, CommandResult, ControllerAction, StepRecord


def _edit_step() -> StepRecord:
    return StepRecord(
        step=1,
        action=ControllerAction(action="write_file", path="train.py"),
        observation="",
        changed_files=["train.py"],
    )


def test_finish_auto_verification_receives_env_binding(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('x')\n")
    spec = CodeTaskSpec(
        workspace_path=repo, output_dir=tmp_path / "out", task_goal="x",
        verify_commands=["python train.py"],
        env_name="exp_env", env_policy="frozen",
    )

    captured = {}

    def spy(repo_root, commands, log_dir, timeout_seconds, env_name="", env_policy="auto"):
        captured["env_name"] = env_name
        captured["env_policy"] = env_policy
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "verify_01.stdout"
        stderr_path = log_dir / "verify_01.stderr"
        stdout_path.write_text("")
        stderr_path.write_text("")
        return [
            CommandResult(
                command=commands[0], returncode=0,
                stdout_path=stdout_path, stderr_path=stderr_path,
                duration_seconds=0.0,
            )
        ]

    monkeypatch.setattr("coding_agent.controller.actions.run_verify_commands", spy)

    results = _run_missing_finish_verification(spec, [_edit_step()], tmp_path / "out", 5)

    assert len(results) == 1
    assert captured == {"env_name": "exp_env", "env_policy": "frozen"}


def test_finish_auto_verification_default_binding_unchanged(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('x')\n")
    spec = CodeTaskSpec(
        workspace_path=repo, output_dir=tmp_path / "out", task_goal="x",
        verify_commands=["python train.py"],
    )

    captured = {}

    def spy(repo_root, commands, log_dir, timeout_seconds, env_name="", env_policy="auto"):
        captured["env_name"] = env_name
        captured["env_policy"] = env_policy
        return []

    monkeypatch.setattr("coding_agent.controller.actions.run_verify_commands", spy)

    _run_missing_finish_verification(spec, [_edit_step()], tmp_path / "out", 5)

    assert captured == {"env_name": "", "env_policy": "auto"}
