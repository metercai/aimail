"""SDK language boundary contract (owner ruling 2026-10-11): pysdk and tssdk are independent ——
``pysdk/`` must not contain nodejs code, ``tssdk/`` must not contain python code.

Why hardcoded at code level: "independence" drifts if relied on memory. Each side is consumed by its own language toolchain (pip wheel / npm
tarball), mixing in the other language introduces dependencies on the other toolchain (install node to run pysdk? install python to
publish tssdk?), cross-machine reproduction breaks here first. Repo root ``scripts/`` is language-neutral repo-level tool area, not subject to this limit.

This test only does file tree assertions (no artifact assertions: artifact layer fingerprints are guarded by L2 check-tarball / npm pack respectively).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Allowed exceptions (generated/third-party): only skip node_modules and compiled artifact directories.
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "lib", "dist", "target"}


def _files_under(root: Path, suffixes: tuple[str, ...] | set[str]) -> list[str]:
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if p.suffix in suffixes:
            out.append(str(p.relative_to(REPO)))
    return sorted(out)


def test_no_node_code_in_pysdk():
    node_ext = {".js", ".mjs", ".cjs", ".ts", ".tsx"}
    hits = _files_under(REPO / "pysdk", node_ext)
    hits += [str(p.relative_to(REPO)) for p in (REPO / "pysdk").rglob("package.json")
             if not any(part in SKIP_DIRS for part in p.relative_to(REPO / "pysdk").parts[:-1])]
    assert not hits, (
        "pysdk/ contains nodejs code (should be moved to tssdk/ or repo root scripts/):\n  "
        + "\n  ".join(sorted(hits)))


def test_no_python_code_in_tssdk():
    hits = _files_under(REPO / "tssdk", {".py"})
    assert not hits, (
        "tssdk/ contains python code (should be moved to pysdk/ or repo root scripts/):\n  "
        + "\n  ".join(hits))
