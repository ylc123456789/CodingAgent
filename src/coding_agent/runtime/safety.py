"""Enforce path and command safety constraints."""
from __future__ import annotations

import shlex
from pathlib import Path


BLOCKED_PATH_PARTS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "data",
    "datasets",
    "checkpoints",
    "weights",
    "models",
}

BLOCKED_SUFFIXES = {
    ".ckpt",
    ".h5",
    ".npy",
    ".npz",
    ".onnx",
    ".parquet",
    ".pt",
    ".pth",
    ".safetensors",
}


class SafetyError(ValueError):
    """Raised when a path or command violates safety policy."""
    pass


def ensure_repo_relative(path: str) -> str:
    """Resolve and validate a repository-relative path."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        raise SafetyError(f"absolute paths are not allowed in patches: {path}")
    if normalized.startswith("../") or "/../" in normalized or normalized == "..":
        raise SafetyError(f"path traversal is not allowed in patches: {path}")
    return normalized.removeprefix("./")


def ensure_path_allowed(repo_root: Path, relative_path: str, allowed_paths: list[str] | None = None) -> Path:
    """Validate a path against safety and allow-list rules."""
    safe_rel = ensure_repo_relative(relative_path)
    parts = set(Path(safe_rel).parts)
    suffix = Path(safe_rel).suffix.lower()
    if parts & BLOCKED_PATH_PARTS:
        raise SafetyError(f"blocked path segment in patch: {safe_rel}")
    if suffix in BLOCKED_SUFFIXES:
        raise SafetyError(f"blocked file type in patch: {safe_rel}")
    if allowed_paths:
        allowed = [ensure_repo_relative(item).rstrip("/") for item in allowed_paths]
        if not any(safe_rel == item or safe_rel.startswith(f"{item}/") for item in allowed):
            raise SafetyError(f"path is outside allowed_paths: {safe_rel}")
    resolved = (repo_root / safe_rel).resolve()
    if repo_root.resolve() not in [resolved, *resolved.parents]:
        raise SafetyError(f"path escapes repo root: {safe_rel}")
    return resolved




READ_ONLY_COMMAND_PREFIXES = (
    "ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "file", "pwd", "tree",
)


def validate_read_only_command(command: str) -> None:
    """Reject commands not in the read-only whitelist."""
    validate_command(command)
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise SafetyError(f"invalid shell command: {command}") from exc
    if not tokens:
        raise SafetyError("empty command")
    base = tokens[0].split("/")[-1]
    if base not in READ_ONLY_COMMAND_PREFIXES:
        raise SafetyError(
            f"command not allowed in read-only mode: {base}. "
            f"Allowed: {', '.join(READ_ONLY_COMMAND_PREFIXES)}"
        )


HEAVY_FRAMEWORKS = (
    "torch", "tensorflow", "jax", "paddlepaddle", "mxnet",
)

_SHELL_OPERATORS = ("&&", "||", ";", "|")

_INSTALL_SUBCOMMANDS = ("install",)
_UNINSTALL_SUBCOMMANDS = ("uninstall", "remove")
_UPGRADE_SUBCOMMANDS = ("update", "upgrade")
_PKG_SUBCOMMANDS = _INSTALL_SUBCOMMANDS + _UNINSTALL_SUBCOMMANDS + _UPGRADE_SUBCOMMANDS
_ENV_SUBCOMMANDS = ("create", "remove", "update")


def _command_mutation(command: str):
    """Classify a shell command's environment impact.

    Returns ("env", manager, subcommand) for env create/remove,
    ("pkg", manager, subcommand) for package install/uninstall/update,
    ("unparseable", "", "") when tokenization fails, and
    (None, "", "") when the command does not mutate anything.

    Segments are split on shell operators so compound commands like
    `python train.py && pip install x` are checked per segment.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "unparseable", "", ""
    segments = [[]]
    for token in tokens:
        if token in _SHELL_OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)

    for segment in segments:
        if not segment:
            continue
        argv0 = segment[0].split("/")[-1].lower()

        # python -m pip install X  /  python -m uv pip install X
        if argv0 in ("python", "python3") and len(segment) >= 4 and segment[1] == "-m":
            manager = segment[2].lower()
            sub = segment[3].lower()
            if manager in ("pip", "pip3", "uv") and sub in _PKG_SUBCOMMANDS:
                return "pkg", manager, sub
            continue

        # uv pip install / uv venv
        if argv0 == "uv":
            if len(segment) >= 2 and segment[1].lower() == "venv":
                return "env", "uv", "venv"
            if len(segment) >= 3 and segment[1].lower() == "pip" and segment[2].lower() in _PKG_SUBCOMMANDS:
                return "pkg", "uv", segment[2].lower()
            continue

        # pip / pip3 / pip3.x
        if argv0 in ("pip", "pip3") or argv0.startswith("pip3") or argv0.startswith("pip2"):
            if len(segment) >= 2 and segment[1].lower() in _PKG_SUBCOMMANDS:
                return "pkg", argv0, segment[1].lower()
            continue

        # conda / mamba / micromamba
        if argv0 in ("conda", "mamba", "micromamba"):
            if len(segment) < 2:
                continue
            sub = segment[1].lower()
            if sub == "create":
                return "env", argv0, sub
            if sub in _PKG_SUBCOMMANDS:
                # conda remove -n <env> removes an environment; treat
                # remove-with-name as env-level to stay conservative
                if sub in _UNINSTALL_SUBCOMMANDS and "-n" in [s.lower() for s in segment]:
                    return "env", argv0, sub
                return "pkg", argv0, sub
            if sub == "env" and len(segment) >= 3 and segment[2].lower() in _ENV_SUBCOMMANDS:
                return "env", argv0, segment[2].lower()
            continue

    return None, "", ""


def validate_env_command(command: str, env_policy: str) -> None:
    """Enforce env_policy constraints on a shell command.

    auto: no restriction.  reuse_only: may install small missing
    packages but must not touch heavy frameworks, uninstall/upgrade
    anything, or create/remove environments.  frozen: no environment
    or package mutation at all; commands that cannot be parsed are
    conservatively rejected.
    """
    if not env_policy or env_policy == "auto":
        return
    category, manager, subcommand = _command_mutation(command)
    lowered = command.lower()

    if env_policy == "frozen":
        if category is not None:
            raise SafetyError(
                f"environment is frozen; command attempts env/package mutation: {command}"
            )
        return

    # reuse_only
    if category == "env":
        raise SafetyError(
            f"reuse_only forbids creating/removing environments: {command}"
        )
    if category == "pkg":
        if any(fw in lowered for fw in HEAVY_FRAMEWORKS):
            raise SafetyError(
                f"reuse_only forbids heavy framework changes ({', '.join(HEAVY_FRAMEWORKS)}): {command}"
            )
        if subcommand not in _INSTALL_SUBCOMMANDS:
            raise SafetyError(
                f"reuse_only only allows installing small missing packages: {command}"
            )


def validate_command(command: str) -> None:
    """Reject dangerous shell commands."""
    lowered = command.lower()
    blocked_fragments = [
        "rm -rf",
        "sudo ",
        "chmod -r",
        "chown -r",
        "shutdown",
        "reboot",
        "curl",
        "wget",
    ]
    if any(fragment in lowered for fragment in blocked_fragments):
        if ("curl" in lowered or "wget" in lowered) and "| bash" not in lowered and "| sh" not in lowered:
            return
        raise SafetyError(f"blocked verification command: {command}")
    try:
        shlex.split(command)
    except ValueError as exc:
        raise SafetyError(f"invalid shell command: {command}") from exc
