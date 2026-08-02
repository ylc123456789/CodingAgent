"""Batch test CodingAgent across diverse ML research editing tasks.

Usage:
    conda activate CodingAgent
    export DEEPSEEK_API_KEY="your-key"
    python tests/batch_real_tasks.py

Each case's artifacts are saved under /tmp/ca-batch-*/results/<case_name>/
so you can inspect individual runs after the batch completes.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import json
from pathlib import Path
from typing import Callable

_here = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_here / "src"))

SetupFn = Callable[[Path], dict]

# ============================================================
# Case definitions
# ============================================================

def _case_argparse(repo: Path) -> dict:
    return {
        "task_goal": (
            "Modify examples/odenet_mnist.py to add a --epochs command-line argument "
            "that overrides args.nepochs. The default should be 160 (the current default). "
            "Use the existing argparse pattern in the file."
        ),
        "constraints": ["Only edit examples/odenet_mnist.py.", "Keep all other arguments unchanged."],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 12,
    }

def _case_multi_edit(repo: Path) -> dict:
    return {
        "task_goal": (
            "In examples/odenet_mnist.py, ensure 'import time' is present at the top "
            "of the file (add it if missing). Then add a comment '  # time-tracked' "
            "at the end of each line that calls 'time.time()'."
        ),
        "constraints": ["Only edit examples/odenet_mnist.py.", "Use exact text matching."],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 12,
    }

def _case_nested_loop(repo: Path) -> dict:
    return {
        "task_goal": (
            "In examples/odenet_mnist.py, inside the main training loop, "
            "add per-batch timing breakdown: record data_time and forward_time "
            "using time.time() around the data transfer and forward pass. "
            "Declare data_time_meter and forward_time_meter as RunningAverageMeter() "
            "before the loop, update them inside, and include them in the epoch logger.info line."
        ),
        "constraints": ["Only edit examples/odenet_mnist.py.", "Use the existing RunningAverageMeter pattern."],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 16,
    }

def _case_file_create(repo: Path) -> dict:
    return {
        "task_goal": (
            "Create a new file examples/metrics_tracker.py containing a class "
            "MetricsTracker with: __init__(self, name), update(self, value), "
            "reset(self), summary(self)->dict with min/max/avg/count. "
            "Use only Python standard library. Include docstring and type hints."
        ),
        "constraints": ["Create only examples/metrics_tracker.py.", "Use only stdlib.", "Do not touch existing files."],
        "verify_commands": [
            "python -m py_compile examples/metrics_tracker.py",
            "python -c \"from examples.metrics_tracker import MetricsTracker; t = MetricsTracker('loss'); t.update(0.5); t.update(0.3); s = t.summary(); assert s['count'] == 2\"",
        ],
        "allowed_paths": ["examples/metrics_tracker.py"],
        "max_steps": 10,
    }

def _case_assertion(repo: Path) -> dict:
    return {
        "task_goal": (
            "In examples/odenet_mnist.py, find the line 'loss = criterion(logits, y)' "
            "and insert immediately before it: "
            "assert isinstance(logits, torch.Tensor), 'logits must be a tensor'"
        ),
        "constraints": ["Only edit examples/odenet_mnist.py.", "Add only the assertion, change no other lines."],
        "verify_commands": ["python -m py_compile examples/odenet_mnist.py"],
        "allowed_paths": ["examples/odenet_mnist.py"],
        "max_steps": 8,
    }

CASES: list[tuple[str, str, SetupFn]] = [
    ("01_argparse",   "Add --epochs CLI argument to argparse block",    _case_argparse),
    ("02_multi_edit", "Add import + annotate time.time() calls",        _case_multi_edit),
    ("03_nested",     "Add timing meters inside deep training loop",    _case_nested_loop),
    ("04_file_create","Create new MetricsTracker class from scratch",   _case_file_create),
    ("05_assertion",  "Insert defensive isinstance assertion",         _case_assertion),
]

# ============================================================
# Runner
# ============================================================

def clone_torchdiffeq() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="ca-batch-"))
    repo = tmp / "torchdiffeq"
    print(f"Repo: {repo}")
    print(f"Results: {tmp / 'results'}/\n")
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/rtqichen/torchdiffeq.git", str(repo)],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return tmp, repo

def reset_repo(repo: Path) -> None:
    subprocess.run(["git", "checkout", "--", "."], cwd=repo, capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fd"], cwd=repo, capture_output=True, text=True)

def check_syntax(repo: Path, files: list[str]) -> list[str]:
    errors = []
    for f in files:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(repo / f)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            errors.append(f"{f}: {r.stderr.strip()[-300:]}")
    return errors

def run_one(name: str, description: str, setup: SetupFn, repo: Path, results_dir: Path) -> dict:
    from coding_agent import CodeTaskSpec, run_code_task

    print(f"{'─'*60}")
    print(f"  {name}: {description}")
    print(f"{'─'*60}")

    out_dir = results_dir / name
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
        output_dir=out_dir,
    )

    t0 = time.time()
    try:
        report = run_code_task(spec)
        elapsed = time.time() - t0
    except Exception as exc:
        elapsed = time.time() - t0
        return {
            "name": name, "status": "EXCEPTION", "steps": 0,
            "time": elapsed, "changed_files": [],
            "syntax_ok": False, "syntax_errors": [str(exc)],
            "summary": str(exc)[:200],
        }

    syntax_errors = check_syntax(repo, report.changed_files)

    n_steps, n_errs = 0, 0
    state_path = out_dir / "state.json"
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
    print(f"Model: deepseek-v4-pro  |  Cases: {len(CASES)}\n")

    tmp, repo = clone_torchdiffeq()
    results_dir = tmp / "results"
    results_dir.mkdir(exist_ok=True)

    results = []
    for name, desc, setup in CASES:
        reset_repo(repo)
        r = run_one(name, desc, setup, repo, results_dir)
        results.append(r)
        icon = "[OK]" if r["status"] == "completed" and r["syntax_ok"] else "[FAIL]"
        print(f"  {icon} {r['status']:<12} {r['steps']:2d} steps  {r['time']:4.0f}s  syntax={'OK' if r['syntax_ok'] else 'FAIL'}")

    print(f"\n{'='*70}")
    print(f"{'Case':<22} {'Status':<12} {'Steps':<6} {'Errs':<5} {'Time':<7} {'Syntax':<7}")
    print(f"{'-'*70}")
    for r in results:
        s = "OK" if r["syntax_ok"] else "FAIL"
        print(f"{r['name']:<22} {r['status']:<12} {r['steps']:<6} {r['internal_errors']:<5} {r['time']:<6.0f}s {s:<7}")

    passed = sum(1 for r in results if r["status"] == "completed" and r["syntax_ok"])
    print(f"\nPassed: {passed}/{len(results)}")
    print(f"Results: {results_dir}/")
    for d in sorted(results_dir.iterdir()):
        print(f"  {d.name}/")

    for r in results:
        if r["syntax_errors"]:
            print(f"\n  {r['name']} syntax errors:")
            for e in r["syntax_errors"]:
                print(f"    {e[:200]}")
        if r["status"] not in ("completed",):
            print(f"\n  {r['name']} ({r['status']}): {r['summary']}")

if __name__ == "__main__":
    main()
