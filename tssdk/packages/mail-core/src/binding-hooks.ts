/**
 * Binding-created hooks — the hot-add seam.
 *
 * Address-code activation happens long after the host booted, while the pull
 * runtime enumerates bindings once at startup. After a binding is persisted the
 * activation path fires these hooks, so the runtime that is already running can
 * start a loop for the new mailbox **without a restart**.
 *
 * Python counterpart: `aimail_base.register_binding_created_hook`.
 */
import type { AgentConfig } from './types.js'

export type BindingCreatedHook = (cfg: AgentConfig) => void | Promise<void>

const hooks: BindingCreatedHook[] = []

/** Register a hook; returns the unsubscribe function (idempotent). */
export function onBindingCreated(fn: BindingCreatedHook): () => void {
  hooks.push(fn)
  return () => {
    const i = hooks.indexOf(fn)
    if (i >= 0) hooks.splice(i, 1)
  }
}

/** Fire the hooks. A throwing hook must never break the activation itself. */
export async function fireBindingCreated(cfg: AgentConfig): Promise<void> {
  for (const fn of [...hooks]) {
    try {
      await fn(cfg)
    } catch (e: unknown) {
      console.warn(`[aimail] binding-created hook failed: ${String(e)}`)
    }
  }
}
