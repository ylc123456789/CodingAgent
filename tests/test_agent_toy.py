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
            {"action": "read_file", "reasoning": "Inspect the target script before editing.", "path": "train.py"},
            {
                "action": "insert_after",
                "reasoning": "Add minimal loss output after accuracy with an exact anchor.",
                "path": "train.py",
                "anchor_text": "print('accuracy 0.5')\n",
                "insert_text": "print('loss 1.0')\n",
            },
            {"action": "run_command", "reasoning": "Verify the script prints both metrics.", "command": "python train.py"},
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
        CodeTaskSpec(repo_path=repo, task_goal="Add training loss logging.", verify_commands=["python train.py"], max_steps=4)
    )

    assert report.status == "completed"
    assert report.changed_files == ["train.py"]
    assert (repo / "coding_agent_run" / "patch_report.md").exists()
    assert (repo / "coding_agent_run" / "logs" / "action_01.json").exists()
    assert "loss 1.0" in (repo / "coding_agent_run" / "logs" / "step_03" / "verify_01.stdout").read_text(encoding="utf-8")


def test_controller_recovers_malformed_patch_with_structured_repair(tmp_path: Path, monkeypatch) -> None:
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
            {"action": "run_command", "reasoning": "Verify repaired edit.", "command": "python train.py"},
            {"action": "finish", "reasoning": "Verification passed.", "status": "completed", "summary": "Repaired with structured edit."},
        ]

        def __init__(self, *args, **kwargs) -> None:
            self.index = 0

        def complete_json(self, system, user):
            if "repair" in system.lower():
                return {
                    "action": "insert_after",
                    "path": "train.py",
                    "anchor_text": "print('accuracy 0.5')\n",
                    "insert_text": "print('loss 1.0')\n",
                    "notes": ["Converted malformed unified diff into exact insert_after edit."],
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
    assert (logs / "repair_context_01_01.json").exists()
    assert (logs / "repair_response_01_01.json").exists()
    assert "loss 1.0" in (repo / "train.py").read_text(encoding="utf-8")






def test_controller_repairs_ambiguous_structured_anchor(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text(
        "batch_time_meter = RunningAverageMeter()\n"
        "f_nfe_meter = RunningAverageMeter()\n"
        "b_nfe_meter = RunningAverageMeter()\n"
        "end = time.time()\n"
        "for batch in train_loader:\n"
        "    end = time.time()\n",
        encoding="utf-8",
    )
    _init_repo(repo)

    class FakeClient:
        actions = [
            {
                "action": "insert_before",
                "reasoning": "Add loss meter before timing starts, but the anchor is too short.",
                "path": "train.py",
                "anchor_text": "end = time.time()",
                "insert_text": "loss_meter = RunningAverageMeter()\n",
            },
            {"action": "run_command", "reasoning": "Verify syntax.", "command": "python -m py_compile train.py"},
            {"action": "finish", "status": "completed", "summary": "Added loss meter."},
        ]

        def __init__(self, *args, **kwargs) -> None:
            self.index = 0

        def complete_json(self, system, user):
            if "repair failed deterministic structured edits" in system.lower():
                return {
                    "action": "insert_before",
                    "reasoning": "Use a longer unique anchor around the meter setup.",
                    "path": "train.py",
                    "anchor_text": (
                        "batch_time_meter = RunningAverageMeter()\n"
                        "f_nfe_meter = RunningAverageMeter()\n"
                        "b_nfe_meter = RunningAverageMeter()\n"
                        "end = time.time()"
                    ),
                    "insert_text": "loss_meter = RunningAverageMeter()\n",
                }
            action = self.actions[self.index]
            self.index += 1
            return action

    monkeypatch.setattr("coding_agent.controller.LLMClient", FakeClient)

    report = run_code_task(
        CodeTaskSpec(
            repo_path=repo,
            task_goal="Add training loss logging.",
            verify_commands=["python -m py_compile train.py"],
            max_steps=3,
            patch_repair_attempts=2,
        )
    )

    text = (repo / "train.py").read_text(encoding="utf-8")
    assert report.status == "completed"
    assert text.startswith("loss_meter = RunningAverageMeter()\nbatch_time_meter")
    assert (repo / "coding_agent_run" / "logs" / "failed_structured_edit_01_01.json").exists()
    assert (repo / "coding_agent_run" / "logs" / "structured_edit_context_01_01.json").exists()
    assert (repo / "coding_agent_run" / "logs" / "structured_edit_response_01_01.json").exists()

def test_finish_without_reasoning_auto_verifies_after_edit(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _init_repo(repo)

    class FakeClient:
        actions = [
            {
                "action": "insert_after",
                "reasoning": "Add loss logging.",
                "path": "train.py",
                "anchor_text": "print('accuracy 0.5')\n",
                "insert_text": "print('loss 1.0')\n",
            },
            {
                "action": "finish",
                "status": "completed",
                "summary": "Done.",
                "residual_risks": [],
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
        CodeTaskSpec(repo_path=repo, task_goal="Add training loss logging.", verify_commands=["python train.py"], max_steps=2)
    )

    assert report.status == "completed"
    assert len(report.verification_results) == 1
    assert report.verification_results[0].succeeded
    assert (repo / "coding_agent_run" / "logs" / "step_02_finish_verify" / "verify_01.stdout").exists()
    assert "loss 1.0" in report.verification_results[0].stdout_path.read_text(encoding="utf-8")

def test_controller_infers_missing_path_for_structured_edit(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("def main():\nloss_meter.update(loss.item())\n", encoding="utf-8")
    _init_repo(repo)

    class FakeClient:
        actions = [
            {"action": "read_file", "reasoning": "Inspect target file.", "path": "train.py"},
            {
                "action": "replace_text",
                "reasoning": "Fix indentation but omit path by mistake.",
                "old_text": "loss_meter.update(loss.item())",
                "new_text": "    loss_meter.update(loss.item())",
            },
            {"action": "run_command", "reasoning": "Verify syntax.", "command": "python -m py_compile train.py"},
            {"action": "finish", "status": "completed", "summary": "Fixed indentation."},
        ]

        def __init__(self, *args, **kwargs) -> None:
            self.index = 0

        def complete_json(self, system, user):
            action = self.actions[self.index]
            self.index += 1
            return action

    monkeypatch.setattr("coding_agent.controller.LLMClient", FakeClient)

    report = run_code_task(
        CodeTaskSpec(repo_path=repo, task_goal="Fix loss logging indentation.", verify_commands=["python -m py_compile train.py"], max_steps=4)
    )

    logged = (repo / "coding_agent_run" / "logs" / "action_02.json").read_text(encoding="utf-8")
    assert report.status == "completed"
    assert '"path": "train.py"' in logged
    assert "Inferred missing path" in logged


def test_controller_uses_progress_extension_to_verify_after_base_budget(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    _init_repo(repo)

    class FakeClient:
        actions = [
            {
                "action": "insert_after",
                "reasoning": "Add loss logging right before the base step budget expires.",
                "path": "train.py",
                "anchor_text": "print('accuracy 0.5')",
                "insert_text": "print('loss 1.0')",
            },
            {"action": "run_command", "reasoning": "Use grace step to verify the edit.", "command": "python train.py"},
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
            max_steps=1,
            max_extra_steps_after_progress=1,
        )
    )

    assert report.status == "completed"
    assert len(report.verification_results) == 1
    assert report.verification_results[0].succeeded
    assert "loss 1.0" in report.verification_results[0].stdout_path.read_text(encoding="utf-8")


def test_patch_repair_response_accepts_string_notes() -> None:
    from coding_agent.controller import PatchRepairResponse

    response = PatchRepairResponse.model_validate({"action": "insert_after", "notes": "fixed anchor"})
    assert response.notes == ["fixed anchor"]

def _init_repo(repo: Path) -> None:
    _run(repo, "git init")
    _run(repo, "git config user.email coding-agent@example.invalid")
    _run(repo, "git config user.name CodingAgent")
    _run(repo, "git add train.py")
    _run(repo, "git commit -m init")


def _run(cwd: Path, command: str) -> None:
    import subprocess

    subprocess.run(command, cwd=cwd, shell=True, check=True, capture_output=True, text=True)
