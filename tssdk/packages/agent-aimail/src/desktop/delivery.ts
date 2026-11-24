/**
 * PI-Desktop delivery port (plan §4 `./desktop` face, contract §11.5).
 *
 * A PI-Desktop plugin runs inside the host and reaches sessions through the injected
 * `pi` API. That API is narrower than RACP, and two of the differences decide how this
 * port must be written:
 *
 *   session.appendMessage  ✅  deliver into an existing session (needs `session.update.all`)
 *   session.import         ✅  allocate a session from an imported transcript (originKind=imported)
 *   session.create         ❌  NOT offered to plugins
 *
 * The payloads are not free-form either — the host validates each one (ADR 0200/0339):
 *
 *   appendMessage → { sessionId, message: { id?, role, content, createdAt: RFC3339 } }
 *   import        → { source, externalId, title, createdAt, updatedAt, messages[], projectId? }
 *
 * Two consequences shape this file:
 *
 * 1. **Delivery is always one `appendMessage` with a deterministic message id.** The host
 *    treats message ids as globally unique and replays of the same id as already-delivered
 *    (`sessions::append_message_with_receipt`), so a retry after a lost response cannot
 *    duplicate the mail. That is the only idempotency the platform offers, and it is why
 *    the id is derived from the mail rather than left to the host.
 *
 * 2. **A new thread is allocated with `import`, and the import carries the mail.** The host
 *    dedupes imports on `(pluginId, source, externalId)`; with `externalId` derived from the
 *    mail, a retried import answers `skipped: true` for a session that already holds that
 *    exact mail — so the retry is a success, not a second copy. The price is a documented
 *    one: the session's `originKind` is `imported`, not `created` (contract §11.5).
 *
 * Nothing here acknowledges anything. Every refusal — missing capability, failed pre-flight
 * bound, host error envelope, thrown RPC error — comes back as a typed `skip`, and the
 * caller leaves the mail pending instead of parking it in the wrong conversation.
 */

/** Subset of the host's `pi` API this face needs (structural, so tests can fake it). */
export interface DesktopHostApi {
  session?: {
    appendMessage?: (input: Record<string, unknown>) => Promise<unknown>
    list?: (input?: Record<string, unknown>) => Promise<unknown>
    get?: (input: Record<string, unknown>) => Promise<unknown>
    import?: (input: Record<string, unknown>) => Promise<unknown>
    /** Present on hosts that expose real creation; absent today (verified 2026-11-24). */
    create?: (input: Record<string, unknown>) => Promise<unknown>
  }
}

export type DesktopCapabilities = {
  append: boolean
  create: boolean
  importCreate: boolean
}

export function desktopCapabilities(api: DesktopHostApi): DesktopCapabilities {
  const s = api.session ?? {}
  return {
    append: typeof s.appendMessage === 'function',
    create: typeof s.create === 'function',
    importCreate: typeof s.import === 'function',
  }
}

/** Host ceilings, restated here so a refusal happens before the RPC, not after it. */
export const DESKTOP_DELIVERY_LIMITS = {
  titleChars: 200,
  externalIdChars: 256,
  messageIdChars: 128,
  contentBytes: 512 * 1024,
} as const

/** Import origin recorded by the host for a session this plugin created by import. */
export const DESKTOP_IMPORT_SOURCE = 'aimail'
export const DESKTOP_IMPORT_SOURCE_LABEL = 'AIMail'

export type DesktopDeliveryKind = 'append' | 'create+append' | 'import' | 'import-existing'

export type DesktopDeliveryResult =
  | {
      status: 'accepted'
      kind: DesktopDeliveryKind
      sessionId: string
      /** The host's id for the delivered row (ours, since we chose it). */
      messageId?: string
      /** Set when the host refused the space binding and the mail landed unbound. */
      projectDropped?: boolean
    }
  | { status: 'skip'; reason: string }

export interface DesktopDeliveryRequest {
  /** Target session; empty means "this thread needs a new one". */
  sessionId?: string
  /** Bound space (a host project id, numeric). Metadata — never identity. */
  projectId?: string
  /** Mail text after the shared preprocessing chain. */
  text: string
  /** RFC822 `Message-ID`; the deterministic anchor for both ids below. */
  messageId?: string
  /** Thread root, so an imported session can be titled with it. */
  threadRoot?: string
  /** RFC3339 receive time; defaults to the clock. */
  receivedAt?: string
}

export interface DesktopDeliveryOptions {
  /** Injectable clock so the payload is assertable in tests. */
  now?: () => Date
}

