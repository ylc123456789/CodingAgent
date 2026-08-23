"""Test session card write, read, list, status."""
import tempfile
from pathlib import Path

import pytest

from coding_agent.session import (
    _generate_session_id,
    write_session_card,
    read_session_card,
    list_sessions,
    session_status,
)


class FakeSpec:
    session_id = ""
    parent_run = None
    workspace_path = Path("/tmp/test")

    def __init__(self, sid="", parent=None):
        self.session_id = sid
        self.parent_run = parent


class FakeReport:
    status = "completed"
    summary = "Test summary"
    diff_path = Path("/tmp/test/diff.patch")


def test_generate_session_id_has_prefix():
    sid = _generate_session_id("code")
    assert sid.startswith("code-")
    assert len(sid) > 10


def test_write_and_read_card(tmp_path):
    spec = FakeSpec()
    report = FakeReport()
    sid = write_session_card(spec, report, tmp_path)
    assert sid.startswith("code-")

    card = read_session_card(tmp_path)
    assert card is not None
    assert card["schema_version"] == 1
    assert card["module"] == "codingagent"
    assert card["kind"] == "task_session"
    assert card["status"] == "completed"
    assert "Test summary" in card["summary"]
    assert len(card["key_artifacts"]) >= 1


def test_card_with_explicit_session_id(tmp_path):
    spec = FakeSpec(sid="my-custom-id")
    report = FakeReport()
    sid = write_session_card(spec, report, tmp_path, kind="qa_session")
    assert sid == "my-custom-id"

    card = read_session_card(tmp_path)
    assert card["session_id"] == "my-custom-id"
    assert card["kind"] == "qa_session"


def test_card_with_parent(tmp_path):
    spec = FakeSpec(parent={"module": "resagent", "run_id": "res-123", "task_id": "task_1"})
    report = FakeReport()
    write_session_card(spec, report, tmp_path)

    card = read_session_card(tmp_path)
    assert card["parent"] == {"module": "resagent", "run_id": "res-123", "task_id": "task_1"}


def test_content_addressed_environment_binding_keeps_logical_id(tmp_path):
    spec = FakeSpec()
    spec.workspace_path = tmp_path
    spec.env_name = "/resources/conda-envs/resenv_project_abc123"
    spec.env_policy = "auto"
    environment_info = {
        "env_id": "resenv_project_abc123",
        "manifest_path": "/resources/environments/resenv_project_abc123/manifest.json",
        "prefix": spec.env_name,
        "spec_fingerprint": "a" * 64,
        "resolved_fingerprint": "b" * 64,
        "certification": "verification",
    }

    write_session_card(
        spec, FakeReport(), tmp_path, environment_info=environment_info,
    )

    environment = read_session_card(tmp_path)["bindings"]["environment"]
    assert environment["env_id"] == "resenv_project_abc123"
    assert environment["name"] == spec.env_name
    assert environment["prefix"] == spec.env_name


def test_list_sessions(tmp_path):
    # Create two session dirs
    d1 = tmp_path / "a"
    d1.mkdir()
    write_session_card(FakeSpec(sid="test-1"), FakeReport(), d1)

    d2 = tmp_path / "b"
    d2.mkdir()
    write_session_card(FakeSpec(sid="test-2"), FakeReport(), d2, kind="qa_session")

    results = list_sessions(tmp_path)
    assert len(results) == 2
    ids = [r["session_id"] for r in results]
    assert "test-1" in ids
    assert "test-2" in ids


def test_session_status(tmp_path):
    write_session_card(FakeSpec(sid="test-1"), FakeReport(), tmp_path)
    # Create a minimal state.json
    import json
    (tmp_path / "state.json").write_text(json.dumps({
        "steps": [{"step": 1}, {"step": 2}],
        "report": {"summary": "State report summary"},
    }))

    status = session_status(tmp_path)
    assert status["session_id"] == "test-1"
    assert status["steps_count"] == 2
    assert "State report summary" in status["report_summary"]


def test_read_missing_card(tmp_path):
    assert read_session_card(tmp_path) is None


def test_list_empty_dir(tmp_path):
    assert list_sessions(tmp_path) == []



def test_resume_preserves_created_at(tmp_path):
    """Rewriting a session card preserves the original created_at."""
    from coding_agent.session import write_session_card, read_session_card
    import time

    class FakeSpec:
        session_id = "test-preserve"
        parent_run = None
        workspace_path = tmp_path

    class FakeReport:
        status = "completed"
        summary = "First run"
        diff_path = None

    write_session_card(FakeSpec(), FakeReport(), tmp_path)
    card1 = read_session_card(tmp_path)
    created1 = card1["created_at"]

    time.sleep(1)

    FakeReport.summary = "Second run"
    write_session_card(FakeSpec(), FakeReport(), tmp_path)
    card2 = read_session_card(tmp_path)

    assert card2["created_at"] == created1
    assert card2["updated_at"] != created1
    assert card2["summary"] == "Second run"


def test_session_status_no_card(tmp_path):
    assert "error" in session_status(tmp_path)
