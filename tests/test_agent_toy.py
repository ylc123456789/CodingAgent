from pathlib import Path

from coding_agent.agent import run_code_task
from coding_agent.models import CodeTaskSpec


def test_run_code_task_with_mocked_llm(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _run(repo, "git init")
    _run(repo, "git config user.email coding-agent@example.invalid")
    _run(repo, "git config user.name CodingAgent")
    _run(repo, "git add train.py")
    _run(repo, "git commit -m init")

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    def fake_plan(spec, context, client):
        from coding_agent.models import EditPlan

        return EditPlan(
            summary="Add loss logging.",
            target_files=["train.py"],
            allowed_edit_type="logging_only",
            risks=["Only a toy verification was run."],
            verification=["python train.py"],
            needs_user_input=[],
            feasibility="ready_to_edit",
        )

    def fake_patch(spec, context, plan, client):
        return """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,2 @@
 print('accuracy 0.5')
+print('loss 1.0')
"""

    monkeypatch.setattr("coding_agent.agent.LLMClient", FakeClient)
    monkeypatch.setattr("coding_agent.agent.build_edit_plan", fake_plan)
    monkeypatch.setattr("coding_agent.agent.build_patch", fake_patch)

    report = run_code_task(
        CodeTaskSpec(
            repo_path=repo,
            task_goal="Add training loss logging.",
            verify_commands=["python train.py"],
            max_iterations=1,
        )
    )

    assert report.status == "completed"
    assert report.changed_files == ["train.py"]
    assert (repo / "coding_agent_run" / "patch_report.md").exists()
    assert "loss 1.0" in (repo / "coding_agent_run" / "logs" / "verify_01.stdout").read_text(encoding="utf-8")


def _run(cwd: Path, command: str) -> None:
    import subprocess

    subprocess.run(command, cwd=cwd, shell=True, check=True, capture_output=True, text=True)