const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function clip(value: string, max: number): string {
  return [...value].slice(0, max).join('')
}

/** Fold any id into the host's id alphabet; stable, so retries carry the same string. */
function slug(value: string): string {
  return value
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

/** FNV-1a over UTF-8, so a mail without a Message-ID still gets a stable anchor. */
function fingerprint(value: string): string {
  let hash = 0x811c9dc5
  for (const byte of new TextEncoder().encode(value)) {
    hash ^= byte
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash.toString(16).padStart(8, '0')
}

/**
 * The message id we deliver under.
 *
 * Deterministic in the mail (Message-ID first, content hash otherwise) so that a retry
 * after a lost response lands on the row the host already holds instead of a duplicate.
 */
export function destinationMessageId(req: DesktopDeliveryRequest): string {
  const anchor =
    slug(text(req.messageId)) || slug(text(req.threadRoot)) || `auto-${fingerprint(text(req.text))}`
  return clip(`aimail-${anchor}`, DESKTOP_DELIVERY_LIMITS.messageIdChars)
}

/** The import origin id; per mail, so a retried import answers `skipped` for itself only. */
export function importExternalId(req: DesktopDeliveryRequest): string {
  return clip(destinationMessageId(req), DESKTOP_DELIVERY_LIMITS.externalIdChars)
}

export function sessionTitle(req: DesktopDeliveryRequest): string {
  const root = text(req.threadRoot)
  const title = root ? `${DESKTOP_IMPORT_SOURCE_LABEL} ${root}` : DESKTOP_IMPORT_SOURCE_LABEL
  return clip(title, DESKTOP_DELIVERY_LIMITS.titleChars)
}

function byteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength
}

/** The pre-flight checks shared by both write paths; `null` means "send it". */
function payloadProblem(req: DesktopDeliveryRequest): string | null {
  if (!text(req.text)) return 'empty-text'
  if (byteLength(req.text) > DESKTOP_DELIVERY_LIMITS.contentBytes) return 'content-too-large'
  return null
}

/** The highest session id an answer names, whatever envelope the host chose. */
function idOf(value: unknown): string {
  const v = (value ?? {}) as Record<string, unknown>
  const nested = (k: string) => (v[k] as Record<string, unknown> | undefined)?.id
  const direct = v.sessionId ?? v.id ?? nested('session') ?? v.messageId ?? nested('message')
  return String(direct ?? '').trim()
}

type Refusal = { code: string; message: string }

/** Turn an answer or a thrown RPC error into a refusal, or `null` when it was accepted. */
function refusalOf(value: unknown): Refusal | null {
  if (value === undefined || value === null) return null
  if (typeof value !== 'object') return null
  const v = value as Record<string, unknown>
  if (v.ok === false || v.error) {
    const error = (v.error ?? {}) as Record<string, unknown>
    return {
      code: text(error.code) || text(v.code) || 'HOST_REFUSED',
      message: text(error.message) || text(v.error) || 'host refused the write',
    }
  }
  return null
}

function thrownRefusal(error: unknown): Refusal {
  const e = (error ?? {}) as Record<string, unknown>
  const code = text(e.code) || 'HOST_ERROR'
  const message = text(e.message) || String(error)
  // Host errors arrive either with a `code` field or as `"CODE: text"`.
  const marked = /^([A-Z][A-Z0-9_]{2,}):\s*(.*)$/s.exec(message)
  return marked
    ? { code, message: marked[2] }
    : { code, message }
}

export interface DesktopDelivery {
  capabilities: DesktopCapabilities
  /** Deliver into an existing session. Never creates. */
  append(req: DesktopDeliveryRequest): Promise<DesktopDeliveryResult>
  /** Allocate a session for a new thread (import-based when `create` is absent). */
  create(req: DesktopDeliveryRequest): Promise<DesktopDeliveryResult>
}

export function createDesktopDelivery(
  api: DesktopHostApi,
  options: DesktopDeliveryOptions = {},
): DesktopDelivery {
  const caps = desktopCapabilities(api)
  const session = api.session ?? {}
  const now = options.now ?? (() => new Date())

  function stamp(req: DesktopDeliveryRequest): string {
    const given = text(req.receivedAt)
    return RFC3339.test(given) ? given : now().toISOString()
  }

  function projectNumber(req: DesktopDeliveryRequest): number | null {
    const raw = text(req.projectId)
    if (!/^[1-9]\d*$/.test(raw)) return null
    return Number(raw)
  }

  async function deliver(req: DesktopDeliveryRequest): Promise<DesktopDeliveryResult> {
    if (!caps.append) return { status: 'skip', reason: 'host-lacks-appendMessage' }
    const sessionId = text(req.sessionId)
    if (!sessionId) return { status: 'skip', reason: 'no-target-session' }
    const problem = payloadProblem(req)
    if (problem) return { status: 'skip', reason: problem }

    const messageId = destinationMessageId(req)
    const payload = {
      sessionId,
      message: { id: messageId, role: 'user', content: req.text, createdAt: stamp(req) },
    }
    let answer: unknown
    try {
      answer = await session.appendMessage!(payload)
    } catch (error) {
      const refusal = thrownRefusal(error)
      return { status: 'skip', reason: `appendMessage-refused:${refusal.code}` }
    }
    const refusal = refusalOf(answer)
    if (refusal) return { status: 'skip', reason: `appendMessage-refused:${refusal.code}` }
    return {
      status: 'accepted',
      kind: 'append',
      sessionId,
      messageId: idOf(answer) || messageId,
    }
  }

  async function importSession(req: DesktopDeliveryRequest): Promise<DesktopDeliveryResult> {
    const at = stamp(req)
    const externalId = importExternalId(req)
    const project = projectNumber(req)
    const base: Record<string, unknown> = {
      source: DESKTOP_IMPORT_SOURCE,
      sourceLabel: DESKTOP_IMPORT_SOURCE_LABEL,
      externalId,
      title: sessionTitle(req),
      createdAt: at,
      updatedAt: at,
      // The import carries the mail: one call both allocates the thread's session and
      // delivers the first message, so there is no window where an empty session exists.
      messages: [{ role: 'user', content: req.text, createdAt: at }],
    }

    const call = async (payload: Record<string, unknown>): Promise<unknown> => session.import!(payload)
    let answer: unknown
    let projectDropped = false
    try {
      answer = await call(project === null ? base : { ...base, projectId: project })
    } catch (error) {
      const refusal = thrownRefusal(error)
      // A space that no longer exists must not cost us the mail: the binding is metadata.
      if (refusal.code !== 'NOT_FOUND' || project === null) {
        return { status: 'skip', reason: `import-refused:${refusal.code}` }
      }
      projectDropped = true
      try {
        answer = await call(base)
      } catch (retryError) {
        return { status: 'skip', reason: `import-refused:${thrownRefusal(retryError).code}` }
      }
    }

    const refusal = refusalOf(answer)
    if (refusal) return { status: 'skip', reason: `import-refused:${refusal.code}` }

    const sessionId = idOf(answer)
    if (!sessionId) return { status: 'skip', reason: 'import-returned-no-session' }
    const accepted = {
      status: 'accepted' as const,
      sessionId,
      ...(projectDropped ? { projectDropped } : {}),
    }
    const v = (answer ?? {}) as Record<string, unknown>
    if (v.imported === true) return { ...accepted, kind: 'import' }
    // `skipped` means this exact mail was already imported by an earlier attempt whose
    // answer we lost — the mail is in that session, so the retry succeeds.
    if (v.skipped === true) return { ...accepted, kind: 'import-existing' }
    // An older host that does not report either flag: prove the delivery by appending
    // (idempotent on the message id) rather than assuming the import carried the mail.
    const appended = await deliver({ ...req, sessionId })
    if (appended.status !== 'accepted') return appended
    return {
      ...accepted,
      kind: 'import',
      ...(appended.messageId ? { messageId: appended.messageId } : {}),
    }
  }

  return {
    capabilities: caps,

    append: deliver,

    async create(req) {
      const problem = payloadProblem(req)
      if (problem) return { status: 'skip', reason: problem }
      if (caps.create) {
        const project = projectNumber(req)
        let answer: unknown
        try {
          answer = await session.create!({
            ...(project === null ? {} : { projectId: project }),
            title: sessionTitle(req),
          })
        } catch (error) {
          return { status: 'skip', reason: `create-refused:${thrownRefusal(error).code}` }
        }
        const refusal = refusalOf(answer)
        if (refusal) return { status: 'skip', reason: `create-refused:${refusal.code}` }
        const created = idOf(answer)
        if (!created) return { status: 'skip', reason: 'create-returned-no-session' }
        const delivered = await deliver({ ...req, sessionId: created })
        return delivered.status === 'accepted'
          ? { ...delivered, kind: 'create+append', sessionId: created }
          : delivered
      }
      if (!caps.importCreate) return { status: 'skip', reason: 'host-lacks-session-create' }
      if (!caps.append) return { status: 'skip', reason: 'host-lacks-appendMessage' }
      return importSession(req)
    },
  }
}
