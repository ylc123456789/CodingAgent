from pathlib import Path

from coding_agent.agent import run_code_task
from coding_agent.models import CodeTaskSpec


def test_run_code_task_with_mocked_controller(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _init_repo(repo)

    class FakeClient:
        actions = [
            {
                "action": "read_file",
                "reasoning": "Inspect the target script before editing.",
                "path": "train.py",
            },
            {
                "action": "apply_patch",
                "reasoning": "Add minimal loss output after accuracy.",
                "patch": """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,2 @@
 print('accuracy 0.5')
+print('loss 1.0')
""",
            },
            {
                "action": "run_command",
                "reasoning": "Verify the script prints both metrics.",
                "command": "python train.py",
            },
            {
                "action": "finish",
                "reasoning": "The diff is minimal and verification passed.",
                "status": "completed",
                "summary": "Added loss logging and verified the toy script.",
                "residual_risks": ["Only a toy verification was run."],
            },
        ]

        def __init__(self, *args, **kwargs) -> None:
            self.index = 0

        def complete_json(self, system, user):
            action = self.actions[self.index]
            self.index += 1
            return action

    monkeypatch.setattr("coding_agent.controller.LLMClient", FakeClient)

    report = run_code_task(
        CodeTaskSpec(
            repo_path=repo,
            task_goal="Add training loss logging.",
            verify_commands=["python train.py"],
            max_steps=4,
        )
    )

    assert report.status == "completed"
    assert report.changed_files == ["train.py"]
    assert (repo / "coding_agent_run" / "patch_report.md").exists()
    assert (repo / "coding_agent_run" / "logs" / "action_01.json").exists()
    assert "loss 1.0" in (repo / "coding_agent_run" / "logs" / "step_03" / "verify_01.stdout").read_text(encoding="utf-8")


def test_controller_repairs_malformed_patch(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _init_repo(repo)

    class FakeClient:
        actions = [
            {
                "action": "apply_patch",
                "reasoning": "Try to add loss logging, but the hunk header is wrong.",
                "patch": """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,3 @@
 print('accuracy 0.5')
+print('loss 1.0')
""",
            },
            {
                "action": "run_command",
                "reasoning": "Verify repaired patch.",
                "command": "python train.py",
            },
            {
                "action": "finish",
                "reasoning": "Verification passed.",
                "status": "completed",
                "summary": "Repaired and applied loss logging patch.",
            },
        ]

        def __init__(self, *args, **kwargs) -> None:
            self.index = 0

        def complete_json(self, system, user):
            if "repair" in system.lower():
                return {
                    "patch": """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,2 @@
 print('accuracy 0.5')
+print('loss 1.0')
""",
                    "notes": ["Fixed unified diff hunk line count."],
                }
            action = self.actions[self.index]
            self.index += 1
            return action

    monkeypatch.setattr("coding_agent.controller.LLMClient", FakeClient)

    report = run_code_task(
        CodeTaskSpec(
            repo_path=repo,
            task_goal="Add training loss logging.",
            verify_commands=["python train.py"],
            max_steps=3,
            patch_repair_attempts=2,
        )
    )

    logs = repo / "coding_agent_run" / "logs"
    assert report.status == "completed"
    assert report.changed_files == ["train.py"]
    assert (logs / "failed_patch_01_01.patch").exists()
    assert (logs / "failed_patch_01_01.stderr").exists()
    assert "loss 1.0" in (repo / "train.py").read_text(encoding="utf-8")


def _init_repo(repo: Path) -> None:
    _run(repo, "git init")
    _run(repo, "git config user.email coding-agent@example.invalid")
    _run(repo, "git config user.name CodingAgent")
    _run(repo, "git add train.py")
    _run(repo, "git commit -m init")


def _run(cwd: Path, command: str) -> None:
    import subprocess

    subprocess.run(command, cwd=cwd, shell=True, check=True, capture_output=True, text=True)
