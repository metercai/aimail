/**
 * Host-neutral runtime contracts for the SSE AIMail architecture.
 *
 * These types are deliberately free of dsh/pi/openclaw imports. Host adapters
 * implement the primitives; the shared hub/activation/delivery runtime depends
 * only on this surface.
 */
export interface WorkspaceRef {
  /** Stable host workspace id (may be empty when only a path exists). */
  id: string
  /** Canonical absolute workspace path; the durable routing key. */
  path: string
  title?: string
}

export interface SessionRef {
  id: string
  workspace?: WorkspaceRef
}

export interface MessageLike {
  content: Array<{ type: 'text'; text: string }>
  source?: { kind: 'user' }
}

export interface SessionRuntime {
  get(sessionId: string): SessionRef | undefined
  create(opts: {
    sessionId: string
    workspace: WorkspaceRef
    agentOptions?: unknown
    meta?: Record<string, unknown>
  }): Promise<SessionRef>
  resume?(sessionId: string, agentOptions?: unknown): Promise<SessionRef>
  followup(session: SessionRef, message: MessageLike): void
  whenIdle?(session: SessionRef): Promise<void>
  dispose?(session: SessionRef): Promise<void>
}

export interface WorkspaceRuntime {
  default(): Promise<WorkspaceRef | undefined>
  ensure(path: string): Promise<WorkspaceRef>
  /** Resolve the workspace that owns a live session, when the host exposes it. */
  resolveForSession?(sessionId: string): Promise<WorkspaceRef | undefined>
}

export interface HostAdapter {
  readonly id: string
  home(): string
  workspace: WorkspaceRuntime
  sessions: SessionRuntime
  /** Host-specific tool registration is supplied by the host entry. */
  tools?: {
    register(definition: unknown): void
  }
}

export interface DeliveryBinding {
  email: string
  system_id: string
  workspace: WorkspaceRef
  paused?: boolean
}

export interface DeliveryTarget {
  kind: 'followup' | 'new'
  sessionId?: string
  workspace: WorkspaceRef
}

export interface ThreadMetaLike {
  session_id?: string
  workspace_path?: string
}
