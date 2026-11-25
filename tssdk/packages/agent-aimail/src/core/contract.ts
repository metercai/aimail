/**
 * contract.ts — AIMail agent-side naming contract (sole copy on the TS side).
 *
 * ⚠ Values must equal the repo root `contract/aimail-contract.json` (single source of truth).
 * This file is **hardcoded constants + gate assertions** (it never reads the manifest at runtime):
 * npm artifacts are self-contained (the files glob does not include the repo root `contract/`), so
 * reading it in a released package would be ENOENT. `tests/contract/check-contract-single-source.py`
 * asserts item by item in the L0 gate that the two are equal (drift turns red with file:line).
 *
 * Boundary (owner ruling 2026-09-27): the legitimate `aimail` surface = product/repo/framework/CLI
 * name + `~/.aimail` + `AIMAIL_*` environment variables — these are **not** internal agent contract
 * names. This module only constrains the agent-internal **naming** surface: skill/toolset
 * registration names and binding/pointer file names.
 *
 * There are NO endpoint constants any more (2026-11-25): the loopback listener was removed because
 * SSE-V2 delivers by pulling the gateway stream. A host that answers on a local port is not "more
 * deliverable" — the delivery model is `docs/aimail-host-sse-contract-v2.md`. The v1 hermes webhook
 * path pair left the same day with its source-patch adapter.
 */

/** Internal agent skill registration name == SKILL.md frontmatter `name:` (directory name is the same). */
export const AGENT_SKILL_NAME = 'agentmail'

/** Internal agent toolset registration name == SKILL.md frontmatter `toolset:`. */
export const AGENT_TOOLSET_NAME = 'agentmail'

/** Binding file name for each address (systems/{sid}/{cleaned_addr}/agentmail.json). */
export const BINDING_FILE = 'agentmail.json'

/** System pointer file name (e.g., ~/.pi/.agentmail). */
export const POINTER_FILE = '.agentmail'
