"""Test model-aware context budget selection."""
from pathlib import Path

from coding_agent.context_policy import resolve_context_policy
from coding_agent.controller import execute_action
from coding_agent.models import CodeTaskSpec, ControllerAction


class DummyClient:
    """Placeholder client used by tests."""
    pass


def test_deepseek_v4_pro_gets_large_context_budget(tmp_path: Path) -> None:
    """Verify deepseek v4 pro gets large context budget."""
    spec = CodeTaskSpec(repo_path=tmp_path, task_goal="Edit code.", model="deepseek-v4-pro")

    policy = resolve_context_policy(spec)

    assert policy.context_window_tokens == 1_000_000
    assert policy.input_budget_tokens == 800_000
    assert policy.read_file_chars > 100_000
    assert policy.snippet_count > 24


def test_context_budget_override_is_respected(tmp_path: Path) -> None:
    """Verify context budget override is respected."""
    spec = CodeTaskSpec(repo_path=tmp_path, task_goal="Edit code.", model="deepseek-v4-pro", max_context_tokens=64_000)

    policy = resolve_context_policy(spec)

    assert policy.input_budget_tokens == 64_000
    assert policy.read_file_chars < 100_000


def test_read_file_uses_model_context_policy(tmp_path: Path) -> None:
    """Verify read file uses model context policy."""
    path = tmp_path / "large.py"
    path.write_text("a" * 80_000, encoding="utf-8")
    small = CodeTaskSpec(
        repo_path=tmp_path,
        task_goal="Read file.",
        model="unknown-small-model",
        max_context_tokens=8_000,
    )
    large = CodeTaskSpec(repo_path=tmp_path, task_goal="Read file.", model="deepseek-v4-pro")
    action = ControllerAction(action="read_file", path="large.py")

    small_obs = execute_action(small, action, tmp_path / "small_out", 1, DummyClient()).observation
    large_obs = execute_action(large, action, tmp_path / "large_out", 1, DummyClient()).observation

    assert len(small_obs) < len(large_obs)
    assert len(large_obs) == 80_000
    assert "truncated middle" in small_obs
