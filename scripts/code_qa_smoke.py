"""Smoke test for Code QA capability."""
import subprocess, sys, tempfile
from pathlib import Path

# Clone torchdiffeq
tmp = Path(tempfile.mkdtemp(prefix="coding-agent-qa-"))
repo = tmp / "torchdiffeq"
subprocess.run(["git", "clone", "--depth", "1", "https://github.com/rtqichen/torchdiffeq.git", str(repo)],
               check=True, capture_output=True, text=True, timeout=120)

# Record files before
before = {str(f.relative_to(repo)) for f in repo.rglob("*") if f.is_file()}

from coding_agent import CodeQuestionSpec, run_code_question

spec = CodeQuestionSpec(
    workspace_path=repo,
    question="In examples/odenet_mnist.py, where is the training loop? "
             "Report the line numbers of: (1) the main for-loop, "
             "(2) the loss computation, (3) the optimizer step. "
             "Give exact line numbers from the file.",
    output_dir=tmp / "out",
    context_hint="Look at examples/odenet_mnist.py, especially the __main__ block.",
    max_steps=8,
    api_base="https://api.deepseek.com",
    api_key_env="DEEPSEEK_API_KEY",
    model="deepseek-v4-pro",
)

print("Running code QA...")
result = run_code_question(spec)

print(f"\nStatus: {result.status}")
print(f"Answer preview: {result.answer[:500]}")
print(f"Evidence files: {result.evidence_files}")
print(f"Commands run: {len(result.commands_run)}")

# Verify no files were changed
after = {str(f.relative_to(repo)) for f in repo.rglob("*") if f.is_file()}
new_files = after - before
deleted_files = before - after
changed = []
for f in before & after:
    b = (repo / f).read_bytes() if (repo / f).exists() else b""
    # compare with git
    r = subprocess.run(["git", "diff", "--exit-code", "--", f], cwd=repo, capture_output=True)
    if r.returncode != 0:
        changed.append(f)

print(f"\nFile integrity check:")
print(f"  New files: {new_files or 'none'}")
print(f"  Deleted files: {deleted_files or 'none'}")
print(f"  Changed files: {changed or 'none'}")

if not new_files and not deleted_files and not changed:
    print("  PASS: zero file modifications")
else:
    print("  FAIL: files were modified!")
    sys.exit(1)

print(f"\nOutput: {tmp / 'out'}")
