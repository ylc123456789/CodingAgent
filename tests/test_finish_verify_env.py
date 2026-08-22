"""Finish auto-verification tests: environment binding (C1) and the
declared-command gate (only spec.verify_commands count as verification)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from coding_agent import CodeTaskSpec, run_code_task
from coding_agent.controller.actions import _run_missing_finish_verification
from coding_agent.models import CommandResult, ControllerAction, StepRecord


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, capture_output=True)


def _make_client(actions: list[dict]):
    """Build a FakeClient that replays one action per step."""

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.index = 0

        def complete_json(self, system, user):
            action = actions[self.index]
            self.index += 1
            return action

    return FakeClient


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


def test_unrelated_command_still_triggers_declared_verification(tmp_path, monkeypatch):
    """A run_command that is not a declared verify_commands entry does not
    satisfy verification: the declared command still runs before finish."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n")
    _init_repo(repo)
    out = tmp_path / "out"

    actions = [
        {"action": "insert_after", "reasoning": "add loss logging",
         "path": "train.py",
         "anchor_text": "print('accuracy 0.5')\n",
         "insert_text": "print('loss 1.0')\n"},
        {"action": "run_command", "reasoning": "check the interpreter",
         "command": "python --version"},
        {"action": "finish", "status": "completed", "summary": "done"},
    ]
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _make_client(actions))
    report = run_code_task(
        CodeTaskSpec(
            workspace_path=repo, output_dir=out, task_goal="add loss",
            verify_commands=["python train.py"], max_steps=8,
        )
    )

    assert report.status == "completed"
    commands = [r.command for r in report.verification_results]
    assert "python train.py" in commands  # declared command auto-ran despite the unrelated run
