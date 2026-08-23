"""Content-addressed environment resources (M2 contract primitives).

Fingerprint algorithms here are byte-identical to the frozen M2-P0
contract (ResAgent/contracts/README.md): canonical JSON with sorted
keys, ensure_ascii, no whitespace, then SHA-256. All decisions are
deterministic code; the LLM never participates.
"""
from __future__ import annotations

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


# The contract algorithms live in ONE place: the vendored contract file
# (byte-identical across the three repos; a test asserts the sha256).
# Only CodingAgent-specific helpers stay local.
from ._vendor import env_contract_v1 as _contract

canonical_dumps = _contract.canonical_dumps
sha256_hex = _contract.sha256_hex
identity_subset = _contract.identity_subset
spec_fingerprint = _contract.spec_fingerprint
resolved_fingerprint = _contract.resolved_fingerprint
project_slug = _contract.project_slug
env_id = _contract.env_id



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


def _host_has_gpu() -> bool:
    """Feasibility probe, per the vendored contract (never identity)."""
    return _contract.probe_gpu_usable()


def collect_environment_spec(
    workspace: Path,
    python_version: str = "",
    requires_gpu: bool = False,
    accelerator_variant: str = "",
    pip_index_profile: str = "",
) -> dict:
    """Build an ENVIRONMENT_SPEC_V1 document deterministically.

    Identity inputs come from the workspace's dependency declarations,
    caller-supplied task facts (requires_gpu, explicit accelerator
    variant, mirror profile), and host feasibility.  The variant is
    NEVER inferred from driver capabilities or from frameworks
    installed in the caller process.
    """
    # python selection, dependency enumeration, and constraint extraction
    # all follow the vendored contract (never the caller's interpreter).
    python = _contract.select_python_version(python_version, workspace)
    if not accelerator_variant:
        accelerator_variant = _contract.constraint_cuda_variant(workspace)
    accel_type = "cuda" if (requires_gpu and _host_has_gpu()) else "cpu"
    return {
        "schema": "ENVIRONMENT_SPEC_V1",
        "python": python,
        "os": _normalize_os(),
        "arch": _normalize_arch(),
        "accelerator": {"type": accel_type, "variant": accelerator_variant},
        "dependency_files": _contract.collect_dependency_files(workspace),
        "channels": [],
        "pip_index_profile": pip_index_profile,
        "framework_constraints": [],
        "notes": "",
    }


def compute_resolved_inventory(env_prefix: Path) -> dict:
    """Compute the normalized installed inventory of a conda env.

    Probe EXECUTION is local; command shapes and all normalization/hashing
    come from the vendored contract file (the ONE cross-repo implementation),
    so a given physical env yields the identical fingerprint everywhere.
    """
    import shutil
    import subprocess
    from ._vendor import env_contract_v1 as _contract

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
    if not python_exe.exists():
        raise EnvironmentBlockedError(
            f"environment prefix does not exist: {prefix}",
            ["create the environment before computing its inventory"],
        )

    python_text = _run([str(python_exe), "--version"]).strip()
    pip_text = _run([str(python_exe), "-m", "pip", "list", "--format=json"])
    conda_exe = shutil.which("conda") or ""
    conda_text = (
        _run([conda_exe, "list", "-p", str(prefix), "--json"]) if conda_exe else ""
    )
    try:
        import platform
        libc_name, libc_version = platform.libc_ver()
        abi = f"{libc_name}{libc_version}" if libc_name else ""
    except Exception:
        abi = ""

    return _contract.build_resolved(
        python_version=python_text,
        pip_list_json=pip_text,
        conda_list_json=conda_text,
        abi_summary=abi,
    )


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



# ── creation locks (standard-library atomic create) ────────────────────────

