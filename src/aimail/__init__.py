# -*- coding: utf-8 -*-
"""aimail — AgentMail runtime SDK (Python). Unified public entry point.

This package is the single public surface for integrating aimail: both the
platform hosts (Hermes / OpenClaw / DeerFlow) and third-party agents should
``import aimail`` and use the re-exported API, rather than each doing its own
``sys.path`` bootstrap + flat-script imports.

Unified usage (recommended — hosts AND third parties):

    import aimail
    aimail.send_mail(to="x@example.com", subject="hi", body="hello")
    client = aimail.GatewayClient(aimail.agent_email(), api_key="...")
    aimail.manage_contacts(action="list")

The runtime core is implemented as flat scripts (``aimail_base.py``,
``aimail_tools.py``, ...) that import each other by top-level name, so they
must sit on ``sys.path``. ``import aimail`` does that bootstrap for you and
re-exports the curated public API below. The legacy flat path (insert
``aimail.core_dir()`` on ``sys.path`` then ``import aimail_tools``) still
works and is what the host adapters' bootstrap relies on — it is preserved,
not replaced.

Module layout inside the wheel mirrors the repository ``tools/`` directory so
the runtime's flat imports keep working:

  aimail/
    aimail_base.py        shared core (platform-agnostic)
    aimail_tools.py       shared core (GatewayClient / send_mail)
    aimail_board.py       shared core (A2A board)
    gateway_api.py           standard amail API client
    amail_mcp_server.py      platform-agnostic MCP server (stdio JSON-RPC)
    _aimail_bootstrap.py     location-agnostic sys.path bootstrap (runtime glue)
    hermes/aimail_hermes.py   Hermes adapter
    openclaw/                 OpenClaw adapter (CLI / bridge / shared)
    deer-flow/                DeerFlow adapter (inbound router / shared)
    skills/                   agentmail SKILL.md + DESCRIPTION.md
    board_role_prompt_en/     board role prompt templates (en)
"""

import os as _os
import sys as _sys

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # path helpers
    "root",
    "core_dir",
    "skills_dir",
    "board_role_prompt_dir",
    "mcp_server_path",
    # identity
    "agent_email",
    # outbound
    "send_mail",
    "GatewayClient",
    # contacts
    "manage_contacts",
    "contact_profile",
    "set_contact_profile",
    # email
    "email_summary",
    "set_email_summary",
    "store_inbound_message",
    "render_message",
]


# ── Path helpers ──────────────────────────────────────────────────────────

def root() -> str:
    """Absolute path of this package directory."""
    return _os.path.dirname(_os.path.abspath(__file__))


def core_dir() -> str:
    """Directory holding the flat-script core modules (aimail_base.py ...).

    Insert this on ``sys.path`` to enable the legacy flat imports::

        import sys, aimail
        sys.path.insert(0, aimail.core_dir())
        import aimail_base

    In an installed wheel the flat modules are force-included into the package
    dir, so this equals ``root()``. In a raw repo checkout it resolves to
    ``<repo>/tools/``. Either way, the returned dir is guaranteed to contain
    ``aimail_base.py``.
    """
    return _resolve_core_dir()


def skills_dir() -> str:
    """agentmail SKILL.md / DESCRIPTION.md directory."""
    return _os.path.join(root(), "skills")


def board_role_prompt_dir() -> str:
    """Board role prompt templates (English) directory."""
    return _os.path.join(root(), "board_role_prompt_en")


def mcp_server_path() -> str:
    """Path of the platform-agnostic MCP server entry script."""
    return _os.path.join(root(), "amail_mcp_server.py")


# ── Unified core bootstrap + re-export ────────────────────────────────────

def _resolve_core_dir() -> str:
    """Locate the directory containing the flat core modules.

    Resolution order (most specific first):
      1. installed layout — flat modules sit directly in the package dir
      2. repo checkout    — ``src/aimail/`` → ``<repo>/tools/``
      3. AIMAIL_RUNTIME_DIR env override (explicit)

    Returns the first candidate that contains ``aimail_base.py``, falling
    back to ``root()`` (best effort) if none match.
    """
    here = root()
    # 1. installed / bundle layout: core modules are flat siblings here.
    if _os.path.isfile(_os.path.join(here, "aimail_base.py")):
        return here
    # 2. raw repo checkout: package lives in src/aimail/, core in tools/.
    repo_tools = _os.path.abspath(_os.path.join(here, "..", "..", "tools"))
    if _os.path.isfile(_os.path.join(repo_tools, "aimail_base.py")):
        return repo_tools
    # 3. explicit override.
    env = _os.environ.get("AIMAIL_RUNTIME_DIR", "").strip()
    if env:
        d = _os.path.expanduser(env)
        if _os.path.isfile(_os.path.join(d, "aimail_base.py")):
            return d
    return here


def _import_core():
    """Idempotently put the core dir on sys.path and import the flat core
    modules as top-level modules (the same module objects the host adapters
    use — no dual-module identity problem). Returns (base, tools, board, api).
    """
    core = _resolve_core_dir()
    if core not in _sys.path:
        _sys.path.insert(0, core)
    # Reuse the single-source runtime bootstrap so any entry-point layout
    # (adapter subdirs, env override) is handled consistently.
    try:
        import _aimail_bootstrap as _b
        _b.ensure_core(core)
    except Exception:
        pass  # core already on path from the insert above; bootstrap is glue.
    import aimail_base as _base
    import aimail_tools as _tools
    import aimail_board as _board
    import gateway_api as _api
    return _base, _tools, _board, _api


_base, _tools, _board, _api = _import_core()

# Re-export the curated public API (single public entry point).
send_mail = _tools.send_mail
GatewayClient = _tools._GatewayClient
agent_email = _tools._resolve_agent_email
manage_contacts = _tools.manage_contacts
contact_profile = _tools.contact_profile
set_contact_profile = _tools.set_contact_profile
email_summary = _tools.email_summary
set_email_summary = _tools.set_email_summary
store_inbound_message = _tools.store_inbound_message
render_message = _base.render_message
