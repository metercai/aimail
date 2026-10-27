/**
 * Gateway HTTP client — mirrors Python `_GatewayClient` (aimail_tools.py).
 * Auth: v1 signature (X-Api-Identity + X-Api-Timestamp + X-Api-Signature);
 * the raw API key stays offline (see api-signature.ts). Board APIs add dual-
 * credential email query param when memberEmail provided.
 * Contract: gateway REST API + docs/API-SIGNATURE-PROTOCOL.md.
 */
import type { GatewayResponse, AttachmentSpec } from './types.js'
import { computeApiSignature } from './api-signature.js'
import { randomUUID } from 'node:crypto'

/**
 * Stable-per-process client identity for the gateway single-slot mailbox
 * lock (RFC 4122 v4). Generated ONCE per process; the gateway treats it as
 * an opaque, equality-compared string — first connection wins, the same id
 * re-acquires (rotation / reconnect), a different id gets 409 occupied.
 * Parity: py `_CLIENT_ID` (uuid.uuid4), bridge `client_id()` (sha2-derived).
 */
const CLIENT_ID: string = randomUUID()
const CLIENT_ID_HEADER = 'X-Aimail-Client'
import {
  PULL_STREAM_PATH,
  PULL_STREAM_PING_TIMEOUT_MS,
  PULL_STREAM_READ_TIMEOUT_MS,
  type PullBatch,
  type StreamRoundResult,
} from './pull-contract.js'

/**
 * Incremental SSE frame parser (byte-parity with the bridge `SseParser`
 * and py `_SseParser`). Feed arbitrary text chunks (TCP framing does not
 * respect SSE frames); complete frames are returned as they cross a
 * `\n\n` boundary, partial frames stay buffered. Frames with unparseable
 * data are skipped (a bad frame must not kill the stream — the next mail
 * frame must still arrive).
 */
export type SseEvent = ['mail', unknown] | ['ping', null]

export class SseParser {
  private buf = ''

  /** Consume a chunk; return all complete events it completed. */
  feed(chunk: string): SseEvent[] {
    this.buf += chunk
    const out: SseEvent[] = []
    for (;;) {
      const end = this.buf.indexOf('\n\n')
      if (end < 0) return out
      const frame = this.buf.slice(0, end)
      this.buf = this.buf.slice(end + 2)
      const ev = this.parseFrame(frame)
      if (ev) out.push(ev)
    }
  }

  private parseFrame(frame: string): SseEvent | null {
    let eventName: string | null = null
    const dataLines: string[] = []
    let isComment = false
    for (const rawLine of frame.split('\n')) {
      const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
      if (line.startsWith(':')) {
        isComment = true
        continue
      }
      if (line.startsWith('event:')) eventName = line.slice('event:'.length).trim()
      else if (line.startsWith('data:')) {
        // SSE spec: multiple data lines join with '\n'.
        dataLines.push(line.slice('data:'.length).replace(/^\s+/, ''))
      }
      // id:/retry: and unknown fields are ignored.
    }
    if (dataLines.length === 0) return isComment ? ['ping', null] : null
    const data = dataLines.join('\n')
    if (eventName !== 'mail') return null // unknown event name — ignore defensively
    let env: unknown
    try {
      env = JSON.parse(data)
    } catch {
      return null // bad data — skip, keep the stream alive
    }
    if (typeof env !== 'object' || env === null || !Array.isArray((env as { batches?: unknown }).batches)) return null
    return ['mail', (env as { batches: unknown[] }).batches]
  }
}

/** Per-read SSE timeout fired with no frame in the window (stall detector). */
class StreamReadTimeout extends Error {}

/** Race a stream read against the per-read (ping) timeout. */
function withReadTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new StreamReadTimeout()), ms)
    void p.then(
      (v) => { clearTimeout(t); resolve(v) },
      (e) => { clearTimeout(t); reject(e) },
    )
  })
}

export class GatewayClient {
  readonly baseUrl: string
  readonly apiKey: string
  readonly timeoutMs: number
  /** v1 identity: key's email (address-scoped) or its system_id (system keys). */
  readonly identity: string

  constructor(gatewayUrl: string, apiKey: string, timeoutMs = 30_000, identity = '') {
    this.baseUrl = gatewayUrl.replace(/\/+$/, '')
    this.apiKey = apiKey
    this.timeoutMs = timeoutMs
    this.identity = identity
  }

