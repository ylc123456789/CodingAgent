"""Build controller prompts and compact step history."""
from __future__ import annotations

import json

from ..runtime.apply import current_diff
from ..runtime.dataset_cache import render_dataset_block
from ..context.policy import ContextPolicy, resolve_context_policy
from ..llm import LLMClient
from ..models import AgentState, CodeTaskSpec, ControllerAction, StepRecord

ACTION_SCHEMA = {
    "action": "list_tree|read_file|read_input|search|replace_text|insert_before|insert_after|apply_patch|write_file|run_command|finish|ask_user",
    "reasoning": "brief reason for this next action",
    "path": "relative file path for read_file or structured edits, optional",
    "input_id": "id from readonly_inputs for read_input, optional",
    "start_line": "optional 1-based start line for read_file",
    "end_line": "optional 1-based inclusive end line for read_file",
    "query": "search query for search, optional",
    "command": "verification command for run_command, optional",
    "patch": "unified diff for apply_patch, optional",
    "content": "full file content for write_file when creating or overwriting a file",
    "old_text": "exact text copied from the current file for replace_text",
    "new_text": "replacement text for replace_text",
    "anchor_text": "exact unique anchor copied from the current file for insert_before/insert_after; prefer several adjacent lines over a short common line",
    "insert_text": "text to insert before or after anchor_text",
    "occurrence_index": "optional 1-based match index only when a repeated anchor is intentional and read_file/search context proves the target occurrence",
    "status": "completed|failed|blocked|needs_user_input for finish/ask_user",
    "summary": "final or user-facing summary, optional",
    "residual_risks": ["risk strings for finish/ask_user"],
}




# Read-only action set for code question answering
QA_ACTION_SCHEMA = {
    "action": "list_tree|read_file|search|run_command|finish|ask_user",
    "reasoning": "brief reason for this next action",
    "path": "relative file path for read_file, optional",
    "start_line": "optional 1-based start line for read_file",
    "end_line": "optional 1-based inclusive end line for read_file",
    "query": "search query for search, optional",
    "command": "read-only shell command for run_command (allowed: ls, cat, head, tail, grep, rg, find, wc, file, pwd, tree)",
    "status": "completed|failed|blocked|needs_user_input for finish/ask_user",
    "summary": "final answer or user-facing summary; use markdown",
    "residual_risks": ["risk strings for finish/ask_user"],
}

QA_SYSTEM = (
    "You are a code understanding agent. Answer questions about the repository "
    "by reading files (prefer read_file over grep to get exact code context) "
    "and running read-only shell commands (grep, find, ls, cat, etc.). "
    "Always read_file before citing line numbers to confirm accuracy. "
    "Your answer MUST include: (1) file paths and line numbers for every claim, "
    "(2) relevant code snippets copied from the files, "
    "(3) explicit uncertainty statements where applicable. "
    "You CANNOT modify any files — write actions are disabled. "
    "Use finish with status=completed (or failed if you cannot answer), "
    "and a well-structured markdown answer in the summary field. "
    "Return only JSON matching the schema."
)



def _env_policy_guidance(spec) -> str:
    """Render environment policy instructions for the prompt."""
    policy = getattr(spec, "env_policy", "auto")
    env_name = getattr(spec, "env_name", "")
    if policy == "reuse_only":
        return (
            f"Environment policy: reuse_only. Run verification inside the existing "
            f"environment {env_name!r}. You may install small missing packages, but MUST NOT "
            f"install, upgrade, or remove heavy frameworks (torch, tensorflow, jax, ...) and "
            f"MUST NOT create or delete environments."
        )
    if policy == "frozen":
        return (
            f"Environment policy: frozen. Run verification inside the existing environment "
            f"{env_name!r}. You MUST NOT modify the environment or install anything. If a "
            f"dependency is missing, report it honestly in your summary and residual_risks."
        )
    return (
        "Environment policy: auto. You may create conda environments, install packages, "
        "and configure dependencies as needed for verification."
    )


def _mirror_block(spec: CodeTaskSpec) -> str:
    """Render the mirror policy block with profile-specific guidance."""
    profile = spec.mirror_profile
    if not profile or profile == "none":
        return "Mirror policy: none."
    lines = [
        f"Mirror policy: {profile}.",
        "For pip: use -i https://mirrors.aliyun.com/pypi/simple",
        "Avoid --index-url https://download.pytorch.org/whl/ — overrides domestic mirrors.",
    ]
    if profile == "autodl":
        lines.append(
            "Prefer plain pip pins (torch==2.6.0 torchvision==0.21.0). Only use "
            "-f aliyun pytorch-wheels for +cuXXX wheels."
        )
    return "\n".join(lines)


