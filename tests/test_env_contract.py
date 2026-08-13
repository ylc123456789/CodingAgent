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


# ---- C3: env policy + conda wrap ----
from coding_agent.runtime.runner import _conda_executable, _wrap_conda, run_verify_commands
from coding_agent.runtime.safety import validate_env_command
from coding_agent.runtime.safety import SafetyError


def test_env_policy_auto_allows_everything():
    validate_env_command("pip install torch", "auto")
    validate_env_command("conda create -n x python", "auto")


def test_env_policy_frozen_blocks_all_mutation():
    with pytest.raises(SafetyError, match="frozen"):
        validate_env_command("pip install torch", "frozen")
    with pytest.raises(SafetyError, match="frozen"):
        validate_env_command("conda create -n x python", "frozen")
    validate_env_command("python train.py", "frozen")
    validate_env_command("grep loss train.py", "frozen")


def test_env_policy_reuse_only_blocks_heavy_and_env_ops():
    with pytest.raises(SafetyError, match="heavy framework"):
        validate_env_command("pip install torch", "reuse_only")
    with pytest.raises(SafetyError, match="heavy framework"):
        validate_env_command("conda install tensorflow", "reuse_only")
    with pytest.raises(SafetyError, match="reuse_only"):
        validate_env_command("conda create -n x python", "reuse_only")
    # small packages still allowed
    validate_env_command("pip install requests", "reuse_only")
    validate_env_command("python train.py", "reuse_only")


def test_conda_wrap_form(monkeypatch):
    monkeypatch.setattr(
        "coding_agent.runtime.runner._conda_executable",
        lambda: "/fake/conda",
    )
    wrapped = _wrap_conda("python train.py", "myenv")
    assert "conda" in wrapped
    assert "run" in wrapped
    assert "-n myenv" in wrapped
    assert "python train.py" in wrapped


def test_safety_runs_before_wrap(monkeypatch):
    """Dangerous command is rejected even when env_name would wrap it."""
    monkeypatch.setattr(
        "coding_agent.runtime.runner._conda_executable",
        lambda: "/fake/conda",
    )
    with pytest.raises(SafetyError):
        run_verify_commands(
            Path("/tmp"),
            ["rm -rf /"],
            Path("/tmp/logs"),
            30,
            env_name="myenv",
        )
