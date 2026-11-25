/**
 * AIMail shared types — mirror of agentmail.json schema & gateway API shapes.
 * Contract baseline: AGENTMAIL-JSON-REFERENCE.md (aimail repo).
 */

/** Per-address agent config (agentmail.json) — the single source of truth. */
export interface AgentConfig {
  /** agent AIMail address (identity; outbound sender == key.email enforced server-side) */
  email: string
  /** gateway base URL e.g. https://aimail.token.tm */
  gateway_url: string
  /** mail domain (address assembly only) */
  domain: string
  /** system identifier (directory key) */
  system_id: string
  /** stable local Agent-system instance identity (SSE aggregation) */
  agent_system_id?: string
  /** system name (shared-domain address segment) */
  system_name?: string
  /** manager address (inbound whitelist, welcome recipient) */
  manager_address?: string
  /** prompt rules: subject/body/sender/recipient → role file stem
   * (ruling 2026-09-23; loose-validated by readPromptRules) */
  prompt_rules?: unknown[]
  /** agent-scope API key (64 hex) */
  api_key: string
  /** local inbound endpoint full URL (paired with webhook_secret) */
  webhook_url?: string
  /** inbound HMAC verify secret (paired with webhook_url) */
  webhook_secret?: string
  /** platform agent id (OpenClaw/DeerFlow) */
  agent_id?: string
  /** DeerFlow assistant definition id */
  assistant_id?: string
  /** dsh legacy: last session hint (read-only; thread meta is authoritative) */
  session_id?: string
  /** dsh: preset name (definition layer) */
  preset?: string
  /** dsh: workspace registry id (cache; workspace_path is the truth) */
  workspace_id?: string
  /** dsh: canonical workspace path this mailbox is bound to */
  workspace_path?: string
  /** dsh: workspace display title */
  workspace_title?: string
  /** dsh base multi-domain: per-domain domain keys */
  domain_keys?: Record<string, string>
  /** dsh base multi-domain: all domains of the system */
  domains?: string[]
  /** gate for raw out-/in- snapshot writes (default false); meta is ALWAYS written */
  save_raw_snapshots?: boolean
  /**
   * Provenance stamp — written ONLY by the activation chain (activateAddressCodePersist).
   * Absence means "this binding was not produced by consuming an activation code"; see
   * core/binding-provenance.ts for how that is judged (strict vs report-only).
   */
  binding_source?: string
  /** RFC3339 instant at which the activation code was consumed (provenance, not expiry). */
  activated_at?: string
  /**
   * Address validity end (RFC3339) — written by the address-code
   * consumption chain (activateAddressCodePersist). Present only for
   * addresses bound via an activation code; the register path leaves it
   * unset. The gateway enforces the same instant (expired key ⇒ 401).
   */
  expires_at?: string
  /** internal: absolute path of this config file */
  _config_path?: string
  /**
   * Local receive gate (lifecycle pause/resume). `true` = receiving is
   * stopped locally; the cloud key stays active (the file IS the signal a
   * cross-process CLI flips — the pull loop re-checks it every beat).
   * Absent/false = receiving. Written by `setBindingPaused`.
   */
  paused?: boolean
}

/** Gateway HTTP response shape (Python _GatewayClient compatible). */
export interface GatewayResponse {
  status: number
  data?: unknown
  error?: string
  body?: string
  [k: string]: unknown
}

/** Attachment descriptor as accepted by gateway send API. */
export interface AttachmentSpec {
  attachment_id?: string
  id?: string
  filename?: string
  name?: string
  [k: string]: unknown
}

/** Inbound webhook payload (gateway raw body, pre-preprocess). */
export interface InboundPayload {
  mail_id?: string
  message_id?: string
  subject?: string
  body?: string
  to?: string | string[]
  cc?: string | string[]
  from?: string
  headers?: Record<string, string>
  references?: string[]
  attachments?: AttachmentSpec[]
  created_at?: string
  forwarder?: string
  forward_at?: string
  board_id?: string
  board_role?: string
  [k: string]: unknown
}

/** Enriched payload after preprocess (agent-visible format, contract §3). */
export interface EnrichedPayload extends Record<string, unknown> {
  message_id?: string
  subject?: string
  body?: string
  sender?: string
  recipients?: { to: string[]; cc: string[] }
  my_aimail_addr: string
  direct_message: boolean
  mentioned: boolean
  attachments?: string[]
  references?: string[]
  _preprocess_error?: string
  _whoami_prompt?: string
  _role_prompt?: string
  _a2a_session_key?: string
  /** B1 batch profiles (injected only when the gateway returns them). */
  my_profile?: string
  sender_profile?: Record<string, string>
  recipients_profile?: Record<string, string>
  /** B2 thread_summary preload (existing threads only). */
  thread_summary?: string
}

/**
 * Typed inbound delivery outcome (REQ-4).
 *
 * Only `accepted` (taken into a session) and `intercepted` (ping/pong or
 * another handled frame that needs no session) may be acked. `deferred`
 * (temporarily cannot deliver) and `rejected` (bad target/payload/session
 * creation failure) stay pending for a later round.
 */
export type InboundResult =
  | { status: 'accepted'; detail?: string }
  | { status: 'intercepted'; detail?: string }
  | { status: 'deferred'; detail?: string }
  | { status: 'rejected'; detail?: string }

/** Ack rule in one place: accepted/intercepted only. */
export function isAckedInboundResult(result: InboundResult): boolean {
  return result.status === 'accepted' || result.status === 'intercepted'
}
