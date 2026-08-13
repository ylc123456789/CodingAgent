"""Tests: clone timeout, retry, and half-finished cleanup."""
import subprocess
from pathlib import Path

import pytest

from coding_agent.agent import _prepare_workspace
from coding_agent.models import CodeTaskSpec


def _spec(tmp_path, repo_url):
    return CodeTaskSpec(
        workspace_path=tmp_path / "ws",
        output_dir=tmp_path / "out",
        task_goal="x",
        repo_url=repo_url,
    )


def test_clone_failure_cleans_partial_dir(tmp_path, monkeypatch):
    """A failed clone attempt leaves no half-finished directory."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        ws = Path(command[-1])
        ws.mkdir(exist_ok=True)
        (ws / "partial.txt").write_text("partial")
        return subprocess.CompletedProcess(
            command, returncode=128, stdout="", stderr="network error"
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="network error"):
        _prepare_workspace(_spec(tmp_path, "https://example.invalid/repo.git"))

    assert len(calls) == 3  # retried
    ws = tmp_path / "ws"
    assert not (ws / "partial.txt").exists()  # cleaned between attempts


def test_clone_retries_then_succeeds(tmp_path, monkeypatch):
    """First attempt fails, second succeeds."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        ws = Path(command[-1])
        if len(calls) == 1:
            ws.mkdir(exist_ok=True)
            (ws / "partial.txt").write_text("partial")
            return subprocess.CompletedProcess(command, 128, "", "boom")
        ws.mkdir(exist_ok=True)
        (ws / "train.py").write_text("print('hi')")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("time.sleep", lambda s: None)

    _prepare_workspace(_spec(tmp_path, "https://example.invalid/repo.git"))

    assert len(calls) == 2
    ws = tmp_path / "ws"
    assert (ws / "train.py").exists()
    assert not (ws / "partial.txt").exists()


def test_clone_timeout_raises_after_retries(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 300)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="timed out"):
        _prepare_workspace(_spec(tmp_path, "https://example.invalid/repo.git"))

    assert len(calls) == 3


def test_nonempty_workspace_never_cleaned(tmp_path, monkeypatch):
    """The pre-check error must not delete the user's directory."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "precious.txt").write_text("keep me")
    calls = []

    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))

    with pytest.raises(RuntimeError, match="not empty"):
        _prepare_workspace(_spec(tmp_path, "https://example.invalid/repo.git"))

    assert len(calls) == 0  # clone never attempted
    assert (ws / "precious.txt").exists()
