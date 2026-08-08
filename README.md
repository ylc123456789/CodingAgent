# CodingAgent

CodingAgent is a lightweight repo-scoped coding agent for code-related tasks. It is designed to be developed as a standalone project and later copied into `reproagent` as a folder.

The current version uses a step-based controller inspired by modern agentic coding tools: the model chooses one safe action at a time, observes the result, and then decides the next action.

## Architecture

```text
CodeTaskSpec
  -> Context Builder
  -> Step Controller
       -> list_tree
       -> read_file
       -> search
       -> replace_text
       -> insert_before / insert_after
       -> apply_patch
       -> write_file
       -> run_command
       -> finish / ask_user
  -> Safety Layer
  -> Reporter
```

The workflow is not a fixed `plan -> patch -> verify` pipeline anymore. The outer contract remains stable and auditable, while the inner loop can explore:

```text
observe repo state
-> choose one action
-> execute through a safe tool
-> record observation in state.json
-> repeat until verified completion or a clear stop condition
```

## Install

```bash
conda activate CodingAgent
pip install -e ".[dev]"
```

## DeepSeek Smoke Tests

Configure the API key in WSL without committing it:

```bash
export DEEPSEEK_API_KEY="your-key"
```

Run the real API smoke tests:

```bash
conda activate CodingAgent
python scripts/deepseek_smoke.py
python scripts/deepseek_repair_smoke.py
```

The smoke tests use the OpenAI-compatible DeepSeek endpoint:

```text
api_base=https://api.deepseek.com
model=deepseek-v4-pro
api_key_env=DEEPSEEK_API_KEY
```

## API Reference

### Input: `CodeTaskSpec`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `workspace_path` | `Path` | **yes** | — | Working directory. Created if it does not exist. |
| `output_dir` | `Path` | **yes** | — | Where run artifacts are written. |
| `task_goal` | `str` | **yes** | — | Natural-language task description. |
| `model` | `str` | no | `"gpt-4.1"` | Model name. |
| `api_base` | `str` | no | `"https://api.openai.com/v1"` | OpenAI-compatible endpoint. |
| `api_key_env` | `str` | no | `"OPENAI_API_KEY"` | Env var holding the API key. |
| `constraints` | `list[str]` | no | `[]` | Hard constraints for the prompt. |
| `verify_commands` | `list[str]` | no | `[]` | Shell commands the agent runs to verify edits. |
| `allowed_paths` | `list[str]` | no | `[]` | File whitelist. Empty = all safe paths. |
| `max_steps` | `int` | no | `24` | Base step budget. |
| `max_extra_steps_after_progress` | `int` | no | `8` | Grace steps after last file change. |
| `patch_repair_attempts` | `int` | no | `2` | LLM repair attempts per failed edit. |
| `timeout_seconds` | `int` | no | `900` | Per-command timeout. |
| `max_context_tokens` | `int\|None` | no | `None` | Hard cap on prompt tokens. |
| `model_context_window_tokens` | `int\|None` | no | `None` | Override context window size. |
| `context_margin_ratio` | `float` | no | `0.20` | Safety margin fraction. |
| `context_output_reserve_tokens` | `int` | no | `16384` | Token reserve for model output. |

### Output: `PatchReport`

| Field | Type | Description |
|-------|------|-------------|
| `status` | `str` | `"completed"`, `"failed"`, `"blocked"`, or `"needs_user_input"`. Agent's finish status is authoritative; verification failures are recorded as risks, not overrides. |
| `changed_files` | `list[str]` | Paths modified during the run. |
| `diff_path` | `Path\|None` | Path to `diff.patch`. |
| `verification_results` | `list[CommandResult]` | Exit code and log paths per verification command. |
| `summary` | `str` | Human-readable summary. |
| `residual_risks` | `list[str]` | Warnings and known issues. |

### Minimal Example

```python
from coding_agent import CodeTaskSpec, run_code_task

report = run_code_task(CodeTaskSpec(
    workspace_path="/path/to/project",   # required
    output_dir="/path/to/artifacts",     # required
    task_goal="Add training loss logging.",
    constraints=["Do not change model architecture."],
    verify_commands=["python train.py --help"],
    allowed_paths=["train.py"],
    model="deepseek-v4-pro",
    api_base="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
))

print(report.status)
```



