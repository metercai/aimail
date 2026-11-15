/**
 * dsh workspace resolution for the AIMail adapter.
 *
 * A mailbox binds to a workspace (a durable project directory), never to a
 * session id. The session is only used to discover the CURRENT workspace at
 * bind time; system onboarding always uses DSH's default workspace.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'

import type { Context } from '@deepseek-ai/cordis'

export interface WorkspaceRef {
  id: string
  path: string
  title?: string
}

function dshHome(): string {
  return (process.env.DSH_HOME || path.join(process.env.HOME || '', '.dsh')).replace(/\/+$/, '')
}

function profile(): string {
  return process.env.DSH_PROFILE?.trim() || 'web'
}

export async function canonicalPath(p: string): Promise<string> {
  return await fs.realpath(p)
}

/** Current session workspace (session header cwd), canonicalized. */
export async function currentWorkspacePath(ctx: Context, sessionId: string): Promise<string> {
  const sessions = ctx.get('sessions') as
    | { get(id: string): { header?: { cwd?: string } } | undefined }
    | undefined
  const cwd = sessions?.get(sessionId)?.header?.cwd
  if (!cwd) throw new Error('no workspace for this session — open a project workspace first')
  const abs = await canonicalPath(cwd)
  if (!(await fs.stat(abs)).isDirectory()) throw new Error(`workspace is not a directory: ${abs}`)
  return abs
}

/** DSH default workspace (`default-workspace`), from the registry or storage. */
export async function defaultWorkspace(ctx: Context): Promise<WorkspaceRef | undefined> {
  const registry = ctx.get('workspaceRegistry') as
    | { list(): Array<{ id: string; path: string; title?: string }> }
    | undefined
  const list = registry?.list?.() ?? []
  const byPath = list.find((w) => w.path.replace(/\/+$/, '').endsWith('/default-workspace'))
  if (byPath) return { id: byPath.id, path: byPath.path, ...(byPath.title ? { title: byPath.title } : {}) }
  try {
    const raw = await fs.readFile(path.join(dshHome(), 'storages', 'workspace.json'), 'utf-8')
    const obj = JSON.parse(raw) as { global?: { defaultWorkspaceId?: string }; tables?: { workspaces?: Record<string, { path?: string; title?: string }> } }
    const id = obj.global?.defaultWorkspaceId
    const rec = id ? obj.tables?.workspaces?.[id] : undefined
    if (id && rec?.path) return { id, path: rec.path, ...(rec.title ? { title: rec.title } : {}) }
  } catch {
    /* fall through */
  }
  const fallback = list.find((w) => w.title === 'default-workspace')
  return fallback ? { id: fallback.id, path: fallback.path, ...(fallback.title ? { title: fallback.title } : {}) } : undefined
}

/** Ensure a registry entry exists for a path (project selection / default). */
export async function ensureWorkspace(ctx: Context, p: string): Promise<WorkspaceRef> {
  const registry = ctx.get('workspaceRegistry') as
    | {
        list(): Array<{ id: string; path: string; title?: string }>
        create(path: string, title?: string): Promise<{ id: string; path: string; title?: string }>
      }
    | undefined
  const abs = await canonicalPath(p)
  const existing = registry?.list?.().find((w) => w.path.replace(/\/+$/, '') === abs)
  if (existing) return { id: existing.id, path: existing.path, ...(existing.title ? { title: existing.title } : {}) }
  if (registry?.create) {
    const w = await registry.create(abs)
    return { id: w.id, path: w.path, ...(w.title ? { title: w.title } : {}) }
  }
  return { id: '', path: abs }
}

export function statusPath(): string {
  return path.join(dshHome(), 'profiles', profile(), 'aimail', 'status.json')
}
