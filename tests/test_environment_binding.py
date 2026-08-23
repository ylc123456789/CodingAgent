"""Environment binding regressions."""

from coding_agent import CodeTaskSpec
from coding_agent.agent import _prepare_environment


def test_bound_environment_uses_registered_prefix(tmp_path, monkeypatch):
    """Environment ids are resolved to prefixes before command execution."""
    prefix = str(tmp_path / "resources" / "conda-envs" / "resenv_demo")
    spec = CodeTaskSpec(
        workspace_path=tmp_path,
        output_dir=tmp_path / "out",
        task_goal="x",
        resource_root=str(tmp_path / "resources"),
        env_policy="frozen",
        env_name="resenv_demo_abc123",
    )
    monkeypatch.setattr(
        "coding_agent.resources.collect_environment_spec",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "coding_agent.resources.bind_existing_environment",
        lambda *args, **kwargs: {
            "env_id": "resenv_demo_abc123",
            "prefix": prefix,
            "spec_fingerprint": "0" * 64,
            "resolved_fingerprint": "1" * 64,
            "certification": "verification",
        },
    )

    _prepare_environment(spec)

    assert spec.env_name == prefix
