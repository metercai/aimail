#!/usr/bin/env python3
"""L0 gate — the dsh host image's pnpm warm set must equal the plugin's runtime deps.

The `aimail-host-dsh` image is pure native (the agent-aimail plugin is installed by the
test itself) and the real-host journey runs OFFLINE inside the container: `dsh plugin add
<tgz>` resolves the plugin's declared dependencies through pnpm's store + metadata cache,
and the image sets `PNPM_CONFIG_OFFLINE=true`, so every pnpm operation in the container
resolves purely from the store baked in at build time.

That warm step installs `tests/agent-env/docker/pnpm-warm.json` (the single source). If
the plugin's runtime deps drift from the manifest — a dep added, removed, or a range
bumped — the offline plugin install in the container breaks at QA time, not at build
time. This gate pins the two together at L0:

  PASS  iff pnpm-warm.json["dependencies"] == agent-aimail's runtime "dependencies"
           (exact key set, exact range strings — a broader warm range can resolve a
           different version than the plugin's range, so ranges are compared verbatim).

Peer dependencies are deliberately NOT warmed: they are provided by the host (dsh base /
openclaw / pi) at runtime and `dsh plugin add` does not install them into the profile
workspace (measured: profile node_modules contains only the plugin + its runtime deps).

rc: 0 = pass; 1 = drift/violation; 2 = cannot judge (file missing/unreadable).
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WARM = os.path.join(ROOT, "tests", "agent-env", "docker", "pnpm-warm.json")
PLUGIN_PKG = os.path.join(ROOT, "tssdk", "packages", "agent-aimail", "package.json")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[warm-deps] CANNOT JUDGE: missing {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"[warm-deps] CANNOT JUDGE: {path}: {e}", file=sys.stderr)
        sys.exit(2)


def main():
    warm = load_json(WARM).get("dependencies")
    plugin = load_json(PLUGIN_PKG).get("dependencies")
    if not isinstance(warm, dict) or not isinstance(plugin, dict):
        print("[warm-deps] CANNOT JUDGE: 'dependencies' missing in manifest or plugin package.json",
              file=sys.stderr)
        sys.exit(2)

    warm_names = set(warm)
    plugin_names = set(plugin)
    missing = plugin_names - warm_names
    extra = warm_names - plugin_names
    stale = [f"{k}: warm={warm[k]!r} plugin={plugin[k]!r}"
             for k in sorted(warm_names & plugin_names) if warm[k] != plugin[k]]

    if missing or extra or stale:
        print("[warm-deps] FAIL: pnpm-warm.json drifted from the plugin's runtime deps — "
              "the offline plugin install in the dsh host image will break", file=sys.stderr)
        for name in sorted(missing):
            print(f"  ✗ plugin dep not warmed: {name} {plugin[name]!r}", file=sys.stderr)
        for name in sorted(extra):
            print(f"  ✗ warmed dep no longer a plugin dep (stale): {name} {warm[name]!r}", file=sys.stderr)
        for line in stale:
            print(f"  ✗ range drift — {line} (warm range must match the plugin range verbatim)", file=sys.stderr)
        print("  fix: update tests/agent-env/docker/pnpm-warm.json to the plugin's current "
              "runtime deps, then rebuild the host image (host-images workflow).", file=sys.stderr)
        sys.exit(1)

    print(f"[warm-deps] pnpm-warm.json == plugin runtime deps ({len(warm)} dep(s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(warm.items())) + ")")


if __name__ == "__main__":
    main()
