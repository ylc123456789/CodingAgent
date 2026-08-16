"""Deterministic spec collection tests."""
from __future__ import annotations

from pathlib import Path

from coding_agent.resources import collect_environment_spec, spec_fingerprint


def test_spec_shape_and_defaults(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy==1.26\n")
    spec = collect_environment_spec(tmp_path, python_version="3.11")
    assert spec["schema"] == "ENVIRONMENT_SPEC_V1"
    assert spec["python"] == "3.11"
    assert spec["os"] in ("linux", "macos", "windows")
    assert spec["arch"] in ("x86_64", "aarch64")
    assert spec["accelerator"]["type"] in ("cpu", "cuda")
    assert spec["dependency_files"][0]["path"] == "requirements.txt"
    assert len(spec["dependency_files"][0]["sha256"]) == 64


def test_spec_stable_when_notes_differ(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy==1.26\n")
    a = collect_environment_spec(tmp_path, python_version="3.11")
    a["notes"] = "first"
    b = collect_environment_spec(tmp_path, python_version="3.11")
    b["notes"] = "different note must not change identity"
    assert spec_fingerprint(a) == spec_fingerprint(b)


def test_spec_changes_when_requirements_change(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("numpy==1.26\n")
    before = spec_fingerprint(collect_environment_spec(tmp_path, python_version="3.11"))
    req.write_text("numpy==2.0\n")
    after = spec_fingerprint(collect_environment_spec(tmp_path, python_version="3.11"))
    assert before != after


def test_python_version_from_environment_yml(tmp_path):
    (tmp_path / "environment.yml").write_text(
        "name: demo\nchannels: [conda-forge]\ndependencies:\n  - python=3.10\n  - numpy\n"
    )
    spec = collect_environment_spec(tmp_path)
    assert spec["python"] == "3.10"


def test_python_version_defaults_to_interpreter(tmp_path):
    import sys
    spec = collect_environment_spec(tmp_path)
    assert spec["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_accelerator_override_wins(tmp_path):
    spec = collect_environment_spec(
        tmp_path, python_version="3.11", accelerator_type="cuda", accelerator_variant="cu121"
    )
    assert spec["accelerator"] == {"type": "cuda", "variant": "cu121"}


def test_dependency_files_sorted(tmp_path):
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "requirements.txt").write_text("numpy\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "requirements-dev.txt").write_text("pytest\n")
    spec = collect_environment_spec(tmp_path, python_version="3.11")
    paths = [f["path"] for f in spec["dependency_files"]]
    assert paths == sorted(paths)
    assert "requirements.txt" in paths
    assert "sub/requirements-dev.txt" in paths
