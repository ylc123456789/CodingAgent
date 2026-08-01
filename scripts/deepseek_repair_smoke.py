"""Run a DeepSeek-backed smoke test for patch repair behavior."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from coding_agent.apply import PatchApplyError, apply_patch_text, current_diff
from coding_agent.controller import repair_patch
from coding_agent.edits import insert_after_anchor, insert_before_anchor, replace_text_once
from coding_agent.llm import LLMClient
from coding_agent.models import CodeTaskSpec


def main() -> None:
    """Run the script entrypoint."""
    tmp = tempfile.mkdtemp(prefix="coding-agent-repair-")
    repo = Path(tmp) / "toy_repo"
    repo.mkdir()
    (repo / "train.py").write_text("print('accuracy 0.5')\n", encoding="utf-8")
    run(repo, ["git", "init"])
    run(repo, ["git", "config", "user.email", "coding-agent@example.invalid"])
    run(repo, ["git", "config", "user.name", "CodingAgent"])
    run(repo, ["git", "add", "train.py"])
    run(repo, ["git", "commit", "-m", "init"])

    malformed_patch = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,3 @@
 print('accuracy 0.5')
+print('loss 1.0')
"""
    output_dir = repo / "coding_agent_run"
    spec = CodeTaskSpec(
        repo_path=repo,
        task_goal="Add loss logging while preserving the existing accuracy output.",
        constraints=["Only edit train.py.", "Keep the patch minimal."],
        allowed_paths=["train.py"],
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
        output_dir=output_dir,
    )
    client = LLMClient(api_base=spec.api_base, api_key_env=spec.api_key_env, model=spec.model)

    try:
        apply_patch_text(repo, malformed_patch, spec.allowed_paths)
    except PatchApplyError as exc:
        repaired = repair_patch(spec, malformed_patch, exc.stderr or str(exc), output_dir, 1, 1, client)
    else:
        raise RuntimeError("malformed patch unexpectedly applied")

    changed = apply_repaired_edit(spec, repaired)
    verified = subprocess.run(["python", "train.py"], cwd=repo, text=True, capture_output=True, check=False)
    print(f"repo={repo}")
    print(f"changed_files={changed}")
    print(f"repair_action={repaired.action}")
    print("notes=" + "; ".join(repaired.notes))
    print(f"verify_returncode={verified.returncode}")
    print(verified.stdout.strip())
    print("diff:")
    print(current_diff(repo))
    if verified.returncode != 0:
        raise RuntimeError(verified.stderr)


def apply_repaired_edit(spec: CodeTaskSpec, repaired) -> list[str]:
    """Apply a repaired edit action in the smoke test."""
    if repaired.action == "apply_patch":
        return apply_patch_text(spec.repo_path, repaired.patch or "", spec.allowed_paths)
    if repaired.action == "replace_text":
        return [replace_text_once(spec.repo_path, repaired.path or "", repaired.old_text or "", repaired.new_text or "", spec.allowed_paths)]
    if repaired.action == "insert_before":
        return [insert_before_anchor(spec.repo_path, repaired.path or "", repaired.anchor_text or "", repaired.insert_text or "", spec.allowed_paths)]
    if repaired.action == "insert_after":
        return [insert_after_anchor(spec.repo_path, repaired.path or "", repaired.anchor_text or "", repaired.insert_text or "", spec.allowed_paths)]
    raise RuntimeError(f"unsupported repair action: {repaired.action}")


def run(cwd: Path, command: list[str]) -> None:
    """Run a subprocess command and fail on errors."""
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    main()