  /** v1 signature headers for a request (empty when no key). */
  private signedHeaders(method: string, path: string, body?: Uint8Array): Record<string, string> {
    const h: Record<string, string> = {}
    if (!this.apiKey) return h
    if (this.identity) h['X-Api-Identity'] = this.identity
    const sig = computeApiSignature(this.apiKey, method, path, body ?? new Uint8Array(0))
    if (sig) {
      h['X-Api-Timestamp'] = sig.timestamp
      h['X-Api-Signature'] = sig.signature
    }
    return h
  }

  /** Core request — mirrors _request: JSON in/out, status never overwritten. */
  async request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
    headers?: Record<string, string>,
    rawBody?: Uint8Array,
  ): Promise<GatewayResponse> {
    const url = `${this.baseUrl}${path}`
    const reqHeaders: Record<string, string> = {
      Accept: 'application/json',
      [CLIENT_ID_HEADER]: CLIENT_ID,
    }

    let data: Uint8Array | undefined
    if (rawBody) {
      data = rawBody
    } else if (body !== undefined) {
      data = new TextEncoder().encode(JSON.stringify(body))
      reqHeaders['Content-Type'] = reqHeaders['Content-Type'] ?? 'application/json'
    }

    // v1 API signature (raw key stays offline).
    Object.assign(reqHeaders, this.signedHeaders(method, path, data))
    if (headers) Object.assign(reqHeaders, headers)

    try {
      const init: RequestInit = {
        method,
        headers: reqHeaders,
        signal: AbortSignal.timeout(this.timeoutMs),
      }
      if (data) init.body = data as BodyInit
      const resp = await fetch(url, init)
      const text = await resp.text()
      const status = resp.status
      try {
        const parsed: unknown = JSON.parse(text)
        if (Array.isArray(parsed)) return { status, data: parsed }
        const obj = parsed as Record<string, unknown>
        delete obj['status']
        return { status, ...obj }
      } catch {
        return { status, body: text }
      }
    } catch (e) {
      return { status: 0, error: e instanceof Error ? e.message : String(e) }
    }
  }

  // ── Stream (SSE) — primary transport (Phase 2) ─────

  /**
   * Signed `GET /stream` probe: true = 200 + text/event-stream.
   * Only the CONNECT/header phase is bounded (30s) — the body is never
   * consumed here. A streamless/old server (404/405/transport failure)
   * ⇒ false ⇒ the state machine falls to long-pull.
   */
  async streamProbe(): Promise<boolean> {
    const path = PULL_STREAM_PATH
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          [CLIENT_ID_HEADER]: CLIENT_ID,
          ...this.signedHeaders('GET', path, new Uint8Array(0)),
        },
        signal: AbortSignal.timeout(30_000),
      })
      const ctype = (resp.headers.get('content-type') ?? '').toLowerCase()
      const ok = resp.status === 200 && ctype.startsWith('text/event-stream')
      if (resp.body) await resp.body.cancel().catch(() => {})
      return ok
    } catch {
      return false
    }
  }

  /**
   * One stream round: open the SSE connection, read until it ends.
   *
   * Per-read timeout = min(pingTimeoutMs, PULL_STREAM_READ_TIMEOUT_MS)
   * (default ping 30s; the server heartbeats every 15s, so a healthy
   * stream always yields a frame in the window) — the dead-link /
   * buffered-proxy detector.
   *
   * Returns 'stop' (signal aborted), 'stay' (server closed at its max
   * life — reconnect), 'drop' (connect failed / stalled — no alternative
   * transport, the caller drops straight to Poll), or 'error' (mid-stream
   * transport error — the caller applies the decision function with the
   * fail streak, bridge parity).
   */
  async streamRound(
    onBatches: (batches: PullBatch[]) => void | Promise<void>,
    signal?: AbortSignal,
    pingTimeoutMs?: number,
  ): Promise<StreamRoundResult> {
    const path = PULL_STREAM_PATH
    let resp: Response
    try {
      resp = await fetch(`${this.baseUrl}${path}`, {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          [CLIENT_ID_HEADER]: CLIENT_ID,
          ...this.signedHeaders('GET', path, new Uint8Array(0)),
        },
      })
    } catch {
      return 'drop' // connect failed
    }
    if (resp.status === 409) {
      // Single-slot lock held by ANOTHER client: the caller stays in the
      // current mode and backoffs against the SAME endpoint (no downgrade,
      // no transport streak — bridge/py parity).
      await resp.body?.cancel().catch(() => {})
      return 'occupied'
    }
    const ctype = (resp.headers.get('content-type') ?? '').toLowerCase()
    if (resp.status !== 200 || !ctype.startsWith('text/event-stream')) {
      await resp.body?.cancel().catch(() => {})
      return 'drop'
    }
    const body = resp.body
    if (!body) return 'drop'
    const reader = body.getReader()
    const decoder = new TextDecoder()
    const readTimeoutMs = Math.min(pingTimeoutMs ?? PULL_STREAM_PING_TIMEOUT_MS, PULL_STREAM_READ_TIMEOUT_MS)
    const parser = new SseParser()
    type ReadChunk = { done: boolean; value: Uint8Array | undefined }
    try {
      for (;;) {
        if (signal?.aborted) return 'stop'
        let chunk: ReadChunk
        try {
          chunk = await withReadTimeout(reader.read(), readTimeoutMs)
        } catch (e) {
          if (e instanceof StreamReadTimeout) return 'drop' // no data (heartbeat or mail) ⇒ stalled
          return 'error' // mid-stream transport error (reset etc.)
        }
        if (chunk.done) return 'stay' // server closed (max-life rotation)
        const text = decoder.decode(chunk.value ?? new Uint8Array(0), { stream: true })
        if (text) {
          for (const [kind, batches] of parser.feed(text)) {
            if (kind === 'mail' && Array.isArray(batches) && batches.length > 0) {
              await onBatches(batches as PullBatch[])
            }
          }
        }
      }
    } finally {
      try {
        reader.releaseLock()
      } catch {
        // already released
      }
      await body.cancel().catch(() => {})
    }
  }

  // ── Send API ────────────────────────────────────────────────

  async sendMail(opts: {
    to: string
    subject?: string
    body: string
    cc?: string[]
    attachments?: AttachmentSpec[]
    inReplyTo?: string
    references?: string
    sender?: string
    messageId?: string
    headers?: Record<string, string>
  }): Promise<GatewayResponse> {
    const payload: Record<string, unknown> = { to: opts.to, markdown: opts.body }
    if (opts.sender) payload['sender'] = opts.sender
    if (opts.subject) payload['subject'] = opts.subject
    if (opts.cc) payload['cc'] = opts.cc
    if (opts.attachments) payload['attachments'] = opts.attachments
    const hdrs: Record<string, string> = {}
    if (opts.messageId) hdrs['Message-ID'] = opts.messageId
    if (opts.inReplyTo) hdrs['In-Reply-To'] = opts.inReplyTo
    if (opts.references) hdrs['References'] = opts.references
    if (opts.headers) Object.assign(hdrs, opts.headers)
    if (Object.keys(hdrs).length > 0) payload['headers'] = hdrs
    return this.request('POST', '/api/v1/send', payload)
  }

  // ── Attachment API ──────────────────────────────────────────

  async uploadAttachment(filePath: string): Promise<GatewayResponse> {
    const { promises: fsp } = await import('node:fs')
    const content = await fsp.readFile(filePath)
    const name = filePath.split('/').pop() || 'file'
    const boundary = '----AIMailBoundary'
    const pre = new TextEncoder().encode(
      `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="${name}"\r\n` +
        'Content-Type: application/octet-stream\r\n\r\n',
    )
    const post = new TextEncoder().encode(`\r\n--${boundary}--\r\n`)
    const body = new Uint8Array(pre.length + content.length + post.length)
    body.set(pre, 0)
    body.set(content, pre.length)
    body.set(post, pre.length + content.length)
    return this.request(
      'POST',
      '/api/v1/upload',
      undefined,
      { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body,
    )
  }

  async downloadAttachment(attachmentId: string): Promise<Uint8Array | undefined> {
    const path = `/api/v1/attachments/${encodeURIComponent(attachmentId)}`
    const url = `${this.baseUrl}${path}`
    try {
      const resp = await fetch(url, {
        headers: this.signedHeaders('GET', path),
        signal: AbortSignal.timeout(this.timeoutMs),
      })
      if (!resp.ok) return undefined
      return new Uint8Array(await resp.arrayBuffer())
    } catch {
      return undefined
    }
  }

  // ── Whitelist / contacts / state / thread-summary ───────────

  checkWhitelist(domainAddr: string, value: string, direction = 'to'): Promise<GatewayResponse> {
    const q = new URLSearchParams({ domain_addr: domainAddr, value, direction })
    return this.request('GET', `/api/v1/whitelists/check?${q}`)
  }

  setWhitelist(domainAddr: string, value: string, direction = 'to'): Promise<GatewayResponse> {
    const q = new URLSearchParams({ domain_addr: domainAddr, value })
    return this.request('PUT', `/api/v1/whitelists?${q}`, { direction })
  }

  deleteWhitelist(domainAddr: string, value: string): Promise<GatewayResponse> {
    const q = new URLSearchParams({ domain_addr: domainAddr, value })
    return this.request('DELETE', `/api/v1/whitelists?${q}`)
  }

  agentStatePut(key: string, value: string): Promise<GatewayResponse> {
    return this.request('PUT', `/api/v1/agent-state/${encodeURIComponent(key)}`, { value })
  }

  contactPut(address: string, profile: string): Promise<GatewayResponse> {
    return this.request('PUT', `/api/v1/contacts/${encodeURIComponent(address)}`, { profile })
  }

  contactGet(address: string): Promise<GatewayResponse> {
    return this.request('GET', `/api/v1/contacts/${encodeURIComponent(address)}`)
  }

  contactSearch(name: string): Promise<GatewayResponse> {
    const q = new URLSearchParams({ name })
    return this.request('GET', `/api/v1/contacts?${q}`)
  }

  /**
   * Batch profile lookup (B1): GET /api/v1/contacts?addresses=a,b,c.
   * The FIRST address is the inbound sender; the rest are recipients.
   * Returns {my_profile, sender_profile, recipients_profile} — empty
   * shapes on failure (caller treats as no profiles available), mirroring
   * Python `get_contact_profiles`.
   */
  async getContactProfiles(addresses: string[]): Promise<{
    my_profile: { address: string; profile: string } | null
    sender_profile: Record<string, string>
    recipients_profile: Record<string, string>
  }> {
    const addrs = addresses.map(a => a.trim()).filter(Boolean)
    const empty = { my_profile: null, sender_profile: {} as Record<string, string>, recipients_profile: {} as Record<string, string> }
    if (!addrs.length) return empty
    const q = new URLSearchParams({ addresses: addrs.join(',') })
    const res = await this.request('GET', `/api/v1/contacts?${q}`)
    if (res.status !== 200) return empty
    const my = res.my_profile as { address?: string; profile?: string } | null | undefined
    return {
      my_profile: my && my.profile ? { address: my.address ?? '', profile: my.profile } : null,
      sender_profile: (res.sender_profile ?? {}) as Record<string, string>,
      recipients_profile: (res.recipients_profile ?? {}) as Record<string, string>,
    }
  }

  // ── Board API (dual-credential: apiKey/token + member email) ─

  private async boardRequest(method: string, path: string, memberEmail: string, body?: Record<string, unknown>): Promise<GatewayResponse> {
    const sep = path.includes('?') ? '&' : '?'
    return this.request(method, `${path}${sep}email=${encodeURIComponent(memberEmail)}`, body)
  }

  boardStatus(boardId: string, memberEmail: string): Promise<GatewayResponse> {
    return this.boardRequest('GET', `/api/v1/board/${boardId}/status`, memberEmail)
  }

  boardTaskShow(taskId: string, boardId: string, memberEmail: string): Promise<GatewayResponse> {
    return this.boardRequest('GET', `/api/v1/board/${boardId}/task/${taskId}`, memberEmail)
  }

  boardTaskList(boardId: string, memberEmail: string, status = '', assignee = ''): Promise<GatewayResponse> {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (assignee) params.set('assignee', assignee)
    const qs = params.toString()
    return this.boardRequest('GET', `/api/v1/board/${boardId}/tasks${qs ? `?${qs}` : ''}`, memberEmail)
  }

  boardMembers(boardId: string, memberEmail: string, email = ''): Promise<GatewayResponse> {
    const qs = email ? `?email=${encodeURIComponent(email)}` : ''
    return this.boardRequest('GET', `/api/v1/board/${boardId}/members${qs}`, memberEmail)
  }

  boardRoles(boardId: string, memberEmail: string, role = ''): Promise<GatewayResponse> {
    const qs = role ? `?role=${encodeURIComponent(role)}` : ''
    return this.boardRequest('GET', `/api/v1/board/${boardId}/roles${qs}`, memberEmail)
  }

  boardHeartbeat(boardId: string, taskId: string, memberEmail: string, note = ''): Promise<GatewayResponse> {
    return this.boardRequest(
      'POST',
      `/api/v1/board/${boardId}/task/${taskId}/heartbeat?actor=toolset`,
      memberEmail,
      { note },
    )
  }
}
