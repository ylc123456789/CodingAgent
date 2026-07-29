# CodingAgent

CodingAgent is a lightweight programming agent for code-related tasks in a single repository. It is designed to be developed as a standalone project and later copied into `reproagent` as a folder.

The first version focuses on a small, inspectable loop:

1. Build minimal repository context.
2. Ask an OpenAI-compatible model for an edit plan.
3. Ask for a unified diff patch.
4. Apply the patch with repo-boundary safety checks.
5. Run verification commands and save logs.
6. Write `patch_report.md`, `state.json`, `diff.patch`, and command logs.

## Install

```bash
conda activate CodingAgent
pip install -e ".[dev]"
```

## Example

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
    model="gpt-4.1",
    api_key_env="OPENAI_API_KEY",
))

print(report.status)
```

## Safety Defaults

- Only files inside `repo_path` may be edited.
- `.git`, virtual environments, data directories, cache folders, and model weights are blocked by default.
- Dangerous commands such as `sudo`, `rm -rf`, recursive ownership changes, shutdown/reboot, and `curl | bash` are blocked.
- High-risk research-semantics changes should return `needs_user_input` instead of being applied silently.
