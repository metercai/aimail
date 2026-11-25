/**
 * binding-provenance — can this `agentmail.json` be trusted to arm a mailbox?
 *
 * The binding file is a CACHE, not the authority: the authority is the agent-scope key the
 * gateway issued when an activation code was consumed (`POST /api/v1/activate-address-code`),
 * and every pull is authenticated with it. A hand-written file therefore buys nothing by itself:
 *
 *   - without a real key the gateway answers 401 and no mail is ever delivered;
 *   - with a real key for THAT address it is the same capability an activation would have granted;
 *   - writing someone else's address does not fetch their mail — the gateway resolves the address
 *     from the key, so `email` here is only a local label.
 *
 * What a hand-written file CAN do, and what this module closes, is make the SDK *believe* a
 * half-invented binding: missing the AgentSystemId, missing `expires_at`, or pretending to belong
 * to an anchor it never got from activation. That is a diagnostic and integrity problem, not a
 * privilege escalation — but a silent one, which is exactly what we do not want.
 *
 * So the activation path stamps two fields (`binding_source`, `activated_at`) and this module
 * judges them. There is no switch and no compatibility mode: nothing has shipped yet, so a binding
 * without the stamp cannot be a legitimate legacy artefact — it is either a hand-written file or a
 * bug in the writer. Either way it must not arm, and the refusal must say why.
 */

/** The only value a real activation writes into `binding_source`. */
export const BINDING_SOURCE_ACTIVATION = 'activate-address-code'

/** The seven fields a V2 binding must carry (contract §7.1). */
export const REQUIRED_BINDING_FIELDS = (
  ['email', 'gateway_url', 'domain', 'system_id', 'agent_system_id', 'api_key', 'agent_id'] as const
)

export interface ProvenanceVerdict {
  ok: boolean
  /** Human-readable reason; empty when ok. */
  reason: string
}

/** Judge one binding. Never throws; an absent/partial file is simply "not proven". */
export function bindingProvenance(cfg: Record<string, unknown> | undefined | null): ProvenanceVerdict {
  if (!cfg || typeof cfg !== 'object') return { ok: false, reason: 'no binding' }
  const missing = REQUIRED_BINDING_FIELDS.filter((f) => !String(cfg[f] ?? '').trim())
  if (missing.length > 0) {
    return { ok: false, reason: `missing required field(s): ${missing.join(', ')}` }
  }
  const source = String(cfg.binding_source ?? '').trim()
  if (!source) {
    return {
      ok: false,
      reason:
        'no activation stamp (binding_source) — this file was not written by ' +
        'activateAddressCodePersist; re-bind with an activation code',
    }
  }
  if (source !== BINDING_SOURCE_ACTIVATION) {
    return { ok: false, reason: `unknown binding_source '${source}' (expected ${BINDING_SOURCE_ACTIVATION})` }
  }
  const at = String(cfg.activated_at ?? '').trim()
  if (!at || Number.isNaN(Date.parse(at))) {
    return { ok: false, reason: `activated_at is missing or not a timestamp ('${at}')` }
  }
  return { ok: true, reason: '' }
}

// ── gateway verdict ───────────────────────────────────────────────────────────
//
// The local stamp is a self-consistency check; the AUTHORITY is the gateway, which authenticates
// every pull with the binding's key. When it answers 401/403 for long enough to be fatal
// (PullAuthError), the binding's claims have been rejected by the only party that can judge them —
// so it must not be re-armed in this process, or the runtime would keep retrying a binding the
// gateway already refused (and keep it looking "configured").

const _refusedByGateway = new Set<string>()

/** Remember that the gateway rejected this address's key (call on an auth-fatal pull error). */
export function refuseBindingAfterGatewayRejection(email: string): void {
  const e = String(email ?? '').trim().toLowerCase()
  if (e) _refusedByGateway.add(e)
}

/** Did the gateway already reject this address in this process? */
export function isRefusedByGateway(email: string): boolean {
  return _refusedByGateway.has(String(email ?? '').trim().toLowerCase())
}

/** Test hook: forget gateway verdicts. */
export function resetGatewayRejections(): void {
  _refusedByGateway.clear()
}
