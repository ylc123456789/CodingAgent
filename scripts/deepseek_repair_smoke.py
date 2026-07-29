from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from coding_agent.apply import PatchApplyError, apply_patch_text, current_diff
from coding_agent.controller import repair_patch
from coding_agent.llm import LLMClient
from coding_agent.models import CodeTaskSpec


def main() -> None:
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
    spec = CodeTaskSpec(
        repo_path=repo,
        task_goal="Add loss logging while preserving the existing accuracy output.",
        constraints=["Only edit train.py.", "Keep the patch minimal."],
        allowed_paths=["train.py"],
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
    )
    client = LLMClient(api_base=spec.api_base, api_key_env=spec.api_key_env, model=spec.model)

    try:
        apply_patch_text(repo, malformed_patch, spec.allowed_paths)
    except PatchApplyError as exc:
        repaired = repair_patch(spec, malformed_patch, exc.stderr or str(exc), client)
    else:
        raise RuntimeError("malformed patch unexpectedly applied")

    changed = apply_patch_text(repo, repaired.patch, spec.allowed_paths)
    print(f"repo={repo}")
    print(f"changed_files={changed}")
    print("notes=" + "; ".join(repaired.notes))
    print("diff:")
    print(current_diff(repo))


def run(cwd: Path, command: list[str]) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    main()
