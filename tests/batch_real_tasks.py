"""Batch test CodingAgent across diverse ML research editing tasks.

Usage:
    conda activate CodingAgent
    export DEEPSEEK_API_KEY="your-key"
    python tests/batch_real_tasks.py

This clones torchdiffeq once and runs 5 different editing tasks against it,
each testing a different edit pattern. Results are printed as a summary table.

Requirements: git, DeepSeek API key, CodingAgent installed in dev mode.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import json
from pathlib import Path
from typing import Callable

# Ensure coding_agent is importable
_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here / "src"))

SetupFn = Callable[[Path], dict]

# ============================================================
# Case definitions
# ============================================================

def _case_argparse(repo: Path) -> dict:
    """Add --epochs CLI argument to existing argparse block."""
    return {
        "task_goal": (
            "Modify examples/odenet_mnist.py to add a --epochs command-line argument "
            "that overrides args.nepochs. The default should be 160 (the current default). "
            "Use the existing argparse pattern in the file. Keep all other arguments unchanged."
        ),
        "constraints": [
            "Only edit examples/odenet_mnist.py.",
            "Do not change training defaults or model behavior.",
            "Use the existing argparse.add_argument pattern.",
        ],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 12,
    }

def _case_multi_edit(repo: Path) -> dict:
    """Add import line and annotate time.time() calls across file."""
    return {
        "task_goal": (
            "In examples/odenet_mnist.py, ensure 'import time' is present at the top "
            "of the file (add it if missing). Then add a comment '  # time-tracked' "
            "at the end of each line that calls 'time.time()'. "
            "Use exact text matching for all edits."
        ),
        "constraints": [
            "Only edit examples/odenet_mnist.py.",
            "Do not change any functional code except adding the import and end-of-line comments.",
        ],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 12,
    }

def _case_nested_loop(repo: Path) -> dict:
    """Add per-batch timing in deep training loop with nested structure."""
    return {
        "task_goal": (
            "In examples/odenet_mnist.py, inside the main training loop, "
            "add per-batch timing breakdown: record data_time and forward_time "
            "using time.time() around the data transfer (x, y = ...) and forward pass "
            "(logits = model(x)) sections respectively. Declare data_time_meter and "
            "forward_time_meter as RunningAverageMeter() before the loop, "
            "update them inside the loop, and include them in the epoch logger.info line."
        ),
        "constraints": [
            "Only edit examples/odenet_mnist.py.",
            "Use the existing RunningAverageMeter pattern (declare before loop, update inside, report in logger).",
            "Do not change training semantics or existing metrics.",
        ],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 16,
    }

def _case_file_create(repo: Path) -> dict:
    """Create a new utility file from scratch."""
    return {
        "task_goal": (
            "Create a new file examples/metrics_tracker.py containing a class "
            "MetricsTracker with methods: __init__(self, name), update(self, value), "
            "reset(self), and summary(self) that returns a dict with min/max/avg/count. "
            "Use only the Python standard library. Include a docstring and type hints."
        ),
        "constraints": [
            "Create the file at examples/metrics_tracker.py.",
            "Use only Python standard library.",
            "Do not modify any existing files.",
        ],
        "verify_commands": [
            "python -m py_compile examples/metrics_tracker.py",
            "python -c 'from examples.metrics_tracker import MetricsTracker; t = MetricsTracker(\"loss\"); t.update(0.5); t.update(0.3); s = t.summary(); assert s[\"count\"] == 2'",
        ],
        "allowed_paths": ["examples/metrics_tracker.py"],
        "max_steps": 10,
    }

def _case_assertion(repo: Path) -> dict:
    """Insert a defensive assertion in tensor-handling code."""
    return {
        "task_goal": (
            "In examples/odenet_mnist.py, find the line 'loss = criterion(logits, y)' "
            "and insert immediately before it an assertion: "
            "assert isinstance(logits, torch.Tensor), 'logits must be a tensor'. "
            "Use exact anchor matching for the insertion."
        ),
        "constraints": [
            "Only edit examples/odenet_mnist.py.",
            "Add only the assertion line, do not change any existing logic.",
        ],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 8,
    }

CASES: list[tuple[str, str, SetupFn]] = [
    ("argparse_add",   "Add --epochs CLI argument to argparse block",     _case_argparse),
    ("multi_edit",     "Add import + annotate time.time() calls in file",  _case_multi_edit),
    ("nested_loop",    "Add timing meters inside deep training loop",      _case_nested_loop),
    ("file_create",    "Create new MetricsTracker class from scratch",     _case_file_create),
    ("assertion",      "Insert defensive isinstance assertion",           _case_assertion),
]

# ============================================================
# Runner
# ============================================================

def clone_torchdiffeq() -> Path:
    """Clone torchdiffeq into a temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="ca-batch-"))
    repo = tmp / "torchdiffeq"
    print(f"Cloning torchdiffeq into {repo} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/rtqichen/torchdiffeq.git", str(repo)],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return repo

