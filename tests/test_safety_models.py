"""Regression tests: models/ code directory must not be blocked."""
from pathlib import Path

import pytest

from coding_agent.runtime.safety import BLOCKED_PATH_PARTS, BLOCKED_SUFFIXES, ensure_path_allowed, SafetyError


def test_models_code_directory_is_allowed(tmp_path):
    """models/resnet.py is model CODE, not weights — must be editable."""
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "resnet.py").write_text("class ResNet: pass\n")
    result = ensure_path_allowed(tmp_path, "models/resnet.py")
    assert result == (tmp_path / "models" / "resnet.py").resolve()


def test_models_entry_removed_from_blocklist():
    assert "models" not in BLOCKED_PATH_PARTS


def test_weight_files_still_blocked_by_suffix(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "resnet.pth").write_bytes(b"\x00")
    with pytest.raises(SafetyError):
        ensure_path_allowed(tmp_path, "models/resnet.pth")
    assert ".pth" in BLOCKED_SUFFIXES


def test_weight_directories_still_blocked(tmp_path):
    (tmp_path / "weights").mkdir()
    (tmp_path / "weights" / "w.bin").write_bytes(b"\x00")
    with pytest.raises(SafetyError):
        ensure_path_allowed(tmp_path, "weights/w.bin")
    (tmp_path / "checkpoints").mkdir()
    with pytest.raises(SafetyError):
        ensure_path_allowed(tmp_path, "checkpoints/ckpt.bin")
