from __future__ import annotations

from .apply import apply_patch_text, current_diff
from .context import build_repo_context
from .editor import build_patch
from .llm import LLMClient
from .models import AgentState, CodeTaskSpec, PatchAttempt, PatchReport
from .planner import build_edit_plan
from .report import prepare_output_dir, write_diff, write_initial_diff, write_patch_report, write_state
from .reviewer import review_outcome
from .runner import run_verify_commands


def run_code_task(spec: CodeTaskSpec) -> PatchReport:
    output_dir = prepare_output_dir(spec)
    state = AgentState(task=spec)
    context = build_repo_context(spec)
    write_initial_diff(context.initial_diff, output_dir)
    client = LLMClient(api_base=spec.api_base, api_key_env=spec.api_key_env, model=spec.model)

    final_error = ""
    for iteration in range(1, spec.max_iterations + 1):
        attempt = PatchAttempt(iteration=iteration)
        state.attempts.append(attempt)
        try:
            plan = build_edit_plan(spec, context, client)
            attempt.plan = plan
            (output_dir / "logs" / f"plan_{iteration:02d}.json").write_text(
                plan.model_dump_json(indent=2),
                encoding="utf-8",
            )
            if plan.feasibility in {"blocked", "unsafe"} or plan.needs_user_input:
                report = PatchReport(
                    status="needs_user_input",
                    changed_files=[],
                    diff_path=None,
                    verification_results=[],
                    summary=plan.summary,
                    residual_risks=plan.risks + plan.needs_user_input,
                )
                state.report = report
                write_patch_report(spec, report, output_dir)
                write_state(state, output_dir)
                return report
            patch = build_patch(spec, context, plan, client)
            attempt.patch_text = patch
            (output_dir / "logs" / f"edit_response_{iteration:02d}.txt").write_text(patch, encoding="utf-8")
            changed_files = apply_patch_text(spec.repo_path, patch, spec.allowed_paths)
            attempt.applied = True
            diff_path = write_diff(current_diff(spec.repo_path), output_dir)
            verification = run_verify_commands(
                spec.repo_path,
                spec.verify_commands or plan.verification,
                output_dir / "logs",
                spec.timeout_seconds,
            )
            attempt.verification_results = verification
            report = review_outcome(spec, changed_files, diff_path, verification, plan.risks)
            state.report = report
            write_patch_report(spec, report, output_dir)
            write_state(state, output_dir)
            if report.status == "completed":
                return report
            context = build_repo_context(spec)
        except Exception as exc:  # Keep state artifacts even when an iteration fails.
            final_error = str(exc)
            attempt.error = final_error
            write_state(state, output_dir)
            context = build_repo_context(spec)

    report = PatchReport(
        status="failed",
        changed_files=[],
        diff_path=output_dir / "diff.patch",
        verification_results=[],
        summary=f"Coding agent failed after {spec.max_iterations} iteration(s): {final_error}",
        residual_risks=[final_error] if final_error else [],
    )
    state.report = report
    write_patch_report(spec, report, output_dir)
    write_state(state, output_dir)
    return report
