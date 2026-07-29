from pathlib import Path

from coding_agent.apply import apply_patch_text, current_diff
from coding_agent.runner import run_verify_commands


def test_apply_patch_text_and_current_diff(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "train.py").write_text("print('accuracy')\n", encoding="utf-8")
    _run(repo, "git init")
    _run(repo, "git config user.email coding-agent@example.invalid")
    _run(repo, "git config user.name CodingAgent")
    _run(repo, "git add train.py")
    _run(repo, "git commit -m init")

    patch = """diff --git a/train.py b/train.py
--- a/train.py
+++ b/train.py
@@ -1 +1,2 @@
 print('accuracy')
+print('loss')
"""
    changed = apply_patch_text(repo, patch)
    assert changed == ["train.py"]
    assert "print('loss')" in (repo / "train.py").read_text(encoding="utf-8")
    assert "+print('loss')" in current_diff(repo)


def test_run_verify_commands_writes_logs(tmp_path: Path) -> None:
    results = run_verify_commands(
        tmp_path,
        ["python -c \"print('loss 1.0')\""],
        tmp_path / "logs",
        timeout_seconds=30,
    )
    assert len(results) == 1
    assert results[0].succeeded
    assert results[0].stdout_path.read_text(encoding="utf-8").strip() == "loss 1.0"


def _run(cwd: Path, command: str) -> None:
    import subprocess

    subprocess.run(command, cwd=cwd, shell=True, check=True, capture_output=True, text=True)
