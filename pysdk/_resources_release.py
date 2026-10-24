#!/usr/bin/env python3
"""_resources_release — Local configuration directory expansion for SDK resources (python version).

Architecture: resources (board/role_prompt role prompts + skills) are public seeds, shipped with SDK
 distributed; released to ~/.aimail/systems/{sid}/board/ at install/startup for runtime reads
(pysdk and tssdk runtime same path). Only fill missing/update files, never overwrite content user has already
personalized in configuration directory.

Same semantics as tssdk mail-core release-resources.ts, cli old release-board-resources.sh
 same semantics; this module is the python implementation for the pip/repo dual form.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

# Dual-form bootstrap: core directory (containing this module and aimail_base.py) is _CORE
_CORE = os.path.dirname(os.path.abspath(__file__))

# Release manifest: records the "content hash of the last release of this SDK", used to distinguish between "user has personalized" and "user has not modified"
_MANIFEST = ".aimail-resources.json"

# Executable guidance when resources missing (repo state = read repo root resources/ directly, packaged state = wheel includes them)
_RESOURCE_HINT = ("Repo state: resources taken directly from repo root resources/ (no materialization step needed); pip state: reinstall agent-aimail (wheel includes resources)")

# Source subdirectory (in-package resources/board) → configuration directory target subdirectory
# 2026-09-25 User ruling: board keeps only en role prompts (source directory renamed role_prompt, no longer with _en suffix);
# role_prompt_zh / role_soul_en / role_soul_zh three directories have been deleted —— no runtime consumers.
_DIR_MAP = (
    ("role_prompt", "role_prompt"),
)

_AIMAIL_HOME = os.path.join(os.path.expanduser("~"), ".aimail")


def agentmail_home() -> str:
    return os.environ.get("AIMAIL_HOME", "") or _AIMAIL_HOME


def _is_repo_checkout() -> bool:
    """<core> is pysdk/ and has repo root resources/ next to it ⇒ running in repo checkout (not installed state)."""
    return (os.path.basename(_CORE) == "pysdk"
            and os.path.isdir(os.path.join(os.path.dirname(_CORE), "resources")))


def resources_root() -> str:
    """The resource root of this SDK — self-contained, no dependency on any external materialization steps.

    Repo checkout: use repo root resources/ directly (true source git tracked; preferred over any local residual copies);
    Packaged state: <package>/resources (wheel installs repo root resources/ as aimail/resources).
    """
    if _is_repo_checkout():
        return os.path.join(os.path.dirname(_CORE), "resources")
    return os.path.join(_CORE, "resources")


def resources_board_dir() -> str:
    """The board directory in resources (packaged state aimail/resources/board; repo state <repo>/resources/board)."""
    return os.path.join(resources_root(), "board")


def resources_skills_dir() -> str:
    """The skills directory in resources (SKILL.md / DESCRIPTION.md)."""
    return os.path.join(resources_root(), "skills")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(dst_dir: str) -> dict:
    try:
        with open(os.path.join(dst_dir, _MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — missing/corrupt manifest equivalent to "no record"
        return {}


def _save_manifest(dst_dir: str, data: dict) -> None:
    tmp = os.path.join(dst_dir, _MANIFEST + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(dst_dir, _MANIFEST))


def release_resources(system_id: str, board_root: str | None = None) -> dict:
    """Release board resources to ~/.aimail/systems/{sid}/board/ (idempotent).

    Decision criteria (Audit D6, 2026-09-21): **by content** rather than mtime.
      · Target missing            → Release
      · Target content == package content      → Skip (same version)
      · Target content == last release hash (user has not modified) → Overwrite with new package content (SDK upgrade takes effect)
      · Otherwise (user has personalized/no record) → Skip, never overwrite user content
    mtime determination will miss releases: SDK upgrade unpacking timestamp is earlier than target file, or user has touched target,
    new content will never be released. Manifest is recorded in .aimail-resources.json in the target directory.
    """
    src_root = board_root or resources_board_dir()
    # Missing resources are no longer silently skipped (2026-09-25 single source of truth refactoring): package-bundled resources **must be complete**,
    # Otherwise it's a packaging/materialization defect — the silent consequence is "all gates green but zero host role resources", which is harder to debug.
    # Strict side = default source (package-internal resources/board) and package-internal paths used by install/CLI;
    # Fixture/tool roots explicitly passed by callers only require existence (allow local layout).
    if not os.path.isdir(src_root):
        raise RuntimeError(f"Resource directory missing: {src_root}({_RESOURCE_HINT})")
    if os.path.abspath(src_root) == os.path.abspath(resources_board_dir()):
        missing = [s for s, _ in _DIR_MAP if not os.path.isdir(os.path.join(src_root, s))]
        if missing:
            raise RuntimeError(
                f"Resources incomplete: {src_root} missing subdirectory {missing}({_RESOURCE_HINT})")
    board_dir = os.path.join(agentmail_home(), "systems", system_id, "board")
    copied = 0
    updated = 0
    skipped = 0
    for src_name, dst_name in _DIR_MAP:
        src_dir = os.path.join(src_root, src_name)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(board_dir, dst_name)
        os.makedirs(dst_dir, exist_ok=True)
        manifest = _load_manifest(dst_dir)
        dirty = False
        for fname in sorted(os.listdir(src_dir)):
            if not fname.endswith(".md"):
                continue
            src = os.path.join(src_dir, fname)
            dst = os.path.join(dst_dir, fname)
            src_hash = _sha256(src)
            if os.path.exists(dst):
                dst_hash = _sha256(dst)
                if dst_hash == src_hash:
                    skipped += 1
                    continue
                recorded = manifest.get(fname)
                if recorded and dst_hash == recorded:
                    shutil.copy2(src, dst)
                    manifest[fname] = src_hash
                    updated += 1
                    dirty = True
                    continue
                skipped += 1        # User has personalized (or no record): never overwrite
                continue
            shutil.copy2(src, dst)
            manifest[fname] = src_hash
            copied += 1
            dirty = True
        if dirty or not os.path.exists(os.path.join(dst_dir, _MANIFEST)):
            _save_manifest(dst_dir, manifest)
    return {"board_dir": board_dir, "copied": copied, "updated": updated, "skipped": skipped}


def release_all_systems(board_root: str | None = None) -> list:
    """Expand all existing systems under ~/.aimail/systems/ (also covers single-system machines)."""
    systems_root = os.path.join(agentmail_home(), "systems")
    if not os.path.isdir(systems_root):
        return []
    out = []
    for ent in sorted(os.listdir(systems_root)):
        p = os.path.join(systems_root, ent)
        if os.path.isdir(p):
            try:
                out.append(release_resources(ent, board_root))
            except Exception:  # noqa: BLE001
                pass
    return out


if __name__ == "__main__":
    # Convenience: python _resources_release.py [system_id...]
    sids = sys.argv[1:] or sorted(
        d for d in os.listdir(os.path.join(agentmail_home(), "systems"))
        if os.path.isdir(os.path.join(agentmail_home(), "systems", d))
    ) if os.path.isdir(os.path.join(agentmail_home(), "systems")) else []
    for sid in sids:
        r = release_resources(sid)
        print(f"{sid}: {r['board_dir']} (copied {r['copied']}, kept {r['skipped']})")