def reset_repo(repo: Path) -> None:
    """Reset repo to clean state."""
    subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fd"], cwd=repo, capture_output=True, text=True)
    run_dir = repo / "coding_agent_run"
    if run_dir.exists():
        subprocess.run(["rm", "-rf", str(run_dir)])

def check_syntax(repo: Path, files: list[str]) -> list[str]:
    """Check syntax of modified files, returning error messages."""
    errors = []
    for f in files:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(repo / f)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            errors.append(f"{f}: {r.stderr.strip()[-300:]}")
    return errors

def run_one(name: str, description: str, setup: SetupFn, repo: Path) -> dict:
    """Run a single test case."""
    from coding_agent import CodeTaskSpec, run_code_task

    print(f"\n{'─'*60}")
    print(f"  {name}: {description}")
    print(f"{'─'*60}")

    cfg = setup(repo)
    spec = CodeTaskSpec(
        repo_path=repo,
        task_goal=cfg["task_goal"],
        constraints=cfg.get("constraints", []),
        verify_commands=cfg.get("verify_commands", []),
        allowed_paths=cfg.get("allowed_paths", []),
        max_steps=cfg.get("max_steps", 12),
        max_extra_steps_after_progress=3,
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        model="deepseek-v4-pro",
    )

    t0 = time.time()
    try:
        report = run_code_task(spec)
        elapsed = time.time() - t0
    except Exception as exc:
        elapsed = time.time() - t0
        return {
            "name": name, "status": f"EXCEPTION", "steps": 0,
            "time": elapsed, "changed_files": [],
            "syntax_ok": False, "syntax_errors": [str(exc)],
            "summary": str(exc)[:200],
        }

    syntax_errors = check_syntax(repo, report.changed_files)

    n_steps = 0
    n_errs = 0
    state_path = repo / "coding_agent_run" / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        n_steps = len(state.get("steps", []))
        n_errs = sum(1 for s in state.get("steps", []) if s.get("error"))

    return {
        "name": name, "status": report.status, "steps": n_steps,
        "internal_errors": n_errs, "time": elapsed,
        "changed_files": report.changed_files,
        "syntax_ok": len(syntax_errors) == 0,
        "syntax_errors": syntax_errors,
        "summary": report.summary[:200],
    }

def main() -> None:
    print("CodingAgent Batch Real-Task Test")
    print(f"Model: deepseek-v4-pro  |  Cases: {len(CASES)}")
    repo = clone_torchdiffeq()

    results = []
    for name, desc, setup in CASES:
        reset_repo(repo)
        r = run_one(name, desc, setup, repo)
        results.append(r)
        icon = "✓" if r["status"] == "completed" and r["syntax_ok"] else "✗"
        print(f"  {icon} {r['status']:<12} {r['steps']:2d} steps  {r['time']:4.0f}s  syntax={'OK' if r['syntax_ok'] else 'FAIL'}")

    # Summary table
    print(f"\n{'='*70}")
    print(f"{'Case':<20} {'Status':<12} {'Steps':<6} {'Errs':<5} {'Time':<7} {'Syntax':<7}")
    print(f"{'-'*70}")
    for r in results:
        s = "OK" if r["syntax_ok"] else "FAIL"
        print(f"{r['name']:<20} {r['status']:<12} {r['steps']:<6} {r['internal_errors']:<5} {r['time']:<6.0f}s {s:<7}")

    passed = sum(1 for r in results if r["status"] == "completed" and r["syntax_ok"])
    print(f"\nPassed: {passed}/{len(results)}")

    # Show any syntax errors
    for r in results:
        if r["syntax_errors"]:
            print(f"\n  {r['name']} syntax errors:")
            for e in r["syntax_errors"]:
                print(f"    {e[:200]}")

    # Show any unexpected failures
    for r in results:
        if r["status"] != "completed":
            print(f"\n  {r['name']} failed: {r['summary']}")

if __name__ == "__main__":
    main()