def choose_next_action(spec: CodeTaskSpec, state: AgentState, context, client: LLMClient) -> ControllerAction:
    """Ask the model to choose the next controller action."""
    policy = resolve_context_policy(spec)
    is_qa = getattr(spec, "read_only", False)
    system = QA_SYSTEM if is_qa else (
        "You are a coding agent controller inspired by modern agentic coding tools. "
        "Choose exactly one next action from the allowed action set. "
        "After reading a file, prefer structured edit actions (replace_text, insert_before, insert_after) for small local edits. "
        "Do not repeatedly read the same file when recent_file_observations already contain the needed text; make progress "
        "by editing, searching for a specific symbol, running verification, or finishing. "
        "Use exact old_text or anchor_text copied from the current file. For inserts, prefer a unique multi-line "
        "anchor that includes nearby context instead of a short common line. Prefer write_file (full content) for any change larger than a few lines. "
        "apply_patch is the absolute last resort: line numbers in diff hunks frequently fail validation, so never start with it. "
        "Use finish only after the diff and verification evidence satisfy the task, or when failure is clear. "
        "Never silently change existing behavior; prefer adding new code over modifying existing logic. "
        "For insert_before/insert_after anchors: prefer 2-4 adjacent lines as anchor, including the line above the target. "
        "Never use anchors consisting only of whitespace and punctuation (e.g. a closing parenthesis alone). "
        "When nesting is deep, include the parent construct opening line in the anchor. "
        "Use read_input with an input_id to inspect caller-provided read-only files; "
        "never pass their physical paths to repository file or patch actions. "
        "Return only JSON matching the schema."
    )
    if not is_qa:
        system += "\n\n" + _mirror_block(spec)
    user = {
        "task_goal": spec.task_goal,
        "constraints": spec.constraints,
        "verify_commands": spec.verify_commands,
        "env_guidance": _env_policy_guidance(spec),
        "dataset_cache": render_dataset_block(spec, spec.workspace_path, state.dataset_links),
        "allowed_paths": spec.allowed_paths,
        "readonly_inputs": [
            {"id": item.id, "description": item.description}
            for item in spec.readonly_inputs
        ],
        "context_budget": {
            "context_window_tokens": policy.context_window_tokens,
            "input_budget_tokens": policy.input_budget_tokens,
            "margin_ratio": spec.context_margin_ratio,
            "output_reserve_tokens": spec.context_output_reserve_tokens,
        },
        "repo_tree": context.tree[:policy.repo_tree_limit],
        "snippets": [snippet.model_dump() for snippet in context.snippets[:policy.snippet_count]],
        "current_diff_tail": current_diff(spec.workspace_path)[-policy.diff_chars:],
        "remaining_base_steps": max(spec.max_steps - len(state.steps), 0),
        "remaining_hard_steps": max(spec.max_steps + spec.max_extra_steps_after_progress - len(state.steps), 0),
        "progress_hints": _progress_hints(spec, state.steps),
        "recent_file_observations": _recent_file_observations(state.steps, policy),
        "steps": [_compact_step(step, policy) for step in state.steps[-10:]],
        "available_actions": QA_ACTION_SCHEMA if getattr(spec, "read_only", False) else ACTION_SCHEMA,
    }
    return ControllerAction.model_validate(client.complete_json(system, json.dumps(user, indent=2)))


def _recent_file_observations(steps: list[StepRecord], policy: ContextPolicy | None = None) -> list[dict[str, object]]:
    """Return recent read-file observations for prompt reuse."""
    limit = policy.recent_file_count if policy else 2
    char_limit = policy.recent_file_chars if policy else 24_000
    observations = []
    seen = set()
    for step in reversed(steps):
        action = step.action
        if action.action != "read_file" or not action.path or action.path in seen:
            continue
        seen.add(action.path)
        observations.append({
            "path": action.path,
            "start_line": action.start_line,
            "end_line": action.end_line,
            "chars": len(step.observation),
            "text": step.observation[:char_limit],
        })
        if len(observations) >= limit:
            break
    return observations


def _progress_hints(spec: CodeTaskSpec, steps: list[StepRecord]) -> list[str]:
    """Build short hints that discourage stalled behavior."""
    hints = []
    if not steps:
        return hints
    last = steps[-1].action
    repeated_reads = 0
    for step in reversed(steps):
        action = step.action
        if action.action == "read_file" and last.action == "read_file" and action.path == last.path:
            repeated_reads += 1
        else:
            break
    if repeated_reads >= 2 and last.path:
        hints.append(
            f"{last.path} has already been read {repeated_reads} consecutive times; use the recent_file_observations text to edit, search a specific symbol, run verification, or finish instead of reading it again."
        )
    remaining_base = spec.max_steps - len(steps)
    if remaining_base <= 4:
        hints.append("The base step budget is nearly exhausted; prefer concrete edits, verification, or finish over broad exploration.")
    last_change_step = max((step.step for step in steps if step.changed_files), default=0)
    last_verify_step = max((step.step for step in steps if step.verification_results), default=0)
    if last_change_step and last_verify_step < last_change_step:
        hints.append("Files changed after the last verification; run verification before finish.")
    return hints


def _compact_step(step: StepRecord, policy: ContextPolicy | None = None) -> dict[str, object]:
    """Compact a step record for the next prompt."""
    observation_chars = policy.step_observation_chars if policy else 2_000
    return {
        "step": step.step,
        "action": step.action.action,
        "reasoning": step.action.reasoning,
        "observation_tail": step.observation[-observation_chars:],
        "changed_files": step.changed_files,
        "verification": [
            {"command": result.command, "returncode": result.returncode, "timed_out": result.timed_out}
            for result in step.verification_results
        ],
        "error": step.error,
    }