def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _acquire_creation_lock(resource_root: Path, fingerprint: str, timeout: float = 300.0):
    """Acquire the per-fingerprint creation lock.

    Returns a lock handle with .release().  A lock held by a dead
    process is reclaimed; a live holder is waited on with a bounded
    poll (host, pid, started_at are recorded in the lock file).
    """
    import os
    import socket
    import time

    class LockHandle:
        def __init__(self, path):
            self.path = path
            self.released = False

        def release(self):
            if not self.released:
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
                self.released = True

    lock_dir = Path(resource_root) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{fingerprint}.lock"
    deadline = time.monotonic() + timeout

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = {
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "started_at": _now_iso(),
            }
            os.write(fd, canonical_dumps(payload).encode("utf-8"))
            os.close(fd)
            return LockHandle(lock_path)
        except FileExistsError:
            holder = {}
            try:
                holder = json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            holder_pid = holder.get("pid", 0)
            if holder.get("host") == socket.gethostname() and holder_pid and not _pid_alive(holder_pid):
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise EnvironmentBlockedError(
                    f"creation lock for fingerprint {fingerprint[:12]} is held by "
                    f"{holder.get('host', 'unknown')} pid={holder_pid}",
                    ["wait for the other task to finish, or remove the stale lock"],
                )
            time.sleep(1.0)


# ── environment creation and reuse ─────────────────────────────────────────

def env_prefix(resource_root: Path, env_id_value: str) -> Path:
    """Physical conda prefix for a managed environment id."""
    return Path(resource_root) / "conda-envs" / env_id_value


def _find_conda() -> str | None:
    from .runtime.runner import _conda_executable
    return _conda_executable()


def _run_check(args: list[str], timeout: int = 300) -> tuple[int, str]:
    """Run a command; return (returncode, combined output)."""
    import subprocess
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def create_environment_at(
    prefix: Path, spec: dict, project_workspace: Path = None
) -> None:
    """Physically create a conda environment for the given spec.

    environment.yml is installed via conda env create; requirements
    files via pip install -r; pyproject.toml/setup.py via pip install
    of the workspace itself.  Deterministic baseline; the LLM does not
    participate.
    """
    conda = _find_conda()
    if not conda:
        raise EnvironmentBlockedError(
            "no conda executable found to create the environment",
            ["install conda or set CONDA_EXE"],
        )

    def dep_files_of(suffix: str) -> list[dict]:
        return [f for f in spec["dependency_files"] if f["path"].endswith(suffix) or f["path"] == suffix]

    yml = dep_files_of("environment.yml")
    requirements = [f for f in spec["dependency_files"] if f["path"].startswith("requirements") and f["path"].endswith(".txt")]
    project_files = [f for f in spec["dependency_files"] if f["path"] in ("pyproject.toml", "setup.py")]

    prefix.mkdir(parents=True, exist_ok=True)
    if yml:
        yml_path = project_workspace / yml[0]["path"] if project_workspace else None
        if yml_path and yml_path.exists():
            rc, output = _run_check(
                [conda, "env", "create", "-p", str(prefix), "-f", str(yml_path)]
            )
            if rc != 0:
                raise EnvironmentBlockedError(
                    f"conda env create failed: {output.strip()[-500:]}",
                    ["fix the environment.yml declaration"],
                )
        else:
            rc, output = _run_check(
                [conda, "create", "-p", str(prefix), f"python={spec['python']}", "-y"]
            )
            if rc != 0:
                raise EnvironmentBlockedError(
                    f"conda create failed: {output.strip()[-500:]}",
                    ["fix the python version constraint"],
                )
    else:
        rc, output = _run_check(
            [conda, "create", "-p", str(prefix), f"python={spec['python']}", "-y"]
        )
        if rc != 0:
            raise EnvironmentBlockedError(
                f"conda create failed: {output.strip()[-500:]}",
                ["fix the python version constraint"],
            )

    pip = prefix / "bin" / "pip"
    if not pip.exists():
        raise EnvironmentBlockedError(
            f"created prefix has no pip: {prefix}",
            ["check the conda create invocation"],
        )
    for req in requirements:
        req_path = project_workspace / req["path"] if project_workspace else None
        if req_path and req_path.exists():
            rc, output = _run_check([str(pip), "install", "-r", str(req_path)])
            if rc != 0:
                raise EnvironmentBlockedError(
                    f"pip install -r failed: {output.strip()[-500:]}",
                    [f"fix the dependency declaration {req['path']}"],
                )
    if project_files and project_workspace:
        rc, output = _run_check([str(pip), "install", str(project_workspace)])
        if rc != 0:
            raise EnvironmentBlockedError(
                f"project install failed: {output.strip()[-500:]}",
                ["fix pyproject.toml/setup.py packaging"],
            )


