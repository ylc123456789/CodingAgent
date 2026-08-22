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


def test_python_version_defaults_to_contract_default(tmp_path):
    """No explicit version and no environment.yml pin: the contract default
    (3.10) applies — the caller's own interpreter is NOT identity."""
    spec = collect_environment_spec(tmp_path)
    assert spec["python"] == "3.10"


def test_accelerator_from_requires_gpu_and_variant(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coding_agent.resources._host_has_gpu", lambda: True
    )
    spec = collect_environment_spec(
        tmp_path, python_version="3.11",
        requires_gpu=True, accelerator_variant="cu121",
    )
    assert spec["accelerator"] == {"type": "cuda", "variant": "cu121"}


def test_accelerator_cpu_when_no_gpu_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coding_agent.resources._host_has_gpu", lambda: True
    )
    spec = collect_environment_spec(tmp_path, python_version="3.11")
    assert spec["accelerator"] == {"type": "cpu", "variant": ""}


def test_accelerator_cpu_when_host_has_no_gpu(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "coding_agent.resources._host_has_gpu", lambda: False
    )
    spec = collect_environment_spec(tmp_path, python_version="3.11", requires_gpu=True)
    assert spec["accelerator"] == {"type": "cpu", "variant": ""}


def test_pip_index_profile_passthrough(tmp_path):
    spec = collect_environment_spec(
        tmp_path, python_version="3.11", pip_index_profile="aliyun"
    )
    assert spec["pip_index_profile"] == "aliyun"


def test_raw_bytes_hashing_includes_contract_file_set(tmp_path):
    import hashlib
    (tmp_path / "requirements.txt").write_bytes(b"numpy\n")
    (tmp_path / "setup.cfg").write_bytes(b"[metadata]\n")
    (tmp_path / "poetry.lock").write_bytes(b"lockdata")
    spec = collect_environment_spec(tmp_path, python_version="3.11")
    paths = {f["path"] for f in spec["dependency_files"]}
    assert {"requirements.txt", "setup.cfg", "poetry.lock"} <= paths
    req = [f for f in spec["dependency_files"] if f["path"] == "requirements.txt"][0]
    expected = hashlib.sha256(b"numpy\n").hexdigest()
    assert req["sha256"] == expected


def test_raw_bytes_vs_decoded_hash_differ(tmp_path):
    """Guards against regressing to decode-then-hash.

    Invalid UTF-8 bytes are silently dropped by errors=ignore, so the
    decoded hash differs from the raw-byte hash the contract collects.
    """
    import hashlib
    (tmp_path / "requirements.txt").write_bytes(b"numpy\n\xff\xfe")
    spec = collect_environment_spec(tmp_path, python_version="3.11")
    entry = [f for f in spec["dependency_files"] if f["path"] == "requirements.txt"][0]
    assert entry["sha256"] == hashlib.sha256(b"numpy\n\xff\xfe").hexdigest()
    decoded = b"numpy\n\xff\xfe".decode("utf-8", errors="ignore").encode("utf-8")
    assert entry["sha256"] != hashlib.sha256(decoded).hexdigest()


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


def test_mirror_profile_maps_to_pip_index_profile(tmp_path, monkeypatch):
    """mirror_profile feeds pip_index_profile in spec collection (parity)."""
    from coding_agent.agent import _prepare_environment

    captured = {}

    def fake_collect(workspace, python_version="", requires_gpu=False,
                     accelerator_variant="", pip_index_profile=""):
        captured["pip"] = pip_index_profile
        return {"schema": "ENVIRONMENT_SPEC_V1", "python": "3.11",
                "os": "linux", "arch": "x86_64",
                "accelerator": {"type": "cpu", "variant": ""},
                "dependency_files": [], "channels": [],
                "pip_index_profile": pip_index_profile,
                "framework_constraints": [], "notes": ""}

    from coding_agent import CodeTaskSpec
    spec = CodeTaskSpec(
        workspace_path=tmp_path, output_dir=tmp_path / "out", task_goal="x",
        resource_root=str(tmp_path / "resources"),
        mirror_profile="cn", env_policy="auto",
    )
    monkeypatch.setattr("coding_agent.resources.collect_environment_spec", fake_collect)
    monkeypatch.setattr("coding_agent.resources.create_or_reuse_environment",
                        lambda *a, **k: {"env_id": "e", "prefix": "/p",
                                         "spec_fingerprint": "0"*64,
                                         "resolved_fingerprint": "1"*64,
                                         "certification": "verification"})
    _prepare_environment(spec)
    assert captured["pip"] == "cn"
