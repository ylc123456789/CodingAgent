"""Compatibility re-exports; implementations live in coding_agent.context.policy."""
from .context.policy import ContextPolicy, resolve_context_policy

__all__ = ["ContextPolicy", "resolve_context_policy"]
