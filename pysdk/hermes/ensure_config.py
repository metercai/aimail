"""ensure_config.py — Idempotently ensure Hermes profile's webhook inbound config is in place (library form).

Root cause of installation broken chain (2026-08-16 multiple times): installation chain (install-tools.sh / configure.sh /
register_profiles.py) never wrote the following configs, relying entirely on manual completion——missing any item breaks the chain:
  1. profile config.yaml `platform_toolsets.webhook` missing `agentmail`
     → webhook session falls back to default toolset (hermes-webhook, no send_mail),
     agent physically cannot reply to emails ("can receive but cannot send")
  2. profile config.yaml `platform_toolsets.cli` is missing `agentmail`
     → CLI session has no email tools (user decision 2026-08-16: "cli needs to add")
  3. profile config.yaml `platforms.webhook.enabled` is missing
     → registration chain _ensure_profile_webhook cannot read it, webhook_url is empty

⚠ Toolset key = internal identifier is always `agentmail` (external brand aimail does not change internal name;
platform_toolsets / skills directory / agentmail.json etc. Agent internal semantics are all agentmail).

Routing (webhook_subscriptions.json) is created by registration chain _auto_register_email →
_ensure_webhook_route creating hermes's route name (contract HERMES_ROUTE_NAME =
`aimail-inbound`, inbound path = contract HERMES_INBOUND_PATH =
`/webhooks/aimail-inbound`; skills list = internal tool/skill name = contract
AGENT_SKILL_NAME = `agentmail`)——**no second inbound route needed**: bridge forwarding
path is taken from the full URL in the routing table; registering one is the contract route name.
(Paths/names are all taken from aimail_contract constants, do not write a second literal: true source =
repo root contract/aimail-contract.json, gate tests/contract/check-contract-single-source.py)

This module is idempotent: existing config items are preserved (especially secret——changes will cause bridge forwarding
HMAC 401); only missing items are added. Called by hermes/register_profiles.py (install chain per-profile
implementation); original CLI main() has been removed (argparse entry no longer needed).

Migrated from cli/hermes/ensure_webhook_config.py: function body preserved verbatim, argparse
main removed. Public API: ensure_profile_config(profile_dir) -> list,
"""

import os
import secrets
import sys
from pathlib import Path

# ── Dual-form bootstrap (repo pysdk/ ↔ pip site-packages/aimail/) ──
# repo form: dirname(dirname(__file__)) = pysdk/ (contains aimail_base.py etc. flat core,
# bare import available) → added to sys.path. pip form: that directory = site-packages/ (no flat core),
# need to import aimail first to trigger aimail/__init__.py glue (inserts aimail/ directory into sys.path,
# flat core bare import then available).
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_CORE_DIR, "aimail_base.py")):
    if _CORE_DIR not in sys.path:
        sys.path.insert(0, _CORE_DIR)
else:
    try:
        import aimail  # noqa: F401  (pip form glue)
    except Exception:
        pass

# agent side contract constants (true source = repo root contract/aimail-contract.json)
import aimail_contract as _contract  # noqa: E402

# webhook session default toolset (user approved); only ensures contract toolset name exists, others not overwritten
WEBHOOK_TOOLSET = [_contract.AGENT_TOOLSET_NAME, "web", "file", "terminal", "search", "delegation"]


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _dump_yaml(path: Path, data: dict) -> None:
    import yaml
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(path)


def ensure_profile_config(profile_dir: Path) -> list:
    """Ensure platforms.webhook.enabled + platform_toolsets.webhook/cli contains agentmail."""
    changes = []
    cfg_path = profile_dir / "config.yaml"
    if cfg_path.exists():
        cfg = _load_yaml(cfg_path)
    else:
        # J4e root cause (2026-09-30 fixed, evidence two lines):
        #   iso16 log line 557: ensure_profile_config: ['config.yaml missing
        #   (/opt/data/config.yaml) — skipped'] —— first install hermes had not yet generated
        #   config.yaml, old logic directly returned ⇒ platform_toolsets never added;
        #   after hermes built config, subsequent installs were short-circuited by .agentmail pointer (iso16  contract-allowed: comment text (non-code) references contract pointer filename
        #   line 86 registered:0) no longer reached here ⇒ webhook session cannot get agentmail toolset.  contract-allowed: comment text (non-code) references contract toolset name
        # Fix: missing file ⇒ idempotent creation (only writes this module's responsible platforms.webhook +
        # platform_toolsets two groups of keys, other keys left to hermes's own defaults/subsequent writes,
        # existing keys are all preserved —— same semantics as "only add missing items" below).
        cfg = {}
        changes.append(f"config.yaml created ({cfg_path})")

    dirty = False

    # 1) platforms.webhook.enabled (registration chain _ensure_profile_webhook depends on it)
    platforms = cfg.get("platforms") or {}
    wh = platforms.get("webhook") or {}
    if not wh.get("enabled"):
        # Reuse existing port/secret, generate if missing——same structure as _ensure_profile_webhook
        port = wh.get("port") or wh.get("extra", {}).get("port") or 8644
        secret = wh.get("extra", {}).get("secret") or secrets.token_hex(32)
        platforms["webhook"] = {
            "enabled": True,
            "host": "0.0.0.0",
            "port": port,
            "extra": {"port": port, "secret": secret},
        }
        cfg["platforms"] = platforms
        changes.append(f"platforms.webhook enabled (port={port})")
        dirty = True

    # 2) platform_toolsets.webhook contains contract toolset name (webhook session tool capability)
    pt = cfg.get("platform_toolsets") or {}
    wh_tools = pt.get("webhook") or []
    if not isinstance(wh_tools, list):
        wh_tools = []
    if _contract.AGENT_TOOLSET_NAME not in wh_tools:
        if not wh_tools:
            wh_tools = list(WEBHOOK_TOOLSET)
        else:
            wh_tools.append(_contract.AGENT_TOOLSET_NAME)
        pt["webhook"] = wh_tools
        cfg["platform_toolsets"] = pt
        changes.append(f"platform_toolsets.webhook -> {wh_tools}")
        dirty = True

    # 3) platform_toolsets.cli contains contract toolset name (user specified cli should also be added)
    cli_tools = pt.get("cli") or []
    if not isinstance(cli_tools, list):
        cli_tools = []
    if _contract.AGENT_TOOLSET_NAME not in cli_tools:
        cli_tools.append(_contract.AGENT_TOOLSET_NAME)
        pt["cli"] = cli_tools
        cfg["platform_toolsets"] = pt
        changes.append(f"platform_toolsets.cli -> {cli_tools}")
        dirty = True

    if dirty:
        _dump_yaml(cfg_path, cfg)
    return changes


