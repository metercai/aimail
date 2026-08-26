/**
 * Board tools (A2A) — mirror agentmail_board.py. Dual-credential auth:
 * board token (board_creds.json) when present, else agent api_key; member
 * email passed as query param on every board API call.
 */
import { promises as fsp } from 'node:fs'
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

/** Extract board_id from task_id (t_<board>_<id> / board:<board>:<id>). */
export function resolveBoard(taskId: string): string {
  if (taskId.startsWith('t_')) {
    const parts = taskId.split('_', 2)
    if (parts.length >= 2) return parts[1] ?? ''
  }
  if (taskId.startsWith('board:')) {
    const parts = taskId.split(':', 2)
    if (parts.length >= 2) return parts[1] ?? ''
  }
  return ''
}

/** board_creds.json at systems/{sid}/{cleaned_addr}/board_creds.json. */
function boardCredsPath(systemId: string, email: string): string {
  return path.join(AIMAIL_HOME(), 'systems', systemId, cleanAddr(email), 'board_creds.json')
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
  if (!cfg || !cfg.api_key) throw new Error('agentmail not configured for this agent')
  const creds = await loadBoardCreds(ctx.systemId, cfg.email)
  const token = creds[boardId]?.token
  const client = new GatewayClient(boardGatewayUrl(boardId, cfg), token || cfg.api_key, 30_000, cfg.email)
  return { client, email: cfg.email }
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
  const { client, email } = await boardClient(ctx, args.board)
  return toResult(await client.boardStatus(args.board, email))
}

export interface BoardTaskListArgs {
  board: string
  status?: string
  assignee?: string
}

export async function boardTaskList(ctx: ToolCtx, args: BoardTaskListArgs): Promise<ToolResult> {
  const { client, email } = await boardClient(ctx, args.board)
  return toResult(await client.boardTaskList(args.board, email, args.status ?? '', args.assignee ?? ''))
}

export interface BoardTaskShowArgs {
  task_id: string
}

export async function boardTaskShow(ctx: ToolCtx, args: BoardTaskShowArgs): Promise<ToolResult> {
  const boardId = resolveBoard(args.task_id)
  if (!boardId) return { success: false, error: `cannot resolve board_id from task_id ${args.task_id}` }
  const { client, email } = await boardClient(ctx, boardId)
  return toResult(await client.boardTaskShow(args.task_id, boardId, email))
}

export interface BoardHeartbeatArgs {
  task_id: string
  note?: string
}

export async function boardHeartbeat(ctx: ToolCtx, args: BoardHeartbeatArgs): Promise<ToolResult> {
  const boardId = resolveBoard(args.task_id)
  if (!boardId) return { success: false, error: `cannot resolve board_id from task_id ${args.task_id}` }
  const { client, email } = await boardClient(ctx, boardId)
  return toResult(await client.boardHeartbeat(boardId, args.task_id, email, args.note ?? ''))
}

export interface BoardMembersArgs {
  board: string
  email?: string
}

export async function boardMembers(ctx: ToolCtx, args: BoardMembersArgs): Promise<ToolResult> {
  const { client, email } = await boardClient(ctx, args.board)
  return toResult(await client.boardMembers(args.board, email, args.email ?? ''))
}

export interface SetPublicWhoamiArgs {
  text: string
}

export async function setPublicWhoami(ctx: ToolCtx, args: SetPublicWhoamiArgs): Promise<ToolResult> {
  const cfg = await loadAgentConfig(ctx.systemId, ctx.email ?? '')
  if (!cfg || !cfg.api_key) throw new Error('agentmail not configured for this agent')
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
  const r = await client.agentStatePut('public_whoami', args.text)
  if (r.status >= 200 && r.status < 300) return { success: true, status: 'ok' }
  return { success: false, error: r.error ?? `HTTP ${r.status}` }
}
