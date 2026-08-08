"""Provide the top-level entrypoint for running a coding task."""
from __future__ import annotations

from .controller import run_step_controller
from .models import CodeTaskSpec, PatchReport




def run_code_question(spec):
    """Run a read-only code understanding question.

    Internally wraps CodeQuestionSpec into a read_only CodeTaskSpec
    and reuses the step controller.  The result is a CodeExplanation
    (answer + evidence) instead of a PatchReport.
    """
    from .controller.loop import run_step_controller
    from .models import CodeTaskSpec, CodeExplanation

    task_spec = CodeTaskSpec(
        workspace_path=spec.workspace_path,
        task_goal=(
            f"Question: {spec.question}"
            + (f"\n\nContext hint: {spec.context_hint}" if spec.context_hint else "")
        ),
        constraints=spec.constraints + [
            "Do NOT modify any files. Read-only access only.",
        ],
        output_dir=spec.output_dir,
        read_only=True,
        max_steps=spec.max_steps,
        timeout_seconds=spec.timeout_seconds,
        model=spec.model,
        api_base=spec.api_base,
        api_key_env=spec.api_key_env,
        max_context_tokens=spec.max_context_tokens,
        model_context_window_tokens=spec.model_context_window_tokens,
        context_margin_ratio=spec.context_margin_ratio,
        context_output_reserve_tokens=spec.context_output_reserve_tokens,
    )
    report = run_step_controller(task_spec)

    return CodeExplanation(
        status=report.status,
        answer=report.summary,
        evidence_files=report.changed_files,
        commands_run=report.verification_results,
    )


def run_code_task(spec: CodeTaskSpec) -> PatchReport:
    """Run a coding task through the step controller."""
    return run_step_controller(spec)