def run_verification_audit(prefix: Path, spec: dict, creator: dict) -> dict:
    """Run a verification-level audit and return the audit document.

    Checks: policy compliance (always pass for auto-created envs) and
    framework imports declared in framework_constraints.  The audit
    level is always `verification` — CodingAgent never grants
    `experiment` certification.
    """
    python_exe = prefix / "bin" / "python"
    if not python_exe.exists():
        raise EnvironmentBlockedError(
            f"environment prefix missing python: {prefix}",
            ["the environment was not created correctly"],
        )
    checks = [{"name": "policy", "outcome": "pass", "detail": "created under auto policy"}]
    outcome = "pass"
    for constraint in spec.get("framework_constraints", []):
        fw = constraint.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
        if not fw:
            continue
        rc, output = _run_check(
            [str(python_exe), "-c", f"import {fw}; print('ok')"]
        )
        check = {
            "name": "framework_import",
            "outcome": "pass" if rc == 0 else "fail",
            "detail": output.strip()[-200:],
            "evidence_path": "",
        }
        if rc != 0:
            outcome = "fail"
        checks.append(check)
    return {
        "schema": "ENVIRONMENT_AUDIT_V1",
        "audit_id": f"audit_verification_{_now_iso().replace(':', '').replace('-', '').replace('.', '')}",
        "env_id": "",
        "level": "verification",
        "outcome": outcome,
        "resolved_fingerprint": "",
        "audited_by": creator,
        "at": _now_iso(),
        "checks": checks,
        "notes": "",
    }


