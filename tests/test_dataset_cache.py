"""Dataset-cache bridging tests (scan / link / render)."""
from __future__ import annotations

import os
from pathlib import Path

from coding_agent.runtime.dataset_cache import (
    prepare_dataset_links,
    render_dataset_block,
    scan_dataset_roots,
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


# ── scan ────────────────────────────────────────────────────────────────────

def test_scan_torchvision_positional(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text(
        "from torchvision import datasets\n"
        "ds = datasets.MNIST('../data', train=True, download=True)\n"
    )
    refs = scan_dataset_roots(repo)
    assert len(refs) == 1
    assert refs[0]["declared"] == "../data"
    assert refs[0]["dataset"] == "MNIST"
    assert refs[0]["line"] == 2


def test_scan_torchvision_root_kwarg(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text(
        "datasets.CIFAR10(root='data/cifar', train=True, download=True)\n"
    )
    refs = scan_dataset_roots(repo)
    assert refs[0]["declared"] == "data/cifar"
    assert refs[0]["dataset"] == "CIFAR10"


def test_scan_generic_and_argparse(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text(
        "data_dir = 'data'\n"
        "parser.add_argument('--dataset-dir', default='datasets')\n"
    )
    declared = {r["declared"] for r in scan_dataset_roots(repo)}
    assert declared == {"data", "datasets"}


def test_scan_skips_non_local_values(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text(
        "data_dir = 'https://example.com/data'\n"
        "data_dir2 = '/abs/path'\n"
        "data_dir3 = f'{base}/data'\n"
        "data_dir4 = './'\n"
    )
    assert scan_dataset_roots(repo) == []


def test_scan_skips_vendor_dirs(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".venv").mkdir()
    (repo / ".venv" / "lib.py").write_text("data_dir = 'data'\n")
    assert scan_dataset_roots(repo) == []


def test_scan_dedups_resolved_paths(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.py").write_text("data_dir = 'data'\n")
    (repo / "b.py").write_text("data_dir = './data'\n")
    refs = scan_dataset_roots(repo)
    assert len(refs) == 1


# ── prepare / link ──────────────────────────────────────────────────────────

def test_prepare_no_cache_marks_no_cache(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text("data_dir = 'data'\n")
    refs = prepare_dataset_links(
        repo_path=repo, workspace_dir=tmp_path, cache_root=""
    )
    assert refs[0]["link"] == "no_cache"


def test_prepare_creates_symlink_to_cache_root(tmp_path):
    """Declared root with a non-dataset basename links to the cache root."""
    repo = _repo(tmp_path)
    (repo / "train.py").write_text("data_dir = 'data'\n")
    cache = tmp_path / "cache"
    (cache / "MNIST" / "raw").mkdir(parents=True)
    (cache / "MNIST" / "raw" / "f.bin").write_bytes(b"x")

    refs = prepare_dataset_links(
        repo_path=repo,
        workspace_dir=tmp_path,
        cache_root=str(cache),
        allowed_write_root=repo,
    )
    assert refs[0]["link"] == "created"
    assert refs[0]["cache_hit"] is None  # generic ref, unknown dataset
    link = repo / "data"
    assert link.is_symlink()
    assert link.resolve() == cache.resolve()


def test_prepare_links_to_dataset_named_dir_when_present(tmp_path):
    """A declared path whose basename is a dataset dir links straight to it."""
    repo = _repo(tmp_path)
    (repo / "train.py").write_text("data_dir = 'data/MNIST'\n")
    cache = tmp_path / "cache"
    (cache / "MNIST").mkdir(parents=True)

    refs = prepare_dataset_links(
        repo_path=repo, workspace_dir=tmp_path, cache_root=str(cache),
        allowed_write_root=repo,
    )
    assert refs[0]["link"] == "created"
    link = repo / "data" / "MNIST"
    assert link.is_symlink()
    assert link.resolve() == (cache / "MNIST").resolve()


def test_prepare_never_clobbers_existing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text("data_dir = 'data'\n")
    (repo / "data").mkdir()
    (repo / "data" / "precious.txt").write_text("keep")
    cache = tmp_path / "cache"
    cache.mkdir()

    refs = prepare_dataset_links(
        repo_path=repo, workspace_dir=tmp_path, cache_root=str(cache),
        allowed_write_root=repo,
    )
    assert refs[0]["link"] == "exists"
    assert not (repo / "data").is_symlink()
    assert (repo / "data" / "precious.txt").read_text() == "keep"


def test_prepare_respects_write_root_boundary(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text("data_dir = '../outside/data'\n")
    cache = tmp_path / "cache"
    cache.mkdir()

    refs = prepare_dataset_links(
        repo_path=repo, workspace_dir=tmp_path, cache_root=str(cache),
        allowed_write_root=repo,  # ../outside/data resolves outside repo
    )
    assert refs[0]["link"] == "outside_write_root"
    assert not os.path.lexists(tmp_path / "outside" / "data")


def test_prepare_torchvision_cache_hit_detected(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text(
        "datasets.MNIST('../data', train=True, download=True)\n"
    )
    cache = tmp_path / "cache"
    (cache / "MNIST" / "raw").mkdir(parents=True)
    refs = prepare_dataset_links(
        repo_path=repo, workspace_dir=tmp_path, cache_root=str(cache),
        allowed_write_root=repo,
    )
    assert refs[0]["cache_hit"] is True


# ── render ──────────────────────────────────────────────────────────────────

def test_render_empty_without_cache(tmp_path):
    repo = _repo(tmp_path)

    class Task:
        dataset_cache_dir = ""

    assert render_dataset_block(Task(), repo, []) == ""


def test_render_reports_symlinks(tmp_path):
    repo = _repo(tmp_path)
    (repo / "train.py").write_text("data_dir = 'data'\n")
    cache = tmp_path / "cache"
    cache.mkdir()

    refs = prepare_dataset_links(
        repo_path=repo, workspace_dir=tmp_path, cache_root=str(cache),
        allowed_write_root=repo,
    )

    class Task:
        dataset_cache_dir = str(cache)

    block = render_dataset_block(Task(), repo, refs)
    assert "Dataset cache" in block
    assert "pre-created" in block


def test_render_no_matches_message(tmp_path):
    repo = _repo(tmp_path)

    class Task:
        dataset_cache_dir = "/tmp/cache"

    block = render_dataset_block(Task(), repo, [])
    assert "No hardcoded dataset paths" in block
