"""Test agent.yaml capability card against the V2 contract."""
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_card() -> dict:
    path = REPO_ROOT / "agent.yaml"
    assert path.exists(), "agent.yaml missing at repository root"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_card_required_fields():
    card = _load_card()
    for field in ("name", "role", "capabilities", "side_effects",
                  "input_contract", "output_contract", "status",
                  "description_for_router"):
        assert card.get(field), f"missing field: {field}"


def test_card_declares_modify_code():
    card = _load_card()
    assert "modify_code" in card["capabilities"]


def test_card_side_effects_workspace():
    """Code modification writes to the workspace; not environment-level."""
    card = _load_card()
    assert card["side_effects"] == "workspace"


def test_card_status_available():
    card = _load_card()
    assert card["status"] == "available"
