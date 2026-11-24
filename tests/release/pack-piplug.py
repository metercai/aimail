#!/usr/bin/env python3
"""Pack a PI-Desktop plugin directory into a `.piplug` (store-only zip) and verify it.

Why a script instead of `zip -r`: the host rejects anything that is not store-only
(`extract_zip_bytes`, ADR 0007). `zip -r` defaults to deflate, so a package built that way
installs nowhere. This writes every entry with ZIP_STORED and then re-opens the archive to prove it
— the reader is the same code path the host uses (name, method, size), so a bug here fails here.

Constraints enforced (from the handoff's §7.2 and the host's walk.ts / MAX_PACKAGE_*):
  * layout       : manifest.json at the archive ROOT, next to main.js
  * methods      : ZIP_STORED only
  * limits       : <= 2000 entries, <= 50 MiB uncompressed, no symlinks
  * never packed : .git/, node_modules/, .env*, .npmrc, *.pem, *.key
  * integrity    : sha256 over the archive BYTES, written next to it
  * naming       : <id>-<version>.piplug   (id/version come from manifest.json)
  * one version  : manifest.version == package.json version (the build step also refuses otherwise)

Usage: pack-piplug.py --dist <dir> --out <dir> [--plug-package <package.json>]
Exit codes: 0 packed and verified · 2 usage · 3 constraint violated
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import zipfile

FORBIDDEN_DIRS = {".git", "node_modules"}
FORBIDDEN_FILES = [".env", ".env.*", ".npmrc", "*.pem", "*.key"]
# Mirrors the host's PLUGIN_ID_PATTERN (packages/plugin-sdk/src/index.ts). Note the FIRST segment
# allows no hyphen/underscore — only later segments do — which is why "agent-aimail.desktop" is
# rejected by the installer even though it looks reasonable. If the host changes the pattern, this
# must follow; the gate would otherwise bless an artifact that installs nowhere.
PLUGIN_ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_-]+)+$")
MAX_ENTRIES = 2000
MAX_BYTES = 50 * 1024 * 1024


def fail(msg: str) -> "None":
    print("PACK FAIL: " + msg, file=sys.stderr)
    raise SystemExit(3)


def forbidden(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in FORBIDDEN_DIRS for p in parts):
        return True
    base = parts[-1]
    return any(fnmatch.fnmatch(base, pat) for pat in FORBIDDEN_FILES)


def collect(dist: str) -> list:
    entries = []
    for root, dirs, files in os.walk(dist):
        dirs.sort()
        for name in sorted(files):
            src = os.path.join(root, name)
            rel = os.path.relpath(src, dist).replace(os.sep, "/")
            if os.path.islink(src):
                fail("symlink in package: " + rel)
            if forbidden(rel):
                fail("forbidden entry in package: " + rel)
            entries.append((src, rel))
    return entries


def source_date(start: str) -> str:
    """Commit date, not wall-clock: a stamp with `builtAt = now()` makes the archive irreproducible —
    the local build and the CI build of the same commit then differ (caught by comparing the release
    asset hash with the hash I had signed)."""
    import subprocess
    d = start
    for _ in range(6):
        try:
            r = subprocess.run(["git", "-C", d, "log", "-1", "--format=%cI"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        d = os.path.dirname(d)
    return ""


def git_sha(start: str) -> str:
    """Best-effort: the commit the artifact was built from, so the pairing is recorded at build time."""
    import subprocess
    d = start
    for _ in range(6):
        try:
            r = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        d = os.path.dirname(d)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plug-package", default="", help="plugin package.json (version cross-check)")
    ap.add_argument("--sdk-package", default="",
                    help="package.json of the SDK that gets bundled; its name+version are stamped")
    ap.add_argument("--dirty-paths", default="",
                    help="repo-relative paths to check for uncommitted changes; dirty => refuse (unless --allow-dirty)")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--stamp-name", default="build.json",
                    help="name of the build stamp written into the archive root")
    a = ap.parse_args()

    dist = os.path.abspath(a.dist)
    manifest_path = os.path.join(dist, "manifest.json")
    if not os.path.isfile(manifest_path):
        fail("manifest.json must sit at the package root: " + manifest_path)
    manifest = json.load(open(manifest_path))
    plug_id = str(manifest.get("id") or "").strip()
    version = str(manifest.get("version") or "").strip()
    if not plug_id or not version:
        fail("manifest.json needs both id and version")
    if not PLUGIN_ID_RE.match(plug_id):
        fail("manifest.id %r violates the host pattern %s (first segment: lowercase letters/digits only; "
             "hyphens are allowed only in later segments)" % (plug_id, PLUGIN_ID_RE.pattern))

    # the bundle must be require-able: the package root is "type": "module", so dist carries its own
    pj = os.path.join(dist, "package.json")
    if not os.path.isfile(pj):
        fail("dist/package.json missing (the host require()s main.js; without {\"type\":\"commonjs\"} it is parsed as ESM)")
    if json.load(open(pj)).get("type") != "commonjs":
        fail("dist/package.json must declare \"type\": \"commonjs\"")
    if not os.path.isfile(os.path.join(dist, "main.js")):
        fail("dist/main.js missing")

    if a.plug_package:
        pv = json.load(open(a.plug_package)).get("version")
        if pv and pv != version:
            fail("manifest.version %s != package.json version %s (single source of truth)" % (version, pv))

    entries = collect(dist)
    if len(entries) > MAX_ENTRIES:
        fail("%d entries exceeds the host limit %d" % (len(entries), MAX_ENTRIES))
    total = sum(os.path.getsize(src) for src, _ in entries)
    if total > MAX_BYTES:
        fail("%d bytes exceeds the host limit %d" % (total, MAX_BYTES))

    # Refuse to stamp a dirty tree: the stamp's gitSha would then name a commit that does not contain
    # the bytes in the archive. Paid for: 0.2.0 was first built before committing, so local and CI
    # hashes disagreed (61504d0b… vs fd1f298b…) purely because the local gitSha was the parent commit.
    if a.dirty_paths and not a.allow_dirty:
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(a.dist))))
        for _ in range(6):
            if os.path.isdir(os.path.join(root, ".git")):
                break
            root = os.path.dirname(root)
        dirty = []
        for rel in a.dirty_paths.split(","):
            rel = rel.strip()
            if not rel:
                continue
            r = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no", "--", rel],
                               capture_output=True, text=True)
            dirty += [l for l in r.stdout.splitlines() if l.strip()]
        if dirty:
            fail("working tree has uncommitted changes under the packaged paths; the stamp's gitSha would "
                 "name a commit that does not contain these bytes:\n  " + "\n  ".join(dirty) +
                 "\nCommit first, or pass --allow-dirty to package an explicitly unversioned build.")

    # Build stamp: the artifact must say which SDK it embeds. Without this, rebuilding the plugin at
    # the same version after an SDK change yields a different file under an identical name and the
    # pairing can only be recovered from git history. Packaging owns this, not the product build.
    stamp = {
        "plugin": plug_id,
        "pluginVersion": version,
        "sourceDate": source_date(os.path.dirname(os.path.abspath(a.dist))),
        "gitSha": git_sha(os.path.dirname(os.path.abspath(a.dist))),
    }
    if a.sdk_package:
        sdk = json.load(open(a.sdk_package))
        stamp["sdk"] = sdk.get("name")
        stamp["sdkVersion"] = sdk.get("version")
    stamp_bytes = (json.dumps(stamp, indent=2, sort_keys=True) + "\n").encode()
    if forbidden(a.stamp_name):
        fail("stamp name is a forbidden entry: " + a.stamp_name)

    os.makedirs(a.out, exist_ok=True)
    name = "%s-%s.piplug" % (plug_id, version)
    out = os.path.join(a.out, name)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as z:
        for src, rel in entries:
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED
            zi.external_attr = 0o644 << 16
            with open(src, "rb") as fh:
                z.writestr(zi, fh.read())
        zi = zipfile.ZipInfo(a.stamp_name, date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        zi.external_attr = 0o644 << 16
        z.writestr(zi, stamp_bytes)

    # verify by re-reading: same checks the host's extractor makes
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
        if bad:
            fail("corrupt entry after write: " + bad)
        names = z.namelist()
        if "manifest.json" not in names:
            fail("manifest.json is not at the archive root")
        methods = {i.compress_type for i in z.infolist()}
        if methods != {zipfile.ZIP_STORED}:
            fail("archive is not store-only (methods seen: %s)" % sorted(methods))
        if any(n.startswith("/") or ".." in n.split("/") for n in names):
            fail("unsafe path inside archive")
        for n in names:
            if forbidden(n):
                fail("forbidden entry survived: " + n)
        if a.stamp_name not in names:
            fail("build stamp missing from the archive: " + a.stamp_name)
        got = json.loads(z.read(a.stamp_name))
        if got.get("pluginVersion") != version:
            fail("stamp pluginVersion %s != manifest %s" % (got.get("pluginVersion"), version))
        if a.sdk_package and not got.get("sdkVersion"):
            fail("stamp carries no sdkVersion although --sdk-package was given")

    digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
    with open(out + ".sha256", "w") as fh:
        fh.write("%s  %s\n" % (digest, name))

    print("PACK OK %s" % out)
    print("  entries=%d bytes_archive=%d bytes_payload=%d method=store" %
          (len(entries), os.path.getsize(out), total))
    print("  sha256=%s" % digest)
    print("  stamp: " + json.dumps(stamp, sort_keys=True))
    print("  files: " + ", ".join(sorted(n for n in names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
