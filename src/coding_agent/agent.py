"""Provide the top-level entrypoint for running a coding task."""
from __future__ import annotations

from .controller import run_step_controller
from .models import CodeTaskSpec, PatchReport






def _extract_evidence(output_dir):
    """Extract file evidence from step records.

    Collects paths from read_file actions, search results (parsed as
    path:line: matches), and run_command invocations that reference
    file paths in their command or observation.
    """
    import json, re
    from .models import Snippet

    state_path = output_dir / "state.json"
    if not state_path.exists():
        return [], []

    state = json.loads(state_path.read_text())
    evidence_files = []
    raw_snippets = []
    seen_paths = set()
    seen_snippet_paths = set()

    _file_re = re.compile(r"^(?P<path>[^\s:]+?\.[a-z]{1,6}):\d+:", re.MULTILINE)

    for step in state.get("steps", []):
        action = step.get("action", {})
        obs = step.get("observation", "")

        # 1. read_file — primary evidence
        if action.get("action") == "read_file" and action.get("path"):
            path = action["path"]
            if path not in seen_paths:
                seen_paths.add(path)
                evidence_files.append(path)
            if obs and path not in seen_snippet_paths:
                seen_snippet_paths.add(path)
                start = action.get("start_line")
                end = action.get("end_line")
                if start:
                    raw_snippets.append({
                        "path": path, "content": obs[:2000],
                        "start_line": start,
                        "end_line": end or (start + obs.count("\n")),
                        "why": "",
                    })
                else:
                    raw_snippets.append({
                        "path": path, "content": obs[:2000],
                        "start_line": 1,
                        "end_line": obs.count("\n") + 1,
                        "why": "",
                    })

        # 2. search / grep output — extract path:line: matches
        if action.get("action") in ("search", "run_command") and obs:
            for m in _file_re.finditer(obs):
                path = m.group("path")
                if path not in seen_paths:
                    seen_paths.add(path)
                    evidence_files.append(path)

    return evidence_files, [Snippet(**s) for s in raw_snippets[:10]]

def _detect_uncertainty(text):
    """Scan answer text for uncertainty signals."""
    signals = []
    lowered = text.lower()
    if "uncertain" in lowered or "not sure" in lowered:
        signals.append("Answer contains explicit uncertainty markers.")
    if "may " in lowered or "might " in lowered or "could be" in lowered:
        signals.append("Answer uses hedging language (may/might/could).")
    if "assume" in lowered or "likely" in lowered:
        signals.append("Answer contains assumptions or likelihood statements.")
    return "; ".join(signals) if signals else ""


def run_code_question(spec):
    """Run a read-only code understanding question.

    Internally wraps CodeQuestionSpec into a read_only CodeTaskSpec
    and reuses the step controller.  The result is a CodeExplanation
    (answer + evidence) instead of a PatchReport.
    """
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
        session_id=spec.session_id,
        parent_run=spec.parent_run,
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

    evidence_files, snippets = _extract_evidence(spec.output_dir)
    uncertainty = _detect_uncertainty(report.summary)

    from .session import write_session_card
    write_session_card(task_spec, report, spec.output_dir, kind="qa_session")
    return CodeExplanation(
        status=report.status,
        answer=report.summary,
        evidence_files=evidence_files,
        relevant_snippets=snippets,
        uncertainty=uncertainty,
        commands_run=report.verification_results,
        produced_files=report.produced_files,
    )




