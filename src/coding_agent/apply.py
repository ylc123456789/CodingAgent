"""Compatibility re-exports; implementations live in coding_agent.runtime.apply."""
from .runtime.apply import (
    PatchApplyError,
    apply_patch_text,
    check_patch_text,
    current_diff,
    extract_patch_paths,
    normalize_patch_text,
    validate_patch,
)

__all__ = [
    "PatchApplyError", "apply_patch_text", "check_patch_text", "current_diff",
    "extract_patch_paths", "normalize_patch_text", "validate_patch",
]
