# pysdk — AIMail Python SDK

AIMail (agent mail) runtime for Python hosts: gateway client, 15 mail /
contact / note / board / identity tools, the inbound preprocess chain, an MCP
server, and ready-made adapters for Hermes and DeerFlow.

Published as `aimailsdk` on PyPI (import name `aimail`). The wheel mirrors this
directory 1:1 and maps the repo-root `resources/` to `aimail/resources/`, so a
repo checkout and an installed package behave the same.

| What | Files |
|---|---|
| Core (stdlib only) | `aimail_base.py` (identity / signature / config / register), `aimail_tools.py` (mail, contacts, notes, `_GatewayClient`), `aimail_board.py` (A2A board), `gateway_api.py` (v1 API client), `_aimail_bootstrap.py` (location-agnostic `sys.path` boot) |
| Host glue | `install.py` (per-host install / uninstall / check-env), `hermes/` (adapter, patches, toolsets, registrar), `deer-flow/` (inbound router, registrar) |
| MCP | `aimail_mcp_server.py` (stdio JSON-RPC) |
| Resources | `resources/skills/`, `resources/board/role_prompt/` (seeds released at install time) |
| Entry | `__init__.py` — `import aimail` re-exports the curated API and boots the core |

## Install

```bash
pip install aimailsdk        # PyPI
pip install .                # from this directory
```

No hard third-party dependency. Optional extras: `aimail[hermes]` (PyYAML),
`aimail[deerflow]` (fastapi), `aimail[docs]` (markitdown).

## How hosts use it

Two entry points, both called by the aimail CLI:

- **Install / uninstall** — `python -m aimail.install install|uninstall --type
  hermes|deerflow --home <platform-root> [--system-id SID]` (the CLI calls the
  same functions through the python door).
- **Ops door** — `python -m aimail.sdk_ops --op <assemble|update|teardown|
  prompt-test|…> --args '<json>'`, one JSON envelope in and out. Register,
  teardown and prompt work all go through this door.

Host adapters never import the package path directly: each entry calls
`_aimail_bootstrap()`, which resolves the core directory in every layout
(site-packages, bundler copy, repo checkout).

## Resources

Resources are **seeds**, released at install time: board prompts land in
`~/.aimail/systems/{system_id}/board/` (read by both the Python and TypeScript
runtimes, so one edit applies to every platform), and skills are expanded into
each host's own skills directory (hermes: per-profile `skills/`).

## Development

```bash
python3 -c "import sys; sys.path.insert(0, 'pysdk'); import aimail_base"
python3 -m venv /tmp/v && /tmp/v/bin/pip install . && /tmp/v/bin/python -c "import aimail; print(aimail.core_dir())"
```

## Related

- [aimail monorepo](https://github.com/metercai/aimail) — CLI (`cli/`), TypeScript SDK (`tssdk/`), bridge.
- [aimail-gateway](https://github.com/metercai/aimail-gateway) — the mail service this SDK talks to.