def resume_code_task(output_dir, instruction, **overrides):
    """Resume a previous coding task with a new instruction.

    Rebuilds the complete persisted CodeTaskSpec, then applies explicit
    overrides and a continuation goal. New steps are appended to state.json.
    """
    import json
    from pathlib import Path as _Path
    from .session import read_session_card
    from .models import CodeTaskSpec

    output_dir = _Path(output_dir)
    card = read_session_card(output_dir)
    if not card:
        raise ValueError(f"No session.yaml found in {output_dir}")

    state_path = output_dir / "state.json"
    if not state_path.exists():
        raise ValueError(f"No state.json found in {output_dir}")

    state = json.loads(state_path.read_text())
    last_summary = card.get("summary", "")
    last_report = state.get("report", {}).get("summary", "")[:500]

    # Reconstruct task spec
    task_data = state.get("task", {})
    ws = _Path(task_data.get("workspace_path", card.get("project_path", ".")))

    resume_data = {
        **task_data,
        **overrides,
        "workspace_path": ws,
        "output_dir": output_dir,
        "task_goal": (
            f"Continue previous task.\n\n"
            f"Previous summary: {last_summary}\n\n"
            f"Previous report: {last_report}\n\n"
            f"New instruction: {instruction}"
        ),
        "session_id": card["session_id"],
        "parent_run": card.get("parent") or task_data.get("parent_run"),
    }
    task_spec = CodeTaskSpec.model_validate(resume_data)
    return _run_code_task_resume(task_spec, output_dir)




def _run_code_task_resume(spec, output_dir):
    """Run a coding task, appending steps to existing state."""
    from .models import AgentState

    old_state_path = output_dir / "state.json"
    old_state = AgentState.model_validate_json(
        old_state_path.read_text(encoding="utf-8"),
    )
    return _run_prepared_task(spec, resume_state=old_state)




def _prepare_workspace(spec: CodeTaskSpec) -> None:
    """Materialize repo_url into workspace_path before running.

    The destination must be absent or empty.  A non-empty directory is
    never overwritten; it fails with a structured error instead of
    silently reusing an unexpected working tree.

    Clone runs with a timeout and up to 3 attempts (2s/4s backoff).
    Half-finished clone directories from failed attempts are removed
    so a later retry starts clean.
    """
    import shutil
    import subprocess
    import time
    if not spec.repo_url:
        return
    ws = spec.workspace_path
    if ws.exists() and any(ws.iterdir()):
        raise RuntimeError(
            f"workspace_path is not empty; refusing to clone {spec.repo_url} into {ws}"
        )
    command = ["git", "clone", "--depth", "1"]
    if spec.branch:
        command += ["--branch", spec.branch]
    command += [spec.repo_url, str(ws)]

    last_error = ""
    for attempt in range(3):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=300
            )
        except subprocess.TimeoutExpired:
            last_error = "timed out after 300s"
        else:
            if result.returncode == 0:
                return
            last_error = result.stderr.strip()
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        if attempt < 2:
            time.sleep(min(2 ** attempt * 2, 30))
    raise RuntimeError(
        f"git clone failed for {spec.repo_url}: {last_error or 'unknown error'}"
    )




