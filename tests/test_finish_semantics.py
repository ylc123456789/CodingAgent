"""C3 regression tests: deterministic verification failures outrank a
requested "completed" status (controller layer)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from coding_agent import CodeTaskSpec, run_code_task


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


def _edit_then_finish_run(tmp_path, monkeypatch, verify_commands, actions_after_edit):
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
    finish = {
        "action": "finish",
        "status": "completed",
        "summary": "done",
    }
    actions = [edit] + actions_after_edit + [finish]
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _make_client(actions))
    return run_code_task(
        CodeTaskSpec(
            workspace_path=repo, output_dir=out, task_goal="add loss",
            verify_commands=verify_commands, max_steps=6,
        )
    )


def test_failed_auto_verification_downgrades_completed(tmp_path, monkeypatch):
    """finish triggers auto-verification; its failure must yield failed."""
    report = _edit_then_finish_run(tmp_path, monkeypatch, ["exit 1"], [])
    assert report.status == "failed"
    assert any("downgraded" in risk for risk in report.residual_risks)


def test_failed_explicit_verification_downgrades_completed(tmp_path, monkeypatch):
    """An explicit failing run_command must also yield failed."""
    report = _edit_then_finish_run(
        tmp_path, monkeypatch, [],
        [{"action": "run_command", "reasoning": "verify", "command": "exit 1"}],
    )
    assert report.status == "failed"
    assert any("downgraded" in risk for risk in report.residual_risks)


def test_finish_without_verification_stays_completed(tmp_path, monkeypatch):
    """No verification ran: requested completed is kept (no scope expansion)."""
    report = _edit_then_finish_run(tmp_path, monkeypatch, [], [])
    assert report.status == "completed"
    assert not any("downgraded" in risk for risk in report.residual_risks)


def test_no_change_with_failed_verification_downgrades_completed(tmp_path, monkeypatch):
    """A no-op task cannot hide a verification failure behind completed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('unchanged')\n")
    _init_repo(repo)
    actions = [
        {"action": "run_command", "reasoning": "verify existing code", "command": "exit 1"},
        {"action": "finish", "status": "completed", "summary": "no changes needed"},
    ]
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _make_client(actions))

    report = run_code_task(
        CodeTaskSpec(
            workspace_path=repo,
            output_dir=tmp_path / "out",
            task_goal="verify existing code",
            max_steps=4,
        )
    )

    assert report.changed_files == []
    assert report.status == "failed"
    assert any("downgraded" in risk for risk in report.residual_risks)


def test_finish_with_passing_verification_stays_completed(tmp_path, monkeypatch):
    report = _edit_then_finish_run(
        tmp_path, monkeypatch, [],
        [{"action": "run_command", "reasoning": "verify", "command": "python train.py"}],
    )
    assert report.status == "completed"


def test_failed_then_fixed_same_command_reports_completed(tmp_path, monkeypatch):
    """History never decides the final status: the declared check fails,
    the agent fixes the code, re-runs the same check successfully, and
    the final status must be completed (latest run wins, no extra auto-run)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n")
    (repo / "check.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if 'loss' in pathlib.Path('train.py').read_text() else 1)\n"
    )
    _init_repo(repo)
    out = tmp_path / "out"

    actions = [
        {"action": "run_command", "reasoning": "run the check before editing",
         "command": "python check.py"},
        {"action": "insert_after", "reasoning": "fix the check",
         "path": "train.py",
         "anchor_text": "print('accuracy 0.5')\n",
         "insert_text": "print('loss 1.0')\n"},
        {"action": "run_command", "reasoning": "re-run the same check",
         "command": "python check.py"},
        {"action": "finish", "status": "completed", "summary": "fixed and verified"},
    ]
    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", _make_client(actions))
    report = run_code_task(
        CodeTaskSpec(
            workspace_path=repo, output_dir=out, task_goal="add loss",
            verify_commands=["python check.py"], max_steps=8,
        )
    )

    assert report.status == "completed"
    # Exactly the two agent runs of the declared command; the historical
    # failure stays in the evidence but does not trigger a third run.
    assert [r.command for r in report.verification_results] == ["python check.py", "python check.py"]


def test_any_failed_final_verification_reports_failed(tmp_path, monkeypatch):
    """Among multiple verifications after the last change, any failure
    makes the final status failed."""
    report = _edit_then_finish_run(
        tmp_path, monkeypatch, [],
        [
            {"action": "run_command", "reasoning": "check output",
             "command": "python train.py"},
            {"action": "run_command", "reasoning": "check something else",
             "command": "exit 1"},
        ],
    )
    assert report.status == "failed"
    assert any("downgraded" in risk for risk in report.residual_risks)
