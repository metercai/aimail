/**
 * Gateway HTTP client — mirrors Python `_GatewayClient` (agentmail_tools.py).
 * Auth: X-Api-Key header (agent-scope key); board APIs add dual-credential
 * email query param when memberEmail provided.
 * Contract: gateway REST API (aimail-gateway).
 */
import type { GatewayResponse, AttachmentSpec } from './types.js'

export class GatewayClient {
  readonly baseUrl: string
  readonly apiKey: string
  readonly timeoutMs: number

  constructor(gatewayUrl: string, apiKey: string, timeoutMs = 30_000) {
    this.baseUrl = gatewayUrl.replace(/\/+$/, '')
    this.apiKey = apiKey
    this.timeoutMs = timeoutMs
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
    const reqHeaders: Record<string, string> = { Accept: 'application/json' }
    if (this.apiKey) reqHeaders['X-Api-Key'] = this.apiKey
    if (headers) Object.assign(reqHeaders, headers)

    let data: Uint8Array | undefined
    if (rawBody) {
      data = rawBody
    } else if (body !== undefined) {
      data = new TextEncoder().encode(JSON.stringify(body))
      reqHeaders['Content-Type'] = reqHeaders['Content-Type'] ?? 'application/json'
    }

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

  // ── Send API ────────────────────────────────────────────────

  async sendMail(opts: {
    to: string
    subject?: string
    body: string
    cc?: string
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
    const boundary = '----AgentMailBoundary'
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
    const url = `${this.baseUrl}/api/v1/attachments/${encodeURIComponent(attachmentId)}`
    try {
      const resp = await fetch(url, {
        headers: { 'X-Api-Key': this.apiKey },
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

  boardHeartbeat(boardId: string, taskId: string, memberEmail: string, note = ''): Promise<GatewayResponse> {
    return this.boardRequest(
      'POST',
      `/api/v1/board/${boardId}/task/${taskId}/heartbeat?actor=toolset`,
      memberEmail,
      { note },
    )
  }
}