def _prepare_environment(spec: CodeTaskSpec) -> dict | None:
    """Resolve the execution environment before the controller runs.

    Legacy mode (no resource_root): unchanged — env_name is used
    verbatim if set.  Content-addressed mode:

    - auto without env_name: create or reuse a verification-level env
      and bind its prefix so verify commands run inside it;
    - auto with env_name / reuse_only / frozen: validate the bound
      environment against its manifest (spec match, ready state, and
      drift) and block with a structured error otherwise.

    Returns environment info for the session card, or None.
    """
    if not spec.resource_root:
        return None
    from pathlib import Path as _Path
    from .resources import (
        bind_existing_environment,
        collect_environment_spec,
        create_or_reuse_environment,
        project_slug,
    )

    root = _Path(spec.resource_root)
    mirror = getattr(spec, "mirror_profile", "") or ""
    # mirror_profile (ReproAgent naming) feeds pip_index_profile when the
    # contract field is unset; "" and "none" mean no mirror, exactly like
    # reproagent's env_identity mapping.
    pip_profile = getattr(spec, "pip_index_profile", "") or (
        "" if mirror in ("", "none") else mirror
    )
    spec_doc = collect_environment_spec(
        spec.workspace_path,
        requires_gpu=getattr(spec, "requires_gpu", False),
        accelerator_variant=getattr(spec, "accelerator_variant", ""),
        pip_index_profile=pip_profile,
    )
    # Orchestrated runs carry the project identity explicitly (contract
    # §4.3); standalone falls back to the repo/dir basename.
    if getattr(spec, "project_ref", ""):
        project = spec.project_ref
    elif spec.repo_url:
        project = spec.repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    else:
        project = spec.workspace_path.name
    project = project_slug(project)

    creator = {"module": "codingagent"}
    if spec.session_id:
        creator["task_id"] = spec.session_id

    if spec.env_policy == "auto" and not spec.env_name:
        manifest = create_or_reuse_environment(
            root, spec_doc, project, spec.workspace_path, creator,
            repo_origin=spec.repo_url,
        )
        spec.env_name = manifest["prefix"]
        return {
            "env_id": manifest["env_id"],
            "manifest_path": str(root / "environments" / manifest["env_id"] / "manifest.json"),
            "spec_fingerprint": manifest["spec_fingerprint"],
            "resolved_fingerprint": manifest["resolved_fingerprint"] or "",
            "prefix": manifest["prefix"],
            "certification": manifest["certification"],
        }

    if spec.env_name:
        manifest = bind_existing_environment(root, spec.env_name, spec_doc, spec.env_policy)
        # Binding may start from an env id or name; execution uses its prefix.
        spec.env_name = manifest["prefix"]
        return {
            "env_id": manifest["env_id"],
            "manifest_path": str(root / "environments" / manifest["env_id"] / "manifest.json"),
            "spec_fingerprint": manifest["spec_fingerprint"],
            "resolved_fingerprint": manifest["resolved_fingerprint"] or "",
            "prefix": manifest["prefix"],
            "certification": manifest["certification"],
        }
    return None


def run_code_task(spec: CodeTaskSpec) -> PatchReport:
    """Run a coding task through the step controller."""
    _prepare_workspace(spec)
    return _run_prepared_task(spec)


def _run_prepared_task(spec: CodeTaskSpec, resume_state=None) -> PatchReport:
    """Run a fresh or resumed task through one environment lifecycle."""
    environment_info = None
    try:
        environment_info = _prepare_environment(spec)
    except Exception as exc:
        from .models import PatchReport as _PatchReport
        from .resources import EnvironmentBlockedError
        if isinstance(exc, EnvironmentBlockedError):
            report = _PatchReport(
                status="blocked",
                changed_files=[],
                diff_path=None,
                verification_results=[],
                summary=exc.reason,
                residual_risks=exc.required_actions,
            )
            from .session import write_session_card
            write_session_card(spec, report, spec.output_dir, kind="task_session")
            return report
        raise
    report = run_step_controller(spec, resume_state=resume_state)
    if environment_info and spec.env_policy in {"auto", "reuse_only"}:
        from .resources import EnvironmentBlockedError, recertify_environment
        try:
            manifest = recertify_environment(
                spec.resource_root,
                environment_info["env_id"],
                {"module": "codingagent", "task_id": spec.session_id},
            )
            environment_info.update({
                "resolved_fingerprint": manifest["resolved_fingerprint"] or "",
                "certification": manifest["certification"],
            })
        except EnvironmentBlockedError as exc:
            report = report.model_copy(update={
                "status": "blocked",
                "summary": f"{report.summary} Environment recertification failed: {exc.reason}",
                "residual_risks": list(report.residual_risks) + exc.required_actions,
            })
            from .models import AgentState
            from .report import write_patch_report, write_state
            state_path = spec.output_dir / "state.json"
            if state_path.exists():
                state = AgentState.model_validate_json(
                    state_path.read_text(encoding="utf-8"),
                )
                state.report = report
                write_state(state, spec.output_dir)
            write_patch_report(spec, report, spec.output_dir)
    from .session import write_session_card
    write_session_card(spec, report, spec.output_dir, kind="task_session",
                       environment_info=environment_info)
    return report
