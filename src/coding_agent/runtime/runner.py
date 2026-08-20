"""Run verification commands and capture their logs."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from ..models import CommandResult
from .safety import validate_command, validate_env_command




def _conda_executable() -> str | None:
    """Locate the conda executable from env, PATH, or common locations."""
    candidates: list[str] = []
    for var in ("CONDA_EXE", "MAMBA_EXE", "MICROMAMBA_EXE"):
        value = os.environ.get(var)
        if value and Path(value).is_file():
            candidates.append(value)
    found = shutil.which("conda")
    if found:
        candidates.append(found)
    for candidate in (
        Path.home() / "miniconda3" / "bin" / "conda",
        Path.home() / "anaconda3" / "bin" / "conda",
        Path.home() / "miniforge3" / "bin" / "conda",
        Path("/opt/conda/bin/conda"),
        Path("/usr/local/miniconda3/bin/conda"),
        Path("/root/miniconda3/bin/conda"),
        Path("/root/anaconda3/bin/conda"),
    ):
        if candidate.is_file():
            candidates.append(str(candidate))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def _wrap_conda(command: str, env_name: str) -> str:
    """Wrap a shell command to run inside a conda environment.

    env_name may be a registered environment name (wrapped with -n)
    or an absolute prefix path such as a content-addressed environment
    under a resource root (wrapped with -p).
    """
    conda = _conda_executable()
    if not conda:
        raise RuntimeError(
            f"env_name={env_name!r} specified but no conda executable found"
        )
    is_prefix = os.path.isabs(env_name) or "/" in env_name
    flag = "-p" if is_prefix else "-n"
    return (
        f"{shlex.quote(conda)} run --no-capture-output {flag} "
        f"{shlex.quote(env_name)} bash -c {shlex.quote(command)}"
    )


def run_verify_commands(
    repo_root: Path,
    commands: list[str],
    log_dir: Path,
    timeout_seconds: int,
    env_name: str = "",
    env_policy: str = "auto",
) -> list[CommandResult]:
    """Run verification commands with captured logs.

    Safety validation runs on the original command before any conda
    wrapper is added.  env_name non-empty wraps execution via
    conda run -n <env_name>.  env_policy constrains environment
    mutation attempts (see validate_env_command).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    results: list[CommandResult] = []
    for index, command in enumerate(commands, start=1):
        validate_command(command)
        validate_env_command(command, env_policy)
        run_command = _wrap_conda(command, env_name) if env_name else command
        stdout_path = log_dir / f"verify_{index:02d}.stdout"
        stderr_path = log_dir / f"verify_{index:02d}.stderr"
        start = time.monotonic()
        timed_out = False
        try:
            completed = subprocess.run(
                run_command,
                cwd=repo_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            # TimeoutExpired.stdout/stderr are raw bytes even with text=True
            # (the exception is raised before text decoding); decode them so
            # write_text below never receives bytes.
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        duration = time.monotonic() - start
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        results.append(
            CommandResult(
                command=command,
                returncode=returncode,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                duration_seconds=duration,
                timed_out=timed_out,
            )
        )
    return results