def _git_head(workspace: Path | None) -> str:
    """Return git HEAD of a workspace, or empty string."""
    if not workspace:
        return ""
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def create_or_reuse_environment(
    resource_root: Path,
    spec: dict,
    project: str,
    project_workspace: Path | None = None,
    creator: dict | None = None,
    repo_origin: str = "",
) -> dict:
    """Deterministic create-or-reuse of a verification-level environment.

    Returns the manifest.  A ready manifest with matching identity and
    fresh inventory is reused with zero creation; anything else fails
    with EnvironmentBlockedError (CodingAgent never repairs in place).
    """
    import time

    root = Path(resource_root)
    fp = spec_fingerprint(spec)
    id_value = env_id(project, fp)
    creator_info = creator or {"module": "codingagent"}

    manifest = read_manifest(root, id_value)
    if manifest and manifest["state"] == "ready":
        if manifest["spec_fingerprint"] != fp:
            raise EnvironmentBlockedError(
                f"manifest fingerprint mismatch for {id_value}",
                ["this indicates a hash collision; rename the project"],
            )
        prefix = Path(manifest["prefix"])
        if not prefix.exists():
            raise EnvironmentBlockedError(
                f"manifest prefix missing: {prefix}",
                ["rebuild the environment"],
            )
        check_manifest_freshness(manifest, prefix)
        return manifest

    if manifest and manifest["state"] in ("failed", "drifted"):
        raise EnvironmentBlockedError(
            f"environment {id_value} is {manifest['state']} and will not be "
            f"repaired in place",
            ["rebuild via a fresh content-addressed creation after removing the old manifest"],
        )

    lock = _acquire_creation_lock(root, fp)
    try:
        manifest = read_manifest(root, id_value)
        if manifest and manifest["state"] == "ready":
            prefix = Path(manifest["prefix"])
            check_manifest_freshness(manifest, prefix)
            return manifest

        prefix = env_prefix(root, id_value)
        manifest = {
            "schema": "ENVIRONMENT_MANIFEST_V1",
            "env_id": id_value,
            "state": "creating",
            "certification": "none",
            "spec_fingerprint": fp,
            "resolved_fingerprint": None,
            "prefix": str(prefix),
            "manager": "codingagent",
            "created_by": creator_info,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "pinned": False,
            "provenance": {
                "repo_path": str(project_workspace) if project_workspace else "",
                "repo_origin": repo_origin or "local",
                "repo_commit": _git_head(project_workspace),
            },
            "spec": spec,
            "resolved": None,
            "audits": [],
            "usage": [],
        }
        write_manifest_atomic(root, manifest)
        try:
            create_environment_at(prefix, spec, project_workspace)
            resolved = compute_resolved_inventory(prefix)
            manifest["resolved"] = resolved
            manifest["resolved_fingerprint"] = resolved_fingerprint(resolved)
            audit = run_verification_audit(prefix, spec, creator_info)
            audit["env_id"] = id_value
            audit["resolved_fingerprint"] = manifest["resolved_fingerprint"]
            if audit["outcome"] == "fail":
                transition_manifest(manifest, "failed")
                manifest["certification"] = "none"
                write_manifest_atomic(root, manifest)
                raise EnvironmentBlockedError(
                    "verification audit failed after environment creation",
                    ["fix the dependency declarations and rebuild"],
                )
            transition_manifest(manifest, "ready")
            manifest["certification"] = "verification"
            manifest["audits"].append({
                "artifact": f"audits/{audit['audit_id']}.json",
                "level": "verification",
                "outcome": "pass",
                "at": audit["at"],
            })
            write_manifest_atomic(root, manifest)
            audit_dir = Path(root) / "environments" / id_value / "audits"
            audit_dir.mkdir(parents=True, exist_ok=True)
            (audit_dir / f"{audit['audit_id']}.json").write_text(
                canonical_dumps(audit), encoding="utf-8"
            )
            return manifest
        except Exception:
            manifest = read_manifest(root, id_value) or manifest
            if manifest["state"] == "creating":
                transition_manifest(manifest, "failed")
                try:
                    write_manifest_atomic(root, manifest)
                except ValueError:
                    pass
            raise
    finally:
        lock.release()


def recertify_environment(
    resource_root: Path, env_id_value: str, creator: dict | None = None,
) -> dict:
    """Re-audit a managed environment after an allowed package mutation.

    A task running under ``auto`` or ``reuse_only`` may install packages
    after the environment was initially certified.  Keep the manifest in
    sync when the resulting environment passes verification; otherwise
    quarantine it as drifted so it cannot be reused silently.
    """
    root = Path(resource_root)
    manifest = read_manifest(root, env_id_value)
    if manifest is None:
        raise EnvironmentBlockedError(
            f"environment {env_id_value!r} is not registered in resource root {root}",
            ["register the environment via its manifest"],
        )
    if manifest["state"] != "ready":
        raise EnvironmentBlockedError(
            f"environment {env_id_value} is {manifest['state']}, not ready",
            ["rebuild the environment before reuse"],
        )

    lock = _acquire_creation_lock(root, manifest["spec_fingerprint"])
    try:
        manifest = read_manifest(root, env_id_value) or manifest
        prefix = Path(manifest["prefix"])
        resolved = compute_resolved_inventory(prefix)
        computed = resolved_fingerprint(resolved)
        if computed == manifest.get("resolved_fingerprint"):
            return manifest

        creator_info = creator or {"module": "codingagent"}
        spec = manifest.get("spec")
        if not isinstance(spec, dict):
            raise EnvironmentBlockedError(
                f"environment {env_id_value} manifest has no resolved spec",
                ["rebuild the environment from a complete manifest"],
            )
        audit = run_verification_audit(prefix, spec, creator_info)
        audit["env_id"] = env_id_value
        audit["resolved_fingerprint"] = computed
        manifest["resolved"] = resolved
        manifest["resolved_fingerprint"] = computed
        manifest.setdefault("audits", []).append({
            "artifact": f"audits/{audit['audit_id']}.json",
            "level": "verification",
            "outcome": audit["outcome"],
            "at": audit["at"],
        })
        if audit["outcome"] != "pass":
            transition_manifest(manifest, "drifted")
            manifest["certification"] = "none"
        else:
            manifest["certification"] = "verification"
            manifest["updated_at"] = _now_iso()
        write_manifest_atomic(root, manifest)

        audit_dir = root / "environments" / env_id_value / "audits"
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / f"{audit['audit_id']}.json").write_text(
            canonical_dumps(audit), encoding="utf-8",
        )
        if audit["outcome"] != "pass":
            raise EnvironmentBlockedError(
                f"environment {env_id_value} changed and failed recertification",
                ["fix the environment or rebuild it before retrying"],
            )
        return manifest
    finally:
        lock.release()


