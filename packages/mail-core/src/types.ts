/**
 * AgentMail shared types — mirror of agentmail.json schema & gateway API shapes.
 * Contract baseline: AGENTMAIL-JSON-REFERENCE.md (agentmail repo).
 */

/** Per-address agent config (agentmail.json) — the single source of truth. */
export interface AgentConfig {
  /** agent amail address (identity; outbound sender == key.email enforced server-side) */
  email: string
  /** gateway base URL e.g. https://amail.token.tm */
  gateway_url: string
  /** mail domain (address assembly only) */
  domain: string
  /** system identifier (directory key) */
  system_id: string
  /** system name (shared-domain address segment) */
  system_name?: string
  /** manager address (inbound whitelist, welcome recipient) */
  manager_address?: string
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
  /** dsh: session id (instance identity; 1 address per session) */
  session_id?: string
  /** dsh: preset name (definition layer) */
  preset?: string
  /** gate for raw out-/in- snapshot writes (default false); meta is ALWAYS written */
  save_raw_snapshots?: boolean
  /** internal: absolute path of this config file */
  _config_path?: string
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
  my_amail_addr: string
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
