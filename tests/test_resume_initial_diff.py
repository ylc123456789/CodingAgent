"""C2 regression test: resume preserves the first-run initial_diff.patch."""
from __future__ import annotations

import subprocess
from pathlib import Path

from coding_agent import CodeTaskSpec, resume_code_task, run_code_task


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


def test_resume_preserves_initial_diff_patch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n")
    _init_repo(repo)
    out = tmp_path / "out"

    edit = {
        "action": "insert_after",
        "reasoning": "add loss logging",
        "path": "train.py",
        "anchor_text": "print('accuracy 0.5')\n",
        "insert_text": "print('loss 1.0')\n",
    }
    pause = {
        "action": "ask_user",
        "status": "needs_user_input",
        "summary": "paused for review",
    }
    finish = {
        "action": "finish",
        "status": "completed",
        "summary": "resumed and finished",
    }

    # First run: edit, then pause via ask_user.
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _make_client([edit, pause]))
    report1 = run_code_task(
        CodeTaskSpec(workspace_path=repo, output_dir=out, task_goal="add loss", max_steps=4)
    )
    assert report1.status == "needs_user_input"
    initial = (out / "initial_diff.patch").read_text(encoding="utf-8")
    assert initial == ""  # clean baseline before any edits

    # Resume: the original initial_diff.patch must survive untouched.
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _make_client([finish]))
    report2 = resume_code_task(out, "continue", max_steps=4)

    assert report2.status == "completed"
    assert (out / "initial_diff.patch").read_text(encoding="utf-8") == initial
    # The current diff at resume time is non-empty, so an overwrite would
    # have been observable — this asserts the test scenario is meaningful.
    assert "+print('loss 1.0')" in (out / "diff.patch").read_text(encoding="utf-8")
