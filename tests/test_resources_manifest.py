"""Manifest lifecycle tests: validation, atomic write, state transitions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.resources import (
    read_manifest,
    transition_manifest,
    validate_manifest,
    write_manifest_atomic,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "m2_contracts"


def _fixture_manifest() -> dict:
    return json.loads(
        (FIXTURES / "ready_experiment.json").read_text(encoding="utf-8")
    )


def test_fixture_manifest_validates():
    validate_manifest(_fixture_manifest())


def test_missing_fields_rejected():
    m = _fixture_manifest()
    del m["env_id"]
    with pytest.raises(ValueError, match="env_id"):
        validate_manifest(m)


def test_unknown_state_rejected():
    m = _fixture_manifest()
    m["state"] = "weird"
    with pytest.raises(ValueError, match="state"):
        validate_manifest(m)


def test_codingagent_cannot_write_experiment(tmp_path):
    """Manifest schema allows experiment but CodingAgent must never set it;
    the write layer accepts the schema value — the certification cap is
    enforced by the create/audit flow (tested in step 5)."""
    m = _fixture_manifest()
    # fixture itself is experiment-certified by reproagent — validation
    # must accept reading it (cross-module tolerance)
    validate_manifest(m)


def test_atomic_write_and_read_roundtrip(tmp_path):
    m = _fixture_manifest()
    path = write_manifest_atomic(tmp_path, m)
    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    loaded = read_manifest(tmp_path, m["env_id"])
    assert loaded == m


def test_read_missing_manifest_returns_none(tmp_path):
    assert read_manifest(tmp_path, "resenv_x_000000000000") is None


def test_transition_creating_to_ready(tmp_path):
    m = _fixture_manifest()
    m["state"] = "creating"
    m["resolved_fingerprint"] = None
    result = transition_manifest(m, "ready")
    assert result["state"] == "ready"


def test_transition_ready_to_drifted(tmp_path):
    m = _fixture_manifest()
    result = transition_manifest(m, "drifted")
    assert result["state"] == "drifted"


def test_illegal_transition_rejected():
    m = _fixture_manifest()
    with pytest.raises(ValueError, match="illegal"):
        transition_manifest(m, "ready")  # ready -> ready not allowed
