/**
 * Board tools (A2A) — mirror aimail_board.py. Dual-credential auth:
 * board token (board_creds.json) when present, else agent api_key; member
 * email passed as query param on every board API call.
 *
 * B1 (2026-11-03): the agent-facing board handle is the board's email
 * address. The internal 20-hex board_id is derived locally
 * (`sha256(lower(address))[:20]`, identical to the gateway's
 * `derive_board_id`) and never appears in tool arguments. The 20-hex id
 * still keys board_creds.json and fills the gateway URL path — zero
 * gateway change.
 */
import { promises as fsp } from 'node:fs'
import { createHash } from 'node:crypto'
import * as path from 'node:path'
import { GatewayClient } from './gateway.js'
import { AIMAIL_HOME, cleanAddr, loadAgentConfig } from './config.js'
import type { AgentConfig } from './types.js'
import type { ToolCtx, ToolResult } from './tools.js'

// ── board registry (mirror Python _board_gateways, extracted from .a2a@ mail) ──

const boardGateways = new Map<string, string>()

/** Register board gateway URL (called by inbound preprocess on .a2a@ mail). */
export function registerBoardGateway(boardId: string, gatewayUrl: string): void {
  if (boardId && gatewayUrl) boardGateways.set(boardId, gatewayUrl)
}

export function boardGatewayUrl(boardId: string, cfg: AgentConfig): string {
  return boardGateways.get(boardId) || cfg.gateway_url
}

/**
 * Internal board id from the board's email address — `sha256(lower(addr))[:20]`,
 * byte-identical to the gateway's `derive_board_id` (board/models.rs).
 * Pure and deterministic; the same address always yields the same id.
 */
export function deriveBoardIdFromAddress(address: string): string {
  const addr = address.trim().toLowerCase()
  if (!addr) return ''
  return createHash('sha256').update(addr, 'utf-8').digest('hex').slice(0, 20)
}

/**
 * Validate an agent-supplied board handle. Accepts a board address
 * (`.a2a@domain`); anything else is rejected up front (a 20-hex id or a
 * task id passed here would silently target a nonexistent board before).
 */
export function normalizeBoardHandle(handle: string): { ok: true; boardId: string } | { ok: false; error: string } {
  const h = (handle ?? '').trim()
  if (!h) return { ok: false, error: 'board (the board\'s .a2a@ address) is required' }
  if (/[0-9a-fA-F]{20}/.test(h) || h.startsWith('t_') || h.startsWith('b_')) {
    return { ok: false, error: `board must be the board's .a2a@ address (got "${h}"; pass the address shown in the board invitation, not a board id or task id)` }
  }
  if (!/\.a2a@/i.test(h)) {
    return { ok: false, error: `board must be a board address like xk9mp2q.a2a@mail.example.com (got "${h}")` }
  }
  return { ok: true, boardId: deriveBoardIdFromAddress(h) }
}

/** board_creds.json at systems/{sid}/{cleaned_addr}/board_creds.json. */
function boardCredsPath(systemId: string, email: string): string {
  return path.join(AIMAIL_HOME(), 'systems', systemId, cleanAddr(email), 'board_creds.json')
}

/**
 * Fields of a board invite mail.
 *
 * The board address in the From header is the single board handle (B1):
 * the 20-hex id is derived locally from it (same algorithm as the
 * gateway's `derive_board_id`), so the `Board ID:` line the gateway used
 * to print is no longer read. Keying board_creds.json with the derived id
 * matches the gateway's storage layout exactly.
 */
export function boardInviteFrom(
  body: string,
  fromRaw: string,
): { boardId: string; boardEmail: string; gatewayUrl: string; token: string } | null {
  const gw = /API:\s*(https?:\/\/\S+)/.exec(body)
  if (!gw?.[1]) return null
  const gatewayUrl = gw[1].replace(/\/+$/, '')
  const token = (/Token:\s*(bdt_\S+)/.exec(body)?.[1] ?? '').trim()
  const addr = /([A-Za-z0-9._-]+\.a2a@[A-Za-z0-9._-]+)/.exec(fromRaw)?.[1] ?? ''
  const boardEmail = addr.toLowerCase()
  const boardId = deriveBoardIdFromAddress(boardEmail)
  if (!boardId) return null
  return { boardId, boardEmail, gatewayUrl, token }
}

