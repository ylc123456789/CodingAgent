"""Behavior tests for reviewer.review_outcome (budget-exhaustion report path)."""
from __future__ import annotations

from pathlib import Path

from coding_agent.models import CodeTaskSpec, CommandResult
from coding_agent.reviewer import review_outcome


def _spec(tmp_path: Path) -> CodeTaskSpec:
    return CodeTaskSpec(
        workspace_path=tmp_path / "repo",
        output_dir=tmp_path / "out",
        task_goal="add logging",
    )


def _cmd(tmp_path: Path, returncode: int = 0, timed_out: bool = False) -> CommandResult:
    return CommandResult(
        command="python train.py",
        returncode=returncode,
        stdout_path=tmp_path / "verify.stdout",
        stderr_path=tmp_path / "verify.stderr",
        duration_seconds=0.1,
        timed_out=timed_out,
    )


def test_no_changes_reports_failed(tmp_path):
    report = review_outcome(_spec(tmp_path), [], tmp_path / "diff.patch", [])
    assert report.status == "failed"
    assert report.summary == "No files were changed."


def test_changes_with_passing_verification_reports_completed(tmp_path):
    report = review_outcome(
        _spec(tmp_path), ["train.py"], tmp_path / "diff.patch", [_cmd(tmp_path)],
    )
    assert report.status == "completed"
    assert "train.py" in report.changed_files
    # no failure note when verification passed
    assert not any("returned non-zero" in risk for risk in report.residual_risks)


def test_failed_verification_is_recorded_as_risk(tmp_path):
    report = review_outcome(
        _spec(tmp_path), ["train.py"], tmp_path / "diff.patch", [_cmd(tmp_path, returncode=1)],
    )
    # Current semantics: verification failure is evidence, recorded as a
    # residual risk (status semantics are owned by the overall review).
    assert report.status == "completed"
    assert any("returned non-zero" in risk for risk in report.residual_risks)


def test_missing_verification_is_noted(tmp_path):
    report = review_outcome(
        _spec(tmp_path), ["train.py"], tmp_path / "diff.patch", [],
    )
    assert any("No verification commands" in risk for risk in report.residual_risks)
