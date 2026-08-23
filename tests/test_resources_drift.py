"""Resolved inventory and drift detection tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.resources import (
    EnvironmentBlockedError,
    check_manifest_freshness,
    compute_resolved_inventory,
    drift_state,
    resolved_fingerprint,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "m2_contracts"


def _fixture_manifest() -> dict:
    return json.loads(
        (FIXTURES / "ready_experiment.json").read_text(encoding="utf-8")
    )


def _resolved() -> dict:
    return {
        "python": "3.11.9",
        "conda_inventory_sha256": "aa" * 32,
        "pip_inventory_sha256": "bb" * 32,
        "frameworks": {"torch": {"version": "2.6.0", "cuda": "12.4"}},
        "abi_summary": "glibc2.35",
    }


def test_drift_detected_on_resolved_mismatch():
    m = _fixture_manifest()
    assert drift_state(m, "00" * 32) is True


def test_no_drift_when_fingerprints_match():
    m = _fixture_manifest()
    assert drift_state(m, m["resolved_fingerprint"]) is False


def test_missing_resolved_fingerprint_is_drifted():
    m = _fixture_manifest()
    m["resolved_fingerprint"] = None
    assert drift_state(m, "00" * 32) is True


def test_check_freshness_blocks_drifted(monkeypatch, tmp_path):
    m = _fixture_manifest()
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: _resolved(),
    )
    with pytest.raises(EnvironmentBlockedError, match="drifted"):
        check_manifest_freshness(m, tmp_path / "env")


def test_check_freshness_passes_clean(monkeypatch, tmp_path):
    m = _fixture_manifest()
    resolved = _resolved()
    m["resolved_fingerprint"] = resolved_fingerprint(resolved)
    monkeypatch.setattr(
        "coding_agent.resources.compute_resolved_inventory",
        lambda prefix: resolved,
    )
    check_manifest_freshness(m, tmp_path / "env")  # no raise


def test_missing_prefix_is_blocked(tmp_path):
    with pytest.raises(EnvironmentBlockedError, match="does not exist"):
        compute_resolved_inventory(tmp_path / "nonexistent")


def test_inventory_blocks_when_conda_cannot_be_located(tmp_path, monkeypatch):
    prefix = tmp_path / "env"
    python = prefix / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("coding_agent.resources._find_conda_executable", lambda: None)

    with pytest.raises(EnvironmentBlockedError, match="cannot locate conda"):
        compute_resolved_inventory(prefix)


def test_conda_lookup_uses_explicit_executable_without_path(tmp_path, monkeypatch):
    conda = tmp_path / "miniconda" / "bin" / "conda"
    conda.parent.mkdir(parents=True)
    conda.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("CONDA_EXE", str(conda))
    monkeypatch.setattr("shutil.which", lambda name: None)

    from coding_agent.resources import _find_conda_executable

    assert _find_conda_executable() == str(conda)
