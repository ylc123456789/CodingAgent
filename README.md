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

## Python API

```python
from coding_agent import CodeTaskSpec, run_code_task

report = run_code_task(CodeTaskSpec(
    repo_path="/path/to/repo",
    task_goal="Add training loss logging without changing training semantics.",
    constraints=[
        "Do not change model architecture.",
        "Do not change optimizer, dataset split, or evaluation metric.",
    ],
    verify_commands=["python train.py --help"],
    allowed_paths=["train.py"],
    max_steps=12,
    patch_repair_attempts=2,
    api_base="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
    model="deepseek-v4-pro",
))

print(report.status)
```

## Safety Defaults

- Only files inside `repo_path` may be edited.
- `allowed_paths` can restrict edits to specific files or directories.
- `.git`, virtual environments, data directories, cache folders, and model weights are blocked by default.
- Dangerous commands such as `sudo`, `rm -rf`, recursive ownership changes, shutdown/reboot, and `curl | bash` are blocked.
- Small local edits should use deterministic structured actions: `replace_text`, `insert_before`, or `insert_after`.
- Structured edits require exact one-time matches by default; repeated matches trigger repair with match-context logs.
- Structured edits may use `occurrence_index` only when the target occurrence is explicit and justified by context.
- Unified diff remains available through `apply_patch`, and every run can still produce `diff.patch`.
- Patches are validated with `git apply --check` before they are applied.
- Malformed patches are saved as `logs/failed_patch_<step>_<attempt>.patch` with matching stderr artifacts.
- Patch repair can return either a corrected diff or a structured edit action.
- `ControllerAction.reasoning` is optional so minor model schema omissions do not fail an otherwise valid run.
- If `verify_commands` are provided, a model-requested `completed` finish is downgraded unless verification evidence exists and passes.
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
