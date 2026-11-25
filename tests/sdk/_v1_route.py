"""v1 inbound route, for tests that exercise the still-shipped v1 adapters.

Until 2026-11-25 `/aimail/inbound` was a **contract value** (`INBOUND_PATH`). It is not any more:
SSE-V2 hosts deliver by pulling, so no adapter binds a local listener and the constant is gone from
`aimail_contract` and from `contract/aimail-contract.json`.

The v1 deer-flow adapter still ships its in-process FastAPI route, and several v1 tests build
`webhook_url` fixtures from it. They need the string, so it lives here — in test support, marked as
v1-only — instead of being re-invented per file or smuggled back into the contract.
"""

V1_INBOUND_PATH = "/aimail/inbound"
V1_INBOUND_PORT = 9099
V1_INBOUND_URL = f"http://127.0.0.1:{V1_INBOUND_PORT}{V1_INBOUND_PATH}"
