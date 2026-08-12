"""Compatibility re-exports; implementations live in coding_agent.runtime.edits."""
from .runtime.edits import (
    StructuredEditError,
    find_all,
    insert_after_anchor,
    insert_before_anchor,
    replace_text_once,
)

__all__ = [
    "StructuredEditError", "find_all", "insert_after_anchor",
    "insert_before_anchor", "replace_text_once",
]
