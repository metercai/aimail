# SSE contract gap fixes — 2026-11-18

## Fixed in this change

- **G1 startup autoBind**：dsh `mail-service.apply` no longer calls
  `ensureDefaultAgentBinding`; Pi/OpenClaw SSE startup no longer calls
  `tryAutoBindOnce`. Startup only opens streams for existing bindings.
- **G3 session_id**: removed `session_id` from dsh `mail-service` auto-bind and
  `register-cli` binding writes. Binding routing is workspace-based.
- **G4 routeThread**: dsh `mail-hub.deliver` now calls
  `runtime/session-router.routeThread` for followup/new decision.
- **G7 ack-on-workspace-miss**: dsh `workspaceFor` no longer fabricates a
  default workspace. Missing `workspace_path` returns false ⇒ no ack.
- **G6 no-binding startup**: SSE startup does not create a mailbox; with no
  binding there is simply no deliverable target.

## Verification

- L0 PASS.
- Real local gateway fixture (`run-l2-local-gateway.sh`):
  - dsh PASS
  - pi PASS
  - openclaw PASS
- Each run includes real system/address activation, workspace assertion, shared
  system stream, welcome delivery.
- DSH tests: 33 passed after routeThread/startup changes.

## Still open / not claimed

- Pi/OpenClaw still do not call `routeThread`; only DSH hub does. Their
  session/new-session behavior remains host-API constrained.
