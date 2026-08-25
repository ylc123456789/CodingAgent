"""Readonly caller inputs are addressable by id, never by physical path."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from coding_agent import CodeTaskSpec, ReadonlyInput
from coding_agent.controller.actions import execute_action
from coding_agent.controller.prompts import choose_next_action
from coding_agent.models import AgentState, ControllerAction, RepoContext


class DummyClient:
    def complete_json(self, _system, _user):
        return {"action": "finish", "status": "completed"}


def _spec(tmp_path: Path, readonly_inputs=None) -> CodeTaskSpec:
    return CodeTaskSpec(
        workspace_path=tmp_path / "repo",
        output_dir=tmp_path / "out",
        task_goal="aggregate results",
        readonly_inputs=readonly_inputs or [],
    )


def test_read_input_uses_id_and_returns_content(tmp_path):
    source = tmp_path / "result.json"
    source.write_text('{"accuracy": 0.91}', encoding="utf-8")
    spec = _spec(tmp_path, [
        ReadonlyInput(id="baseline", path=source, description="baseline result"),
    ])

    record = execute_action(
        spec,
        ControllerAction(action="read_input", input_id="baseline"),
        tmp_path / "out",
        1,
        DummyClient(),
    )

    assert record.error is None
    assert '"accuracy": 0.91' in record.observation
    assert str(source) not in record.observation


def test_unknown_input_id_is_recoverable(tmp_path):
    record = execute_action(
        _spec(tmp_path),
        ControllerAction(action="read_input", input_id="missing"),
        tmp_path / "out",
        1,
        DummyClient(),
    )

    assert record.error == "Unknown readonly input id: missing"


def test_readonly_inputs_must_exist_and_have_unique_ids(tmp_path):
    with pytest.raises(ValidationError, match="readonly input is not a file"):
        ReadonlyInput(id="missing", path=tmp_path / "missing.json")

    source = tmp_path / "result.json"
    source.write_text("{}", encoding="utf-8")
    item = ReadonlyInput(id="result", path=source)
    with pytest.raises(ValidationError, match="ids must be unique"):
        _spec(tmp_path, [item, item])


def test_prompt_exposes_ids_but_not_physical_paths(tmp_path):
    source = tmp_path / "result.json"
    source.write_text("{}", encoding="utf-8")
    spec = _spec(tmp_path, [ReadonlyInput(
        id="result", path=source, description="experiment result",
    )])

    class CaptureClient:
        payload = {}

        def complete_json(self, _system, user):
            self.payload = json.loads(user)
            return {"action": "finish", "status": "completed"}

    client = CaptureClient()
    choose_next_action(
        spec,
        AgentState(task=spec),
        RepoContext(workspace_path=spec.workspace_path, tree=[], snippets=[]),
        client,
    )

    assert client.payload["readonly_inputs"] == [
        {"id": "result", "description": "experiment result"},
    ]
    assert str(source) not in json.dumps(client.payload)
