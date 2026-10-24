"""Host peer scope criteria (2026-10-11) -- General gate for S-1 class drift.

Background (dsh × 6 cells, L3): `dsh-aimail`'s host `peerDependencies` is pinned to the old line
(`@deepseek-ai/dsh-agent: ^0.1.0-rc.6`), while host pinned by `tests/agent-env/pins.env`
(`DSH_VERSION`) follows the `next` line (0.2.0-rc.2) ⇒ dsh's compatibility gate directly rejects plugin installation, platform cannot install it.
The same risk applies to pi / openclaw, it just happens to also be satisfied.

Criterion: For each "package that declares a host peer", assert that the host version pinned in pins.env **satisfies** that range.
 the verdict is delegated to semver (node; already in tssdk's pnpm store), no hand-rolled semver — prerelease
"same tuple only" rule is easy to get wrong: this round's test `>=0.1.0-rc.6 <0.3.0` does **not** match
`0.2.0-rc.2`, while `^0.1.0-rc.6 || ^0.2.0-rc.2` does match.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent

# host → (key in pins.env, set of peer target packages for this host **released in the same version as this host**).
# Empirical basis (2026-10-11, aimail-host-dsh image): dsh 0.2.0-rc.2 ships with
# dsh-agent/dsh-llm/dsh-tools 0.2.0-rc.2 (same version line) ⇒ host version can be used as target version.
# 2026-10-22: 5 packages merged into single package agent-aimail — peer set converges to a single manifest.
# Single package carries peers for three hosts, same package name cannot be a dict key ⇒ table changed to list.
# Each host peer is still judged for same version line by its own pin; unlisted peers must be registered in _EXCLUDED_PEERS.
_HOSTS = [
    ("agent-aimail", "DSH_VERSION", {
        "@deepseek-ai/dsh-agent", "@deepseek-ai/dsh-llm", "@deepseek-ai/dsh-tools"}),
    ("agent-aimail", "PI_VERSION", {"@earendil-works/pi-coding-agent"}),
    ("agent-aimail", "OPENCLAW_VERSION", {"openclaw"}),
]

# Cannot use "host version" as the target version for peer: must explicitly register the reason here (new peers must not silently miss detection).
_EXCLUDED_PEERS = {
    # The version of cordis is **not on the same line** as the dsh host version (dsh 0.2.0-rc.2 ships with cordis 4.0.4),
    # cannot obtain its ground truth offline ⇒ manually verified once this round (`^4.0.1` satisfies 4.0.4, so dsh did not flag it);
    # To auto-judge, an offline source of truth for "host → sibling package versions" is needed (to be discussed separately).
    "@deepseek-ai/cordis": "Version is on a different line from host version (empirically dsh 0.2.0-rc.2 ships with cordis 4.0.4)",
}


def _pins() -> dict:
    pins = {}
    for line in (_REPO / "tests" / "agent-env" / "pins.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            pins[k.strip()] = v.strip()
    return pins


def _semver_dir() -> Path | None:
    """semver from the pnpm store (CI: sdk-gates runs `pnpm install --frozen-lockfile` first)."""
    cands = []
    store = _REPO / "tssdk" / "node_modules" / ".pnpm"
    if store.is_dir():
        cands += sorted(store.glob("semver@*/node_modules/semver"))
    cands += sorted((_REPO / "tssdk" / "node_modules").glob("semver"))
    for c in cands:
        if (c / "package.json").is_file():
            return c
    return None


def _satisfies(version: str, rng: str, semver_dir: Path) -> bool:
    js = "const s=require(process.env.SV);console.log(s.satisfies(process.env.V,process.env.R)?'1':'0')"
    out = subprocess.run(
        ["node", "-e", js],
        env={**os.environ, "SV": str(semver_dir), "V": version, "R": rng},
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise AssertionError(f"semver judgment unavailable (node rc={out.returncode}): {out.stderr.strip()[:200]}")
    return out.stdout.strip() == "1"


@pytest.fixture(scope="module")
def semver_dir() -> Path:
    d = _semver_dir()
    if d is None:
        pytest.skip("semver not installed — run `pnpm install` in tssdk first (CI's sdk-gates does this)")
    if not _has_node():
        pytest.skip("node unavailable")
    return d


def _has_node() -> bool:
    try:
        return subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def test_judge_itself_detects_the_s1_condition(semver_dir):
    """Negative self-proof: the criterion must be able to go red — the old declaration (pinned to the old line) judges 0.2.0-rc.2 false."""
    assert not _satisfies("0.2.0-rc.2", "^0.1.0-rc.6", semver_dir), "criterion always true = useless"
    assert _satisfies("0.2.0-rc.2", "^0.1.0-rc.6 || ^0.2.0-rc.2", semver_dir)


def test_host_peers_accept_the_pinned_host_version(semver_dir):
    pins = _pins()
    bad, unchecked = [], []
    # Single package carries peers for multiple hosts: peer belonging to any host row is accepted in that row's iteration, others skipped
    # (in the old 5-package era, each package only carried its own host's peer, no union needed).
    union = set()
    for _, _, peers in _HOSTS:
        union |= set(peers)
    for pkg, pin_key, same_line in _HOSTS:
        pj = json.loads((_REPO / "tssdk" / "packages" / pkg / "package.json").read_text(encoding="utf-8"))
        host_ver = pins.get(pin_key, "")
        assert host_ver, f"pins.env missing {pin_key}"
        for peer, rng in (pj.get("peerDependencies") or {}).items():
            if peer in same_line:
                if not _satisfies(host_ver, rng, semver_dir):
                    bad.append(f"{pkg}: {peer} {rng} does not accept {pin_key}={host_ver}")
            elif peer in union:
                continue  # peer from other host rows, accepted in its own row's iteration
            elif peer not in _EXCLUDED_PEERS:
                unchecked.append(f"{pkg}: {peer} {rng}")
    assert not unchecked, ("Added unclassified host peer —— either include in same version line determination,",
                           f"or register in _EXCLUDED_PEERS with reason: {unchecked}")
    assert not bad, "Host peer does not accept host version pinned in pins.env (S-1 class): " + "; ".join(bad)