def bind_existing_environment(
    resource_root: Path,
    env_name: str,
    spec: dict,
    policy: str,
) -> dict:
    """Validate an existing env against the V1 contract before use.

    Used by reuse_only and frozen.  Unregistered envs, wrong spec,
    non-ready state, or drift all raise EnvironmentBlockedError.
    """
    root = Path(resource_root)
    env_ref = Path(env_name)
    env_id_value = env_ref.name if env_ref.is_absolute() else env_name
    manifest = read_manifest(root, env_id_value)
    if manifest is None:
        # tolerate an env that is a plain conda name in legacy mode:
        raise EnvironmentBlockedError(
            f"environment {env_name!r} is not registered in resource root {root}",
            [
                "register the environment via its manifest",
                "or pass resource_root='' to use legacy env binding",
            ],
        )
    if env_ref.is_absolute() and env_ref.resolve() != Path(manifest["prefix"]).resolve():
        raise EnvironmentBlockedError(
            f"environment prefix {env_name!r} does not match registered "
            f"environment {env_id_value!r}",
            ["use the prefix recorded in the environment manifest"],
        )
    if manifest["spec_fingerprint"] != spec_fingerprint(spec):
        raise EnvironmentBlockedError(
            f"environment {env_name} spec fingerprint does not match the "
            f"current workspace",
            ["create a content-addressed env from the current spec",
             "or revert the dependency declarations"],
        )
    if manifest["state"] != "ready":
        raise EnvironmentBlockedError(
            f"environment {env_name} is {manifest['state']}, not ready",
            ["wait for creation to finish or rebuild"],
        )
    prefix = Path(manifest["prefix"])
    if not prefix.exists():
        raise EnvironmentBlockedError(
            f"environment prefix missing: {prefix}",
            ["rebuild the environment"],
        )
    check_manifest_freshness(manifest, prefix)
    return manifest



# ── inspect / prune (maintenance entry points) ──────────────────────────────

