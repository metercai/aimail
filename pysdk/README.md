# pysdk — AIMail Python SDK

AIMail (agent mail) Python runtime SDK: gateway HTTP client, the 12 mail /
contact / note / board tool functions, the inbound preprocess chain, an MCP
server, and ready-made host adapters (Hermes / DeerFlow). Board resources
(skills, role prompts, role souls) ship with the package and are released at
install time to the local config directory, where users can personalize them.

This SDK lives in the [metercai/aimail](https://github.com/metercai/aimail)
monorepo under `pysdk/` (CLI in `cli/`, TypeScript SDK in `tssdk/`, bridge in
`bridge/`). The pip package `aimail` is the published surface of this tree —
the wheel mirrors `pysdk/` 1:1, so repo and installed layouts are identical.

| What | File(s) |
|---|---|
| Core (framework-agnostic, stdlib-only) | `aimail_base.py` (identity/signature/config/register), `aimail_tools.py` (send_mail + contacts + notes + `_GatewayClient`), `aimail_board.py` (A2A board), `gateway_api.py` (v1 API client), `_aimail_bootstrap.py` (location-agnostic sys.path boot) |
| MCP server | `amail_mcp_server.py` (stdio JSON-RPC, platform-agnostic) |
| Adapters (host-injected) | `hermes/` (aimail_hermes + patch/register/toolsets, 6 modules), `deer-flow/` (inbound router + amail_base + manage), `openclaw/amail_base.py` |
| Resources | `resources/skills/` (SKILL.md + DESCRIPTION.md), `resources/board/` (role_prompt / role_prompt_zh / role_soul / role_soul_zh) |
| Glue | `__init__.py` — unified entry: `import aimail` re-exports the curated API and boots the flat core |

## Install

```bash
pip install aimail            # from PyPI
pip install .                 # from the repo root (hatchling, force-include)
```

No hard third-party dependencies — the core is stdlib-only. Optional extras,
all lazy/host-gated:
- `aimail[hermes]` — PyYAML for the Hermes adapter's profile-config parsing
- `aimail[deerflow]` — fastapi (the DeerFlow inbound router imports it at
  module level; the DeerFlow host always ships FastAPI)
- `aimail[docs]` — markitdown (attachment → markdown conversion)

Platform adapters are host-injected: importing `aimail.hermes.*` outside a
Hermes gateway prints registration-skipped warnings by design.

## Usage

```python
import aimail

# unified entry (boots the flat core, re-exports the curated API)
aimail.send_mail(to="x@example.com", subject="hi", body="hello")
client = aimail.GatewayClient(aimail.agent_email(), api_key="...")

# legacy flat imports still work (host adapters use this)
import sys, aimail
sys.path.insert(0, aimail.core_dir())
import aimail_base, aimail_tools
```

Host adapters never import the package path directly — each entry calls
`_amail_bootstrap()` (in `_aimail_bootstrap.py`), which resolves the core
directory in all three layouts: pip site-packages, self-contained bundle
(provisioner copies), and repo checkout.

## Resources & the local config directory

Board resources are **seeds**, not read-at-runtime-from-the-SDK files. The
SDK install entry (`python -m aimail.install install --type …`, which the
CLI's `aimail install` delegates to) releases them via
`aimail._resources_release` to:

```
~/.aimail/systems/{system_id}/board/
├── role_prompt/        # en (default) — read by the preprocess chain
├── role_prompt_zh/     # zh reference (reading point reserved)
├── role_soul/          # en souls (distinct from a profile SOUL.md)
└── role_soul_zh/       # zh reference
```

Both the Python and the TypeScript (`tssdk/`) runtimes read from this same
location, so users can edit a role prompt / soul file to personalize behavior
— and the change applies to every platform at once. `en` is the default
language; `zh` ships for reference only.

## Development

```bash
# repo layout: core modules import each other by bare name
python3 -c "import sys; sys.path.insert(0, 'pysdk'); import aimail_base"

# build & verify the wheel (three layouts must all import)
python3 -m venv /tmp/v && /tmp/v/bin/pip install .
/tmp/v/bin/python -c "import aimail; print(aimail.core_dir())"
python3 cli/runtime_bundle.py source      # resolves pip > repo pysdk/
```

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — this monorepo:
  aimail CLI (`cli/`), Python SDK (`pysdk/`, you are here), TypeScript SDK
  (`tssdk/`), bridge distributions.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service, address & activation APIs, and the
  board endpoints the SDK client talks to.
