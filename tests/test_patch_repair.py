"""Repair-loop regression tests: no diff re-guessing, write_file fallback."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from coding_agent.runtime.apply import PatchApplyError, apply_patch_text
from coding_agent.controller.actions import _apply_patch_with_repair
from coding_agent.controller.repair import repair_patch
from coding_agent.models import CodeTaskSpec


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)


def _spec(repo: Path, out: Path, repair_attempts: int = 2) -> CodeTaskSpec:
    out.mkdir(parents=True, exist_ok=True)
    return CodeTaskSpec(
        workspace_path=repo,
        output_dir=out,
        task_goal="Fix the file.",
        patch_repair_attempts=repair_attempts,
    )


class FakeClient:
    """LLM mock returning queued repair responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append({"system": system, "user": user})
        if not self.responses:
            raise AssertionError("FakeClient ran out of responses")
        return self.responses.pop(0)


def test_repair_write_file_overwrites_existing(tmp_path):
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    _init_repo(repo)
    (repo / "train.py").write_text("print('a')\nprint('b')\n")

    malformed = (
        "diff --git a/train.py b/train.py\n"
        "--- a/train.py\n+++ b/train.py\n"
        "@@ -1,2 +1,999 @@\n"
    )
    client = FakeClient([
        {"action": "write_file", "path": "train.py",
         "content": "print('a')\nprint('loss 1.0')\n", "notes": ["rewrote"]},
    ])
    changed, observation = _apply_patch_with_repair(
        _spec(repo, out), malformed, out, 4, client
    )
    assert changed == ["train.py"]
    assert (repo / "train.py").read_text() == "print('a')\nprint('loss 1.0')\n"
    assert "full-file rewrite" in observation


def test_repair_write_file_creates_new_file(tmp_path):
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    _init_repo(repo)
    (repo / "train.py").write_text("print('a')\n")
    subprocess.run(["git", "add", "train.py"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=repo, capture_output=True)

    malformed = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n+++ b/new.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+print('n')\n"
        "+print('bad')\n"
    )
    client = FakeClient([
        {"action": "write_file", "path": "new.py",
         "content": "print('new')\n", "notes": []},
    ])
    changed, _ = _apply_patch_with_repair(
        _spec(repo, out), malformed, out, 4, client
    )
    assert changed == ["new.py"]
    assert (repo / "new.py").read_text() == "print('new')\n"


def test_repair_hard_rejects_apply_patch(tmp_path):
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    _init_repo(repo)
    (repo / "train.py").write_text("print('a')\n")

    malformed = (
        "diff --git a/train.py b/train.py\n"
        "--- a/train.py\n+++ b/train.py\n"
        "@@ -1 +1,999 @@\n"
    )
    client = FakeClient([
        {"action": "apply_patch", "patch": malformed, "notes": []},
    ])
    with pytest.raises(PatchApplyError, match="forbidden"):
        repair_patch(_spec(repo, out), malformed, "boom", out, 1, 1, client)


def test_structured_repair_failure_continues_loop(tmp_path):
    """A failing converted structured edit moves to the next repair round."""
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    _init_repo(repo)
    (repo / "train.py").write_text("print('a')\nprint('b')\n")
    subprocess.run(["git", "add", "train.py"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=repo, capture_output=True)

    malformed = (
        "diff --git a/train.py b/train.py\n"
        "--- a/train.py\n+++ b/train.py\n"
        "@@ -1 +1,999 @@\n"
    )
    # call order: patch-repair -> structured repairs (x2, still failing)
    # -> next patch-repair -> write_file success
    client = FakeClient([
        {"action": "replace_text", "path": "train.py",
         "old_text": "text that does not exist",
         "new_text": "x", "notes": []},
        {"action": "replace_text", "path": "train.py",
         "old_text": "still not present",
         "new_text": "y", "notes": []},
        {"action": "replace_text", "path": "train.py",
         "old_text": "never matches",
         "new_text": "z", "notes": []},
        {"action": "write_file", "path": "train.py",
         "content": "print('a')\nprint('c')\n", "notes": ["rewrote"]},
    ])
    changed, observation = _apply_patch_with_repair(
        _spec(repo, out, repair_attempts=2), malformed, out, 4, client
    )
    assert changed == ["train.py"]
    assert (repo / "train.py").read_text() == "print('a')\nprint('c')\n"
    assert len(client.calls) == 4


def test_large_file_context_marked_truncated(tmp_path):
    repo = tmp_path / "repo"
    out = tmp_path / "out"
    _init_repo(repo)
    (repo / "big.py").write_text("x" * 70_000)
    subprocess.run(["git", "add", "big.py"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=repo, capture_output=True)

    malformed = (
        "diff --git a/big.py b/big.py\n"
        "--- a/big.py\n+++ b/big.py\n"
        "@@ -1 +1,999 @@\n"
    )
    client = FakeClient([
        {"action": "replace_text", "path": "big.py",
         "old_text": "x" * 20, "new_text": "y" * 20, "notes": []},
    ])
    repair_patch(_spec(repo, out), malformed, "boom", out, 1, 1, client)
    user = client.calls[0]["user"]
    payload = json.loads(user)
    ctx = payload["current_file_context"]
    assert ctx[0]["truncated"] is True
    assert len(ctx[0]["text"]) == 60_000