def inspect_environments(resource_root: Path) -> list[dict]:
    """List CodingAgent-managed environments with their state summary."""
    root = Path(resource_root)
    environments_dir = root / "environments"
    if not environments_dir.exists():
        return []
    entries = []
    for manifest_file in sorted(environments_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            validate_manifest(manifest)
        except (OSError, ValueError):
            entries.append({
                "env_id": manifest_file.parent.name,
                "state": "unreadable",
                "error": "manifest missing or invalid",
            })
            continue
        if manifest.get("manager") != "codingagent":
            continue
        entries.append({
            "env_id": manifest["env_id"],
            "state": manifest["state"],
            "certification": manifest["certification"],
            "prefix": manifest["prefix"],
            "pinned": manifest.get("pinned", False),
            "last_used_at": manifest.get("last_used_at"),
            "manager": manifest["manager"],
        })
    return entries


def _active_leases(resource_root: Path) -> dict[str, dict]:
    """Index active (unreleased, live-holder) leases by env_id.

    Contract layout: leases live under
    <root>/environments/<env_id>/usage/lease_*.json — never a
    separate top-level leases directory.
    """
    import socket
    root = Path(resource_root)
    environments_dir = root / "environments"
    if not environments_dir.exists():
        return {}
    active: dict[str, dict] = {}
    for lease_file in sorted(environments_dir.glob("*/usage/lease_*.json")):
        try:
            lease = json.loads(lease_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if lease.get("schema") != "RESOURCE_LEASE_V1":
            continue
        if lease.get("released_at"):
            continue
        holder_pid = lease.get("pid", 0)
        if lease.get("host") == socket.gethostname() and holder_pid and not _pid_alive(holder_pid):
            continue
        env = lease.get("env_id", "")
        if env:
            active[env] = lease
    return active


def prune_environments(
    resource_root: Path,
    *,
    min_unused_days: float = 30.0,
    dry_run: bool = True,
) -> list[dict]:
    """Plan removal of CodingAgent-managed environments.

    Dry-run by default and never an apply path (M2-P4 owns apply).
    Protection: pinned manifests and envs under an active lease are
    never candidates.  Returns a list of candidate records; nothing
    on disk is deleted.
    """
    import datetime
    root = Path(resource_root)
    now = datetime.datetime.now(datetime.timezone.utc)
    leases = _active_leases(root)
    candidates = []
    for entry in inspect_environments(root):
        if entry.get("state") == "unreadable":
            continue
        if entry["pinned"]:
            continue
        if entry["env_id"] in leases:
            continue
        last_used = entry.get("last_used_at")
        unused_days: float | None = None
        if last_used:
            try:
                parsed = datetime.datetime.fromisoformat(str(last_used).replace("Z", "+00:00"))
                unused_days = (now - parsed).total_seconds() / 86400.0
            except ValueError:
                pass
        if unused_days is None or unused_days >= min_unused_days:
            candidates.append({
                "env_id": entry["env_id"],
                "prefix": entry["prefix"],
                "state": entry["state"],
                "unused_days": round(unused_days, 1) if unused_days is not None else None,
                "dry_run": dry_run,
            })
    return candidates


def delete_environment(resource_root: Path, env_id: str) -> dict:
    """Physically delete this module's env (prefix + manifest dir).

    M2-P4 apply path. The caller (ResAgent cleanup) owns policy; this
    function owns only identity and containment guards. Never raises.
    """
    import shutil

    root = Path(resource_root)
    manifest_file = root / "environments" / env_id / "manifest.json"
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"env_id": env_id, "deleted": False, "reason": "manifest_missing"}
    if manifest.get("manager") != "codingagent":
        return {"env_id": env_id, "deleted": False,
                "reason": f"not_managed_by_codingagent:{manifest.get('manager', '')}"}

    prefix_text = str(manifest.get("prefix", "") or "")
    envs_root = (root / "conda-envs").resolve()
    if prefix_text:
        resolved = Path(prefix_text).resolve()
        if resolved != envs_root and envs_root not in resolved.parents:
            # Containment guard: never delete outside the managed envs dir.
            return {"env_id": env_id, "deleted": False,
                    "reason": "prefix_outside_resource_root"}

    try:
        if prefix_text and Path(prefix_text).is_dir():
            shutil.rmtree(prefix_text)
        env_dir = root / "environments" / env_id
        if env_dir.is_dir():
            shutil.rmtree(env_dir)
    except OSError as exc:
        return {"env_id": env_id, "deleted": False, "reason": f"os_error:{exc}"}
    return {"env_id": env_id, "deleted": True, "reason": ""}
