"""SDK resource single source of truth contract (2026-09-25 refactoring; 2026-10-11 cancelled repo-level materialization script).

Convention:
  * Source of truth = repo root ``resources/{board,skills}`` —— git tracked, only editable location.
  * The ``resources/`` inside each adapter package is the generated artifact **temporarily staged by each package's own prepack**
    (``tssdk/packages/agent-aimail/scripts/stage-resources.mjs --dest .``):
    Must be git ignored, not committed to repo; if present, **must be byte-for-byte identical to source of truth** (otherwise it is "only one copy modified" drift).
  * pysdk does not keep copies: in repo checkout, SDK directly reads repo root ``resources/``
    (``_resources_release.resources_root``); wheel self-containment is guaranteed by pyproject's directory-level
    force-include (source=repo root ``resources/``); artifact layer fingerprint verification is in L2 check-tarball.sh.

This test is read-only and has no external dependencies (no absolute paths to dev machines — the repo root is derived from this file's location).
"""
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANON = REPO / "resources"
# Only lists **in-package** generated artifacts: pysdk no longer needs a copy (repo checkout reads the true source directly).
# 2026-10-22 Single package merge: The original 3 adapter host packages each staged a copy => converged to a single copy within the agent-aimail package.
TARGETS = {
    "agent-aimail": REPO / "tssdk" / "packages" / "agent-aimail" / "resources",
}
EXPECTED_FILE_COUNT = 8  # 6 board role prompts(role_prompt/) + 2 skills


def _fingerprint(d: Path) -> dict:
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip()


def test_canonical_source_exists_and_is_complete():
    for sub in ("board", "skills"):
        assert (CANON / sub).is_dir(), f"True source missing {sub}/: {CANON}"
    fp = _fingerprint(CANON)
    assert len(fp) == EXPECTED_FILE_COUNT, (
        f"True source file count {len(fp)} != contract {EXPECTED_FILE_COUNT};"
        f"Add/remove resources please sync this assertion with pyproject/check-tarball criteria: {sorted(fp)}")
    for sub in ("role_prompt",):
        assert (CANON / "board" / sub).is_dir(), f"True source missing board/{sub}/"
    for f in ("SKILL.md", "DESCRIPTION.md"):
        assert (CANON / "skills" / f).is_file(), f"True source missing skills/{f}"


def test_generated_copies_are_not_tracked_by_git():
    """Generated artifacts must never be committed (otherwise it reverts to the old "N copies" problem)."""
    rels = [str(t.relative_to(REPO)) for t in TARGETS.values()]
    tracked = _git("ls-files", "--", *[r + "/" for r in rels])
    assert tracked == "", (
        "In-package generated artifacts are tracked by git (should be gitignored + `git rm -r --cached`):\n"
        + "\n".join(tracked.splitlines()[:5]))


def test_generated_copies_are_gitignored():
    for name, d in TARGETS.items():
        rel = str(d.relative_to(REPO)) + "/board"   # Judgment rule (counts even if path does not exist)
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", rel],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0, f"{name}: {rel} not ignored by .gitignore"


def test_existing_copies_match_canonical():
    """If it exists it must be consistent — un-staged (clean clone) is not a failure; each package's prepack / gate stages it."""
    canon = _fingerprint(CANON)
    present = {n: d for n, d in TARGETS.items() if d.is_dir()}
    for name, d in present.items():
        fp = _fingerprint(d)
        extra = sorted(set(fp) - set(canon))
        missing = sorted(set(canon) - set(fp))
        diff = sorted(k for k in set(canon) & set(fp) if canon[k] != fp[k])
        assert not (extra or missing or diff), (
            f"{name} in-package generated artifacts drifted from true source: extra={extra} missing={missing} diff={diff}\n"
            f"  → Run the package's own prepack: node tssdk/packages/agent-aimail/scripts/"
            f"stage-resources.mjs --dest tssdk/packages/agent-aimail/resources")


def test_sdk_reads_the_canonical_dir_in_a_checkout():
    """pysdk side closed loop: in repo checkout, resource root is the repo root true source (does not depend on any copy/materialization step)."""
    sys.path.insert(0, str(REPO / "pysdk"))
    try:
        import _resources_release as R  # noqa: PLC0415 — solely to trigger repository state resolution
    finally:
        sys.path.pop(0)
    root = Path(R.resources_root()).resolve()
    assert root == CANON.resolve(), (
        f"In the repository checkout, resources_root()={root} should point to the true source {CANON.resolve()}")
    for d in (R.resources_board_dir(), R.resources_skills_dir()):
        assert Path(d).is_dir(), f"{d} does not exist (SDK should be able to read true source directly)"
    assert (Path(R.resources_skills_dir()) / "SKILL.md").is_file(), "True source SKILL.md not visible"
