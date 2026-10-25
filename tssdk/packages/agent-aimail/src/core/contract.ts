/**
 * contract.ts — AIMail agent-side contract constants (sole copy on the TS side).
 *
 * ⚠ Values must equal the repo root `contract/aimail-contract.json` (single source of truth).
 * This file is in the form of **hardcoded constants + gate assertions** (not reading files at runtime): npm artifacts are self-contained
 * (bundleDependencies / files glob does not include repo root contract/), reading the manifest at runtime in the release form
 * will inevitably be ENOENT — so the true source is persisted to disk, constants are hardcoded, by
 * `tests/contract/check-contract-single-source.py` asserts item by item in the L0 gate that the two are equal
 * (drift turns red and provides file:line).
 *
 * Boundary (owner ruling 2026-09-27): the legitimate `aimail` surface = product/repo/framework/CLI name +
 * `~/.aimail` + `AIMAIL_*` environment variables — these are **not** internal agent contract names, do not follow
 * violations by this module. This module only constrains the agent internal contract surface: skill/toolset registration names, binding
 * file names, pointer file names, inbound paths.
 */

/** Fixed inbound paths for the four platforms (openclaw / dsh / pi / deer-flow). */
export const INBOUND_PATH = '/aimail/inbound'

/** hermes is the only exception: inbound is hooked to the hermes gateway's own webhook route. */
export const HERMES_INBOUND_PATH = '/webhooks/aimail-inbound'

/** hermes gateway route name (route key in webhook_subscriptions.json). */
export const HERMES_ROUTE_NAME = 'aimail-inbound'

/** Internal agent skill registration name == SKILL.md frontmatter `name:` (directory name is the same). */
export const AGENT_SKILL_NAME = 'agentmail'

/** Internal agent toolset registration name == SKILL.md frontmatter `toolset:`. */
export const AGENT_TOOLSET_NAME = 'agentmail'

/** Binding file name for each address (systems/{sid}/{cleaned_addr}/agentmail.json). */
export const BINDING_FILE = 'agentmail.json'

/** System pointer file name (e.g., ~/.pi/.agentmail). */
export const POINTER_FILE = '.agentmail'

/**
 * Common default inbound listening port for hosts whose SDK owns the listener
 * (dsh/pi) — **port is configurable, path is immutable**. On conflict the
 * runtime re-selects an available port (bind probe = source of truth); the
 * confirmed callback (`--inbound-live --inbound-url`) pins the real endpoint.
 * Hosts where the host gateway owns the port (hermes/deerflow/openclaw) carry
 * no default here — per-host adaptation discovers the actual port.
 */
export const INBOUND_PORT: number = 9099

/**
 * the bridge default forward path — **retired (SDK de-bridged, owner ruling 2026-09-28)**.
 * Routing is the CLI's environmental responsibility (`cli/bridge_wire.py`, triggered by host-notified `aimail address
 * --inbound-live` trigger), SDK is bridge-agnostic, contract true source no longer contains bridge keys (see
 * contract/aimail-contract.json + pysdk/aimail_contract.py, all three converged in the same batch).
 */

/** Local inbound receiving endpoint (non-hermes platforms). */
export function inboundUrl (port: number, host = '127.0.0.1'): string {
  return `http://${host}:${port}${INBOUND_PATH}`
}
