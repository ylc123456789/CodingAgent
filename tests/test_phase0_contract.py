"""Phase 0 compatibility locks for behavior-preserving refactors."""

from __future__ import annotations

import hashlib
import json

import coding_agent
from coding_agent.controller.prompts import ACTION_SCHEMA, QA_ACTION_SCHEMA, QA_SYSTEM
from coding_agent.models import AgentState, CodeQuestionSpec, CodeTaskSpec, PatchReport


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _schema_hash(value: dict) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return _sha256(text)


def test_public_package_contract() -> None:
    assert coding_agent.__all__ == [
        "AgentState", "CodeExplanation", "CodeQuestionSpec", "CodeTaskSpec",
        "CommandResult", "PatchReport", "RepoContext", "Snippet", "list_sessions",
        "read_session_card", "resume_code_task", "run_code_question",
        "run_code_task", "session_status",
    ]


def test_controller_prompt_and_action_contracts() -> None:
    assert _sha256(QA_SYSTEM) == "342771495bbdcc7a68b48ccaca043af8b6a47663b96a21a749a31ef0cf29bf54"
    assert _schema_hash(ACTION_SCHEMA) == "c6a10896f41487ff3f82db36823038b751555fcd854f417f9d35253c0a5c97c5"
    assert _schema_hash(QA_ACTION_SCHEMA) == "eb854bb98ef3587a173e63cb4b22ad2aacad4f6adefb538ca3613a69a77c0461"


def test_public_model_field_contracts() -> None:
    assert list(CodeTaskSpec.model_fields) == [
        "workspace_path", "task_goal", "constraints", "verify_commands",
        "allowed_paths", "max_steps", "max_extra_steps_after_progress",
        "patch_repair_attempts", "timeout_seconds", "max_context_tokens",
        "model_context_window_tokens", "context_margin_ratio",
        "context_output_reserve_tokens", "api_base", "api_key_env", "model",
        "read_only", "session_id", "parent_run", "output_dir",
        "repo_url", "branch", "env_policy", "env_name", "resource_root",
        "requires_gpu", "accelerator_variant", "pip_index_profile",
        "dataset_cache_dir", "mirror_profile",
        "project_ref",
    ]
    assert list(CodeQuestionSpec.model_fields) == [
        "workspace_path", "question", "output_dir", "context_hint", "constraints",
        "max_steps", "timeout_seconds", "session_id", "parent_run", "model",
        "api_base", "api_key_env", "max_context_tokens",
        "model_context_window_tokens", "context_margin_ratio",
        "context_output_reserve_tokens",
    ]
    assert list(AgentState.model_fields) == ["task", "started_at", "steps", "dataset_links", "report"]
    assert list(PatchReport.model_fields) == [
        "status", "changed_files", "diff_path", "verification_results", "summary",
        "produced_files", "residual_risks",
    ]
