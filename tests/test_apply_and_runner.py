"""Test patch application, structured edits, and command running."""
from pathlib import Path

import pytest

from coding_agent.runtime.apply import PatchApplyError, apply_patch_text, current_diff
from coding_agent.runtime.edits import StructuredEditError, insert_after_anchor, insert_before_anchor, replace_text_once
from coding_agent.runtime.runner import run_verify_commands


def test_apply_patch_text_and_current_diff(tmp_path: Path) -> None:
    """Verify apply patch text and current diff."""
    repo = tmp_path
    (repo / "train.py").write_text("print('accuracy')\n", encoding="utf-8")
    _init_repo(repo)

    patch = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,2 @@
 print('accuracy')
+print('loss')
"""
    changed = apply_patch_text(repo, patch)
    assert changed == ["train.py"]
    assert "print('loss')" in (repo / "train.py").read_text(encoding="utf-8")
    assert "+print('loss')" in current_diff(repo)


def test_apply_patch_text_checks_patch_before_applying(tmp_path: Path) -> None:
    """Verify apply patch text checks patch before applying."""
    repo = tmp_path
    (repo / "train.py").write_text("print('accuracy')\n", encoding="utf-8")
    _init_repo(repo)

    malformed_patch = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,3 @@
 print('accuracy')
+print('loss')
"""
    with pytest.raises(PatchApplyError, match="git apply --check failed"):
        apply_patch_text(repo, malformed_patch)
    assert (repo / "train.py").read_text(encoding="utf-8") == "print('accuracy')\n"


def test_replace_text_once_succeeds_and_rejects_ambiguous_matches(tmp_path: Path) -> None:
    """Verify replace text once succeeds and rejects ambiguous matches."""
    repo = tmp_path
    path = repo / "train.py"
    path.write_text("print('accuracy')\n", encoding="utf-8")
    _init_repo(repo)

    changed = replace_text_once(repo, "train.py", "print('accuracy')", "print('loss')")
    assert changed == "train.py"
    assert path.read_text(encoding="utf-8") == "print('loss')\n"

    path.write_text("x = 1\nx = 1\n", encoding="utf-8")
    with pytest.raises(StructuredEditError, match="found 2"):
        replace_text_once(repo, "train.py", "x = 1", "x = 2")
    with pytest.raises(StructuredEditError, match="found 0"):
        replace_text_once(repo, "train.py", "missing", "x = 2")


def test_insert_after_anchor_succeeds_with_exact_anchor(tmp_path: Path) -> None:
    """Verify insert after anchor succeeds with exact anchor."""
    repo = tmp_path
    path = repo / "train.py"
    path.write_text("accuracy = 0.5\nprint(accuracy)\n", encoding="utf-8")
    _init_repo(repo)

    changed = insert_after_anchor(repo, "train.py", "accuracy = 0.5\n", "loss = 1.0\n")
    assert changed == "train.py"
    assert path.read_text(encoding="utf-8") == "accuracy = 0.5\nloss = 1.0\nprint(accuracy)\n"




def test_insert_after_anchor_treats_line_anchor_as_whole_line(tmp_path: Path) -> None:
    """Verify insert after anchor treats line anchor as whole line."""
    repo = tmp_path
    path = repo / "train.py"
    path.write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _init_repo(repo)

    insert_after_anchor(repo, "train.py", "print('accuracy 0.5')", "print('loss 1.0')\n")
    assert path.read_text(encoding="utf-8") == "print('accuracy 0.5')\nprint('loss 1.0')\n"

def test_insert_after_line_anchor_adds_missing_newline(tmp_path: Path) -> None:
    """Verify insert after line anchor adds missing newline."""
    repo = tmp_path
    path = repo / "train.py"
    path.write_text("loss_meter = RunningAverageMeter()\nend = time.time()\n", encoding="utf-8")
    _init_repo(repo)

    insert_after_anchor(repo, "train.py", "loss_meter = RunningAverageMeter()", "start_time = time.time()")

    assert path.read_text(encoding="utf-8") == (
        "loss_meter = RunningAverageMeter()\nstart_time = time.time()\nend = time.time()\n"
    )


def test_insert_before_anchor_supports_explicit_occurrence_index(tmp_path: Path) -> None:
    """Verify insert before anchor supports explicit occurrence index."""
    repo = tmp_path
    path = repo / "train.py"
    path.write_text("start\nend = time.time()\nstep\nend = time.time()\n", encoding="utf-8")
    _init_repo(repo)

    insert_before_anchor(repo, "train.py", "end = time.time()", "loss_meter = RunningAverageMeter()\n", occurrence_index=1)

    assert path.read_text(encoding="utf-8") == (
        "start\nloss_meter = RunningAverageMeter()\nend = time.time()\nstep\nend = time.time()\n"
    )

def test_run_verify_commands_writes_logs(tmp_path: Path) -> None:
    """Verify run verify commands writes logs."""
    results = run_verify_commands(
        tmp_path,
        ["python -c \"print('loss 1.0')\""],
        tmp_path / "logs",
        timeout_seconds=30,
    )
    assert len(results) == 1
    assert results[0].succeeded
    assert results[0].stdout_path.read_text(encoding="utf-8").strip() == "loss 1.0"


def _init_repo(repo: Path) -> None:
    """Initialize a tiny git repository for tests."""
    _run(repo, "git init")
    _run(repo, "git config user.email coding-agent@example.invalid")
    _run(repo, "git config user.name CodingAgent")
    _run(repo, "git add train.py")
    _run(repo, "git commit -m init")


def _run(cwd: Path, command: str) -> None:
    """Run a shell command for test setup."""
    import subprocess

    subprocess.run(command, cwd=cwd, shell=True, check=True, capture_output=True, text=True)
