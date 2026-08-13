"""P2 contract tests: clone prep, env wrapping, env policy, bindings."""
import subprocess
from pathlib import Path

import pytest

from coding_agent.agent import _prepare_workspace
from coding_agent.models import CodeTaskSpec


def _make_source_repo(tmp_path: Path) -> Path:
    """Create a small git repo to serve as a clone source."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "train.py").write_text("print('hello')\n")
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "add", "train.py"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=source, check=True, capture_output=True)
    return source


def test_clone_into_empty_workspace(tmp_path):
    source = _make_source_repo(tmp_path)
    ws = tmp_path / "cloned"
    spec = CodeTaskSpec(
        workspace_path=ws,
        output_dir=tmp_path / "out",
        task_goal="Edit code.",
        repo_url=str(source),
    )
    _prepare_workspace(spec)
    assert (ws / "train.py").exists()
    assert (ws / ".git").exists()


def test_clone_refuses_nonempty_workspace(tmp_path):
    source = _make_source_repo(tmp_path)
    ws = tmp_path / "occupied"
    ws.mkdir()
    (ws / "conflicting.txt").write_text("user data")
    spec = CodeTaskSpec(
        workspace_path=ws,
        output_dir=tmp_path / "out",
        task_goal="Edit code.",
        repo_url=str(source),
    )
    with pytest.raises(RuntimeError, match="not empty"):
        _prepare_workspace(spec)
    # Original file untouched
    assert (ws / "conflicting.txt").read_text() == "user data"
    assert not (ws / "train.py").exists()


def test_no_repo_url_is_noop(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "x.txt").write_text("x")
    spec = CodeTaskSpec(
        workspace_path=ws,
        output_dir=tmp_path / "out",
        task_goal="Edit code.",
    )
    _prepare_workspace(spec)  # no error, no change
    assert (ws / "x.txt").exists()


def test_clone_failure_raises(tmp_path):
    ws = tmp_path / "cloned"
    spec = CodeTaskSpec(
        workspace_path=ws,
        output_dir=tmp_path / "out",
        task_goal="Edit code.",
        repo_url="https://example.invalid/does/not/exist.git",
    )
    with pytest.raises(RuntimeError, match="git clone failed"):
        _prepare_workspace(spec)
