"""M2-P0 fixture parity tests for environment contract primitives."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.resources import (
    canonical_dumps,
    env_id,
    identity_subset,
    project_slug,
    resolved_fingerprint,
    sha256_hex,
    spec_fingerprint,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "m2_contracts"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_canonical_dumps_stable_ordering():
    a = {"b": 1, "a": {"z": 2, "y": 3}}
    b = {"a": {"y": 3, "z": 2}, "b": 1}
    assert canonical_dumps(a) == canonical_dumps(b)
    assert " " not in canonical_dumps(a)


def test_golden_fingerprints_match():
    """Every P0 golden spec case must reproduce byte-identically."""
    golden = _load("fingerprint_golden.json")
    for case in golden["cases"]:
        spec = _load(case["spec"])
        fp = spec_fingerprint(spec)
        assert fp == case["spec_fingerprint"], (
            f"{case['name']}: {fp} != {case['spec_fingerprint']}"
        )


def test_golden_equivalence_and_distinction():
    golden = _load("fingerprint_golden.json")
    computed = {}
    for case in golden["cases"]:
        computed[case["name"]] = spec_fingerprint(_load(case["spec"]))
    for a, b in golden.get("equal", []):
        assert computed[a] == computed[b], f"{a} should equal {b}"
    for a, b in golden.get("distinct", []):
        assert computed[a] != computed[b], f"{a} should differ from {b}"


def test_altpath_spec_has_same_fingerprint():
    """Absolute paths / notes must not enter identity."""
    a = _load("spec/torch_cuda124.json")
    b = _load("spec/torch_cuda124_altpath.json")
    assert spec_fingerprint(a) == spec_fingerprint(b)


def test_env_id_slug_rules():
    fp = "66630c82f6113079d7d193a02700f9b926417dedc0d35a44cfd35f53e1694d00"
    assert env_id("torchdiffeq", fp) == "resenv_torchdiffeq_66630c82f611"
    assert env_id("github.com/pytorch/examples", fp) == "resenv_github-com-pytorch-examples_66630c82f611"
    assert env_id("  Multi   Space / Repo ", fp) == "resenv_multi-space-repo_66630c82f611"
    assert env_id("___", fp) == "resenv_project_66630c82f611"


def test_resolved_fingerprint_is_deterministic():
    resolved = {
        "python": "3.11.9",
        "conda_inventory_sha256": "aa" * 32,
        "pip_inventory_sha256": "bb" * 32,
        "frameworks": {"torch": {"version": "2.6.0", "cuda": "12.4"}},
        "abi_summary": "glibc2.35",
    }
    assert resolved_fingerprint(resolved) == resolved_fingerprint(dict(resolved))
    assert len(resolved_fingerprint(resolved)) == 64
