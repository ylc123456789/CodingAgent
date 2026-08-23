"""Regression coverage for recoverable command safety failures."""
from __future__ import annotations

import subprocess
from pathlib import Path

from coding_agent import CodeTaskSpec, run_code_task


def test_controller_retries_after_blocked_verification_command(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repo, check=True,
    )
    subprocess.run(["git", "add", "train.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"], cwd=repo, check=True,
        capture_output=True,
    )

    class FakeClient:
        actions = [
            {
                "action": "run_command",
                "reasoning": "reset outputs before verification",
                "command": "rm -rf results && python train.py",
            },
            {
                "action": "run_command",
                "reasoning": "retry without destructive cleanup",
                "command": "python train.py",
            },
            {
                "action": "finish",
                "status": "completed",
                "summary": "Verified with a safe command.",
            },
        ]

        def __init__(self, *args, **kwargs):
            self.index = 0

        def complete_json(self, system, user):
            action = self.actions[self.index]
            self.index += 1
            return action

    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", FakeClient)

    report = run_code_task(
        CodeTaskSpec(
            workspace_path=repo,
            output_dir=tmp_path / "out",
            task_goal="Verify the training script.",
            max_steps=3,
        )
    )

    assert report.status == "completed"
    assert [result.command for result in report.verification_results] == [
        "python train.py"
    ]
    state_text = (tmp_path / "out" / "state.json").read_text(encoding="utf-8")
    assert "blocked verification command" in state_text
