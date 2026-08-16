"""Content-addressed environment resources (M2 contract primitives).

Fingerprint algorithms here are byte-identical to the frozen M2-P0
contract (ResAgent/contracts/README.md): canonical JSON with sorted
keys, ensure_ascii, no whitespace, then SHA-256. All decisions are
deterministic code; the LLM never participates.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


class EnvironmentBlockedError(RuntimeError):
    """Structured blocker raised when an environment cannot be used.

    `reason` is a human-readable explanation; `required_actions` lists
    what the caller or user must do to unblock (e.g. rebuild the env,
    fix the dependency declaration, or switch policy).
    """
    def __init__(self, reason: str, required_actions: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.required_actions = required_actions or []


def canonical_dumps(obj) -> str:
    """Canonical JSON: sorted keys, ASCII-safe, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    """SHA-256 hex digest, lowercase."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def identity_subset(spec: dict) -> dict:
    """The identity-bearing subset of ENVIRONMENT_SPEC_V1."""
    return {
        "python": spec["python"],
        "os": spec["os"],
        "arch": spec["arch"],
        "accelerator": {
            "type": spec["accelerator"]["type"],
            "variant": spec["accelerator"].get("variant", ""),
        },
        "dependency_files": [
            {k: f[k] for k in ("path", "sha256", "revision") if k in f}
            for f in sorted(spec["dependency_files"], key=lambda f: f["path"])
        ],
        "channels": sorted(spec.get("channels", [])),
        "pip_index_profile": spec.get("pip_index_profile", ""),
        "framework_constraints": sorted(spec.get("framework_constraints", [])),
    }


def spec_fingerprint(spec: dict) -> str:
    """Deterministic identity fingerprint of a requested environment spec."""
    return sha256_hex(canonical_dumps(identity_subset(spec)))


def resolved_fingerprint(resolved: dict) -> str:
    """Fingerprint of the actual installed inventory (drift detection).

    Hashes the canonical form of the full resolved object
    (python, conda_inventory_sha256, pip_inventory_sha256, frameworks,
    abi_summary).  Note: the M2-P0 golden fixtures do not pin a value
    for this hash; this canonicalization follows contracts/README.md.
    """
    return sha256_hex(canonical_dumps(resolved))


def project_slug(project: str) -> str:
    """Normalize a project name into the env_id slug form."""
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug) or "project"


def env_id(project: str, fingerprint: str) -> str:
    """Content-addressed environment id: resenv_<slug>_<fp[:12]>."""
    return f"resenv_{project_slug(project)}_{fingerprint[:12]}"



# ── ENVIRONMENT_MANIFEST_V1 lifecycle ─────────────────────────────────────

MANIFEST_REQUIRED = {
    "schema", "env_id", "state", "certification", "spec_fingerprint",
    "prefix", "manager", "created_by", "created_at", "updated_at", "pinned",
}

MANIFEST_STATES = {"creating", "ready", "drifted", "failed"}
CERTIFICATION_LEVELS = {"none", "verification", "experiment"}


def validate_manifest(manifest: dict) -> None:
    """Structurally validate an ENVIRONMENT_MANIFEST_V1 document.

    Raises ValueError on missing required fields, unknown state or
    certification values, or wrong env_id shape.  Callers must not
    trust manifests that fail this check.
    """
    missing = MANIFEST_REQUIRED - set(manifest)
    if missing:
        raise ValueError(f"manifest missing required fields: {sorted(missing)}")
    if manifest["schema"] != "ENVIRONMENT_MANIFEST_V1":
        raise ValueError(f"unknown manifest schema: {manifest['schema']}")
    if manifest["state"] not in MANIFEST_STATES:
        raise ValueError(f"unknown manifest state: {manifest['state']}")
    if manifest["certification"] not in CERTIFICATION_LEVELS:
        raise ValueError(f"unknown certification: {manifest['certification']}")
    if not re.fullmatch(r"resenv_[a-z0-9-]+_[0-9a-f]{12}", manifest["env_id"]):
        raise ValueError(f"invalid env_id: {manifest['env_id']}")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["spec_fingerprint"]):
        raise ValueError("invalid spec_fingerprint")
    if manifest["state"] != "creating" and manifest["resolved_fingerprint"] is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", manifest["resolved_fingerprint"]):
            raise ValueError("invalid resolved_fingerprint")


def manifest_path(resource_root: Path, env_id_value: str) -> Path:
    """Return the manifest file path for an environment id."""
    return Path(resource_root) / "environments" / env_id_value / "manifest.json"


def read_manifest(resource_root: Path, env_id_value: str) -> dict | None:
    """Read and validate a manifest; return None when absent."""
    path = manifest_path(resource_root, env_id_value)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(data)
    return data


def write_manifest_atomic(resource_root: Path, manifest: dict) -> Path:
    """Write a manifest via tmp file + rename (atomic publication)."""
    validate_manifest(manifest)
    path = manifest_path(resource_root, manifest["env_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        canonical_dumps(manifest), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def transition_manifest(manifest: dict, new_state: str) -> dict:
    """Apply a legal manifest state transition.

    creating -> ready | failed; ready -> drifted | failed.
    Anything else raises ValueError.  `updated_at` is refreshed.
    """
    import datetime
    legal = {
        "creating": {"ready", "failed"},
        "ready": {"drifted", "failed"},
        "drifted": set(),
        "failed": set(),
    }
    current = manifest["state"]
    if new_state not in legal.get(current, set()):
        raise ValueError(f"illegal manifest transition: {current} -> {new_state}")
    manifest["state"] = new_state
    manifest["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return manifest



# ── ENVIRONMENT_SPEC_V1 collection ────────────────────────────────────────

DEPENDENCY_FILE_PATTERNS = (
    "environment.yml",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
)


def _normalize_os() -> str:
    import sys
    return {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(
        sys.platform, "linux"
    )


def _normalize_arch() -> str:
    import platform
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return "x86_64"


def _python_version_from_environment_yml(workspace: Path) -> str:
    """Extract python major.minor from environment.yml when declared."""
    import re as _re
    yml = workspace / "environment.yml"
    if not yml.exists():
        return ""
    try:
        text = yml.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        match = _re.search(r"\bpython\s*[=~<>]?\s*(\d+\.\d+)", line)
        if match:
            return match.group(1)
    return ""


def _detect_accelerator() -> tuple[str, str]:
    """Detect host accelerator deterministically.

    Returns ("cuda", variant) when torch reports a CUDA build in the
    current interpreter, else ("cpu", "").  Variants require an
    explicit override because no reliable offline detector exists.
    """
    try:
        import torch  # type: ignore
        version = getattr(torch.version, "cuda", None)
        if version:
            return "cuda", "cu" + version.replace(".", "")
    except Exception:
        pass
    return "cpu", ""


def _dependency_files(workspace: Path) -> list[dict]:
    """Collect hashed dependency declarations, sorted by repo-relative path."""
    files = []
    for rel in sorted(workspace.rglob("*")):
        if not rel.is_file():
            continue
        relposix = rel.relative_to(workspace).as_posix()
        name = relposix.split("/")[-1]
        if name == "environment.yml" or name == "requirements.txt" or (
            name.startswith("requirements") and name.endswith(".txt")
        ) or relposix in ("pyproject.toml", "setup.py"):
            files.append({
                "path": relposix,
                "sha256": sha256_hex(rel.read_bytes().decode("utf-8", errors="ignore")),
            })
    return sorted(files, key=lambda f: f["path"])


def collect_environment_spec(
    workspace: Path,
    python_version: str = "",
    accelerator_type: str = "",
    accelerator_variant: str = "",
) -> dict:
    """Build an ENVIRONMENT_SPEC_V1 document deterministically.

    Identity inputs come from the workspace's dependency declarations
    and the host platform; nothing here involves the LLM.
    """
    python = python_version or _python_version_from_environment_yml(workspace)
    if not python:
        import sys
        python = f"{sys.version_info.major}.{sys.version_info.minor}"
    accel_type = accelerator_type
    accel_variant = accelerator_variant
    if not accel_type:
        accel_type, detected_variant = _detect_accelerator()
        if not accel_variant:
            accel_variant = detected_variant
    return {
        "schema": "ENVIRONMENT_SPEC_V1",
        "python": python,
        "os": _normalize_os(),
        "arch": _normalize_arch(),
        "accelerator": {"type": accel_type, "variant": accel_variant},
        "dependency_files": _dependency_files(workspace),
        "channels": [],
        "pip_index_profile": "",
        "framework_constraints": [],
        "notes": "",
    }



# ── resolved inventory and drift detection ─────────────────────────────────

def compute_resolved_inventory(env_prefix: Path) -> dict:
    """Compute the normalized installed inventory of a conda env.

    Deterministic: conda explicit inventory and pip freeze run through
    the environment's own executables; hashed canonically.  Frameworks
    are detected from the pip inventory (torch/tensorflow/jax).
    """
    import shlex
    import subprocess
    prefix = Path(env_prefix)

    def _run(args: list[str]) -> str:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, check=False, timeout=120
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout if result.returncode == 0 else ""

    python_exe = prefix / "bin" / "python"
    conda_exe = prefix / "bin" / "conda"
    if not python_exe.exists():
        raise EnvironmentBlockedError(
            f"environment prefix does not exist: {prefix}",
            ["create the environment before computing its inventory"],
        )

    python_version = _run([str(python_exe), "-c",
                           "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"]).strip()

    conda_out = ""
    if conda_exe.exists():
        conda_out = _run([str(conda_exe), "list", "--explicit"])

    pip_out = _run([str(python_exe), "-m", "pip", "freeze"])

    frameworks: dict = {}
    for line in pip_out.splitlines():
        lowered = line.lower()
        for fw in ("torch", "tensorflow", "jax"):
            if lowered.startswith(fw + "=="):
                frameworks[fw] = {"version": line.split("==")[1].strip()}
        if lowered.startswith("torch==") and "cuda" in lowered:
            # e.g. torch==2.6.0+cu124
            package = line.split("==")[1]
            if "+cu" in package:
                frameworks.setdefault("torch", {})["cuda"] = package.split("+cu")[1].strip()

    return {
        "python": python_version,
        "conda_inventory_sha256": sha256_hex(conda_out) if conda_out else "",
        "pip_inventory_sha256": sha256_hex(pip_out) if pip_out else "",
        "frameworks": frameworks,
        "abi_summary": "",
    }


def drift_state(manifest: dict, computed_resolved_fingerprint: str) -> bool:
    """Return True when the environment has drifted from its manifest.

    Drift is a mismatch between the manifest's recorded
    resolved_fingerprint and the freshly computed one.  A manifest
    without a recorded inventory cannot be verified and is
    conservatively treated as drifted.
    """
    recorded = manifest.get("resolved_fingerprint")
    if not recorded:
        return True
    return recorded != computed_resolved_fingerprint


def check_manifest_freshness(manifest: dict, env_prefix: Path) -> None:
    """Raise EnvironmentBlockedError when a bound env has drifted.

    Used by reuse_only and frozen before executing anything.
    """
    computed = resolved_fingerprint(compute_resolved_inventory(env_prefix))
    if drift_state(manifest, computed):
        raise EnvironmentBlockedError(
            f"environment {manifest['env_id']} has drifted from its manifest "
            f"(recorded {manifest['resolved_fingerprint'][:12]}..., "
            f"computed {computed[:12]}...)",
            [
                "do not reuse this environment",
                "create a new content-addressed environment from the current spec",
                "or rebuild the environment and refresh its manifest",
            ],
        )