### Code QA (Read-Only)

Ask questions about a repository without modifying any files:

```python
from coding_agent import CodeQuestionSpec, run_code_question

result = run_code_question(CodeQuestionSpec(
    workspace_path="/path/to/repo",       # required: must exist
    question="Where is the training loop?",   # required
    output_dir="/path/to/artifacts",          # required
    context_hint="Look at train.py",           # optional
    max_steps=8,
    model="deepseek-v4-pro",
    api_base="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
))

print(result.status)         # "completed" or "failed"
print(result.answer)         # markdown answer with file/line evidence
print(result.evidence_files) # paths inspected during the question
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `workspace_path` | `Path` | yes | Existing repo (read-only, must exist). |
| `question` | `str` | yes | Natural-language code question. |
| `output_dir` | `Path` | yes | Where artifacts are written. |
| `context_hint` | `str` | no | Hint about which files to inspect. |
| `constraints` | `list[str]` | no | Additional hard constraints. |
| `max_steps` | `int` | no | Step budget (default 12). |
| `timeout_seconds` | `int` | no | Per-command timeout (default 600). |
| `model` / `api_base` / `api_key_env` | — | no | Same as CodeTaskSpec. |

## Safety Defaults

- Only files inside `workspace_path` may be edited.
- `allowed_paths` can restrict edits to specific files or directories.
- `.git`, virtual environments, data directories, cache folders, and model weights are blocked by default.
- Dangerous commands such as `sudo`, `rm -rf`, recursive ownership changes, shutdown/reboot, and `curl | bash` are blocked.
- The controller uses a finite step budget: `max_steps` for the base budget plus `max_extra_steps_after_progress` for verification/finish grace after code changes.
- Small local edits should use deterministic structured actions: `replace_text`, `insert_before`, or `insert_after`.
- Structured edits require exact one-time matches by default; repeated matches trigger repair with match-context logs.
- Structured edits may use `occurrence_index` only when the target occurrence is explicit and justified by context.
- Context limits are selected automatically from `model`, with margin and output reserve left unused; use `max_context_tokens` or `model_context_window_tokens` to override.
- `read_file` supports optional `start_line`/`end_line`, and recent file reads are carried forward to discourage repeated full-file reads.
- Unified diff remains available through `apply_patch`, and every run can still produce `diff.patch`.
- Patches are validated with `git apply --check` before they are applied.
- Malformed patches are saved as `logs/failed_patch_<step>_<attempt>.patch` with matching stderr artifacts.
- Patch repair can return either a corrected diff or a structured edit action.
- `ControllerAction.reasoning` is optional so minor model schema omissions do not fail an otherwise valid run.
- The agent's explicit `finish` status is authoritative; verification results are recorded as evidence for the caller to inspect but do not override the agent's judgment.
- If files changed after the last verification, the controller auto-runs `verify_commands` before accepting `finish`.

## Run Artifacts

Each run writes:

```text
coding_agent_run/
  state.json
  patch_report.md
  diff.patch
  initial_diff.patch
  logs/
    action_01.json
    action_02.json
    failed_patch_02_01.patch
    failed_patch_02_01.stderr
    repair_context_02_01.json
    repair_response_02_01.json
    step_03/
      verify_01.stdout
      verify_01.stderr
```

## Current Capability Envelope

Good fits:

- metric/logging additions
- small config or script creation
- API compatibility fixes
- bounded debug/profiling helpers
- repo-local bug fixes with explicit verification commands

Not yet a full research-design agent by itself:

- large multi-module feature work needs better decomposition
- experiment design should still be decided by an upper-level research agent
- reviewer logic is still mostly evidence/rule based
- multi-operation structured edit batches and AST-aware edits are future work

## Batch Tests

Real-API integration tests covering diverse ML editing patterns:

```bash
conda activate CodingAgent
export DEEPSEEK_API_KEY="your-key"
python tests/batch_real_tasks.py
```

Runs 6 cases against torchdiffeq: argparse modification, multi-edit,
nested loop edits, file creation, assertion insertion, and false-negative
verification handling. Results saved under `runs/<timestamp>/results/`.
