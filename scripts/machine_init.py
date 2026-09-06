#!/usr/bin/env python3
"""Machine-level environment init for AIMail hosts.

Formerly the `aimail init` CLI subcommand. It exists as a standalone
script so the bootstrap flow can run it without an extra subcommand:
  - home dir skeleton (~/.aimail/{systems,logs,bridge}, 0700; silent)
  - disk check (warns below 100 MiB)
  - gateway resolution: shell env > ~/.aimail/.env > default remote
  - local gateway (127.0.0.1/localhost/this host) → direct-push, no bridge
  - remote gateway → bridge binary + skeleton config (deploy_bridge
    --init); bridge key + start happen at the first `aimail install`

Safe to re-run (everything is idempotent). Exit 0 on success.
"""
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

AM_HOME = Path(os.environ.get("AIMAIL_HOME", "") or os.path.expanduser("~/.aimail"))


def env_val(key: str, fallback: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v
    env_file = AM_HOME / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "="):
                return line[len(key) + 1:].strip()
    return fallback


def is_local_gateway(url: str) -> bool:
    try:
        host = urlparse(url).netloc.rsplit("@", 1)[-1]
        if ":" in host and not host.startswith("["):
            host = host.rsplit(":", 1)[0]
        host = host.strip("[]")
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        for info in socket.getaddrinfo(socket.gethostname(), None):
            if info[4][0] == host:
                return True
    except OSError:
        pass
    return False


def main() -> int:
    # 1) home skeleton (0700) — silent, idempotent
    try:
        for sub in ("systems", "logs", "bridge"):
            (AM_HOME / sub).mkdir(parents=True, exist_ok=True)
        os.chmod(AM_HOME, 0o700)
    except OSError as e:
        print(f"  ⚠ home dir prepare failed: {e}", file=sys.stderr)
        return 1

    # 2) disk — warn only below threshold
    try:
        st = os.statvfs(str(AM_HOME))
        free_mib = st.f_bavail * st.f_frsize // 1024 // 1024
        if free_mib < 100:
            print(f"  ⚠ disk free {free_mib} MiB — below 100 MiB", file=sys.stderr)
    except OSError:
        pass

    # 3) gateway resolution + network-structure decision
    gw = os.environ.get("GATEWAY_URL", "") or env_val("AIMAIL_URL", "")
    if not gw:
        gw = "https://aimail.token.tm"  # default remote gateway
    print(f"  init: gateway = {gw}", flush=True)
    if is_local_gateway(gw):
        print("  ✓ local gateway (direct push) — no bridge needed; install activates the system")
        return 0

    # 4) remote gateway → bridge binary + skeleton via deploy_bridge --init
    toolkit = Path(__file__).resolve().parent.parent  # <toolkit>/scripts/..
    deploy = toolkit / "cli" / "deploy_bridge.py"
    env = dict(os.environ,
               GATEWAY_URL=gw,
               WEBHOOK_MODE=env_val("AIMAIL_WEBHOOK_MODE", "bridge"))
    return subprocess.call([sys.executable, str(deploy), "--init"], env=env)


if __name__ == "__main__":
    sys.exit(main())