interface BoardCredsFile {
  [boardId: string]: { token?: string; [k: string]: unknown }
}

async function loadBoardCreds(systemId: string, email: string): Promise<BoardCredsFile> {
  try {
    return JSON.parse(await fsp.readFile(boardCredsPath(systemId, email), 'utf-8')) as BoardCredsFile
  } catch {
    return {}
  }
}

async function boardClient(
  ctx: ToolCtx,
  boardId: string,
): Promise<{ client: GatewayClient; email: string }> {
  const cfg = await loadAgentConfig(ctx.systemId, ctx.email ?? '')
  if (!cfg || !cfg.api_key) throw new Error('aimail not configured for this agent')
  const creds = await loadBoardCreds(ctx.systemId, cfg.email)
  const token = creds[boardId]?.token
  const client = new GatewayClient(boardGatewayUrl(boardId, cfg), token || cfg.api_key, 30_000, cfg.email)
  return { client, email: cfg.email }
}

/** Resolve the agent-supplied board handle into a client, or a ToolResult error. */
async function boardClientOrError(
  ctx: ToolCtx,
  handle: string,
): Promise<{ client: GatewayClient; boardId: string; email: string } | { error: ToolResult }> {
  const n = normalizeBoardHandle(handle)
  if (!n.ok) return { error: { success: false, error: n.error } }
  const { client, email } = await boardClient(ctx, n.boardId)
  return { client, boardId: n.boardId, email }
}

function toResult(r: Awaited<ReturnType<GatewayClient['boardStatus']>>): ToolResult {
  if (r.status >= 200 && r.status < 300) return { success: true, ...r }
  return { success: false, error: r.error ?? `HTTP ${r.status}` }
}

// ── board tools ────────────────────────────────────────────────

export interface BoardStatusArgs {
  board: string
}

export async function boardStatus(ctx: ToolCtx, args: BoardStatusArgs): Promise<ToolResult> {
  const r = await boardClientOrError(ctx, args.board)
  if ('error' in r) return r.error
  return toResult(await r.client.boardStatus(r.boardId, r.email))
}

export interface BoardTaskListArgs {
  board: string
  status?: string
  assignee?: string
}

export async function boardTaskList(ctx: ToolCtx, args: BoardTaskListArgs): Promise<ToolResult> {
  const r = await boardClientOrError(ctx, args.board)
  if ('error' in r) return r.error
  return toResult(await r.client.boardTaskList(r.boardId, r.email, args.status ?? '', args.assignee ?? ''))
}

export interface BoardTaskShowArgs {
  task_id: string
  board: string
}

export async function boardTaskShow(ctx: ToolCtx, args: BoardTaskShowArgs): Promise<ToolResult> {
  const r = await boardClientOrError(ctx, args.board)
  if ('error' in r) return r.error
  return toResult(await r.client.boardTaskShow(args.task_id, r.boardId, r.email))
}

export interface BoardHeartbeatArgs {
  task_id: string
  board: string
  note?: string
}

export async function boardHeartbeat(ctx: ToolCtx, args: BoardHeartbeatArgs): Promise<ToolResult> {
  const r = await boardClientOrError(ctx, args.board)
  if ('error' in r) return r.error
  return toResult(await r.client.boardHeartbeat(r.boardId, args.task_id, r.email, args.note ?? ''))
}

export interface BoardMembersArgs {
  board: string
  email?: string
}

export async function boardMembers(ctx: ToolCtx, args: BoardMembersArgs): Promise<ToolResult> {
  const r = await boardClientOrError(ctx, args.board)
  if ('error' in r) return r.error
  return toResult(await r.client.boardMembers(r.boardId, r.email, args.email ?? ''))
}

export interface BoardRolesArgs {
  board: string
  role?: string
}

export async function boardRoles(ctx: ToolCtx, args: BoardRolesArgs): Promise<ToolResult> {
  const r = await boardClientOrError(ctx, args.board)
  if ('error' in r) return r.error
  return toResult(await r.client.boardRoles(r.boardId, r.email, args.role ?? ''))
}
