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
