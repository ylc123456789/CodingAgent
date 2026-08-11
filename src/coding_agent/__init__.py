"""Expose the public CodingAgent package API."""
from .agent import resume_code_task, run_code_question, run_code_task
from .session import list_sessions, read_session_card, session_status
from .models import (
    AgentState,
    CodeExplanation,
    CodeQuestionSpec,
    CodeTaskSpec,
    CommandResult,
    PatchReport,
    RepoContext,
    Snippet,
)

__all__ = [
    "AgentState",
    "CodeExplanation",
    "CodeQuestionSpec",
    "CodeTaskSpec",
    "CommandResult",
    "PatchReport",
    "RepoContext",
    "Snippet",
    "list_sessions",
    "read_session_card",
    "resume_code_task",
    "run_code_question",
    "run_code_task",
    "session_status",
]
