#!/usr/bin/env python3
"""setup_system.py — thin spawn wrapper over the SDK install core.

Architecture (2026-09): install logic lives in the SDKs — pysdk/install_core.py
(python) with a parity tssdk install.ts. This script only translates the
INTEGRATE_* env contract into install_system() and prints JSON (or a line
prefixed __ERROR__) for the spawning caller. It stays a standalone script
because cli/aimail spawns it via sys.executable from the deployed SCRIPTS_DIR
and cli/repair.py imports helpers from the core.
"""
import json
import os
import sys
from pathlib import Path

# Ensure scripts/ dir is on path for gateway_api and local imports
_script_dir = str(Path(__file__).resolve().parent)
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)
# 运行时核心(repo pysdk/ 优先 > pip aimail 兜底)
from runtime_core import load_core  # noqa: E402
load_core()

from install_core import (  # noqa: E402
    create_agent_admin_key,
    detect_system_for_home,
    detect_webhook_host,
    install_system,
    save_system_config,
)

if __name__ == "__main__":
    kwargs = dict(
        gateway_url=os.environ.get("INTEGRATE_GATEWAY_URL", ""),
        system_id=os.environ.get("INTEGRATE_SYSTEM_ID", ""),
        domain=os.environ.get("INTEGRATE_AIMAIL_DOMAIN", "") or "",
        save_raw_snapshots=os.environ.get("INTEGRATE_SAVE_SNAPSHOTS", "true") != "false",
        manager_address=os.environ.get("INTEGRATE_MANAGER_ADDRESS", "") or "",
        webhook_host=os.environ.get("INTEGRATE_WEBHOOK_HOST", "") or "",
        system_name=os.environ.get("INTEGRATE_SYSTEM_NAME", "") or "",
        system_home=os.environ.get("INTEGRATE_SYSTEM_HOME", "") or "",
    )
    if os.environ.get("INTEGRATE_USE_PRODUCT_CODE", "") == "true":
        kwargs["product_code"] = os.environ.get("INTEGRATE_PRODUCT_CODE", "")
    else:
        kwargs["admin_key"] = os.environ.get("INTEGRATE_ADMIN_KEY", "")
    result = install_system(**kwargs)
    display = {k: v for k, v in result.items() if k not in ("success", "path")}
    print(json.dumps(display, indent=2, ensure_ascii=False))
    if not result.get("success"):
        err = result.get("error") or result.get("detail") or "Unknown error"
        print(f"__ERROR__:{err}")
        sys.exit(1)
