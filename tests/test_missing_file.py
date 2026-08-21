"""Regression tests: missing files are recoverable, never fatal."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coding_agent import CodeTaskSpec, run_code_task
from coding_agent.controller.actions import _read_file_observation
from coding_agent.models import ControllerAction
from coding_agent.runtime.edits import (
    StructuredEditError,
    insert_before_anchor,
    replace_text_once,
)
from coding_agent.runtime.safety import SafetyError, ensure_path_allowed


def _empty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    return repo


def _spec(repo: Path, out: Path) -> CodeTaskSpec:
    return CodeTaskSpec(workspace_path=repo, output_dir=out, task_goal="x")


def test_read_file_missing_returns_guidance():
    spec = _spec(Path("/tmp/nonexistent-repo"), Path("/tmp/out"))
    action = ControllerAction(action="read_file", path="train.py")
    observation = _read_file_observation(spec, action)
    assert "does not exist" in observation
    assert "write_file" in observation


def test_read_file_path_traversal_still_rejected(tmp_path):
    repo = _empty_repo(tmp_path)
    spec = _spec(repo, tmp_path / "out")
    action = ControllerAction(action="read_file", path="../outside.py")
    with pytest.raises(SafetyError):
        _read_file_observation(spec, action)


def test_read_file_outside_allowed_paths_still_rejected(tmp_path):
    repo = _empty_repo(tmp_path)
    (repo / "a.py").write_text("x")
    spec = CodeTaskSpec(
        workspace_path=repo, output_dir=tmp_path / "out", task_goal="x",
        allowed_paths=["b.py"],
    )
    action = ControllerAction(action="read_file", path="a.py")
    with pytest.raises(SafetyError):
        _read_file_observation(spec, action)


def test_structured_edits_signal_missing_target(tmp_path):
    repo = _empty_repo(tmp_path)
    with pytest.raises(StructuredEditError, match="write_file"):
        replace_text_once(repo, "train.py", "old", "new")
    with pytest.raises(StructuredEditError, match="write_file"):
        insert_before_anchor(repo, "train.py", "anchor", "insert")


def test_empty_workspace_flow_completes(tmp_path, monkeypatch):
    """list_tree -> read_file(missing) -> write_file -> finish = completed."""
    repo = _empty_repo(tmp_path)
    out = tmp_path / "out"

    class FakeClient:
        actions = [
            {"action": "list_tree", "reasoning": "see what exists"},
            {"action": "read_file", "reasoning": "check target", "path": "train.py"},
            {
                "action": "write_file",
                "reasoning": "create the missing script",
                "path": "train.py",
                "content": "print('hello')\n",
            },
            {"action": "finish", "status": "completed", "summary": "created train.py"},
        ]

        def __init__(self, *args, **kwargs):
            self.index = 0

        def complete_json(self, system, user):
            action = self.actions[self.index]
            self.index += 1
            return action

    monkeypatch.setattr("coding_agent.controller.loop.LLMClient", FakeClient)

    report = run_code_task(CodeTaskSpec(
        workspace_path=repo, output_dir=out,
        task_goal="Create train.py", max_steps=6,
        verify_commands=["python3 train.py"],
    ))

    assert report.status == "completed"
    assert (repo / "train.py").read_text() == "print('hello')\n"

    # the read_file-missing step must be recorded WITHOUT a fatal error
    import json
    state = json.loads((out / "state.json").read_text())
    read_step = next(s for s in state["steps"] if s["action"]["action"] == "read_file")
    assert not read_step.get("error")
    assert "does not exist" in read_step["observation"]
    assert "stopped before completion" not in report.summary
