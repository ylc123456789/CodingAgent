from pathlib import Path

from coding_agent.agent import run_code_task
from coding_agent.models import CodeTaskSpec


def test_run_code_task_with_mocked_controller(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _run(repo, "git init")
    _run(repo, "git config user.email coding-agent@example.invalid")
    _run(repo, "git config user.name CodingAgent")
    _run(repo, "git add train.py")
    _run(repo, "git commit -m init")

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


def _run(cwd: Path, command: str) -> None:
    import subprocess

    subprocess.run(command, cwd=cwd, shell=True, check=True, capture_output=True, text=True)
