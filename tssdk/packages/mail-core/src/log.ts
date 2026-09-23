/**
 * Per-agent agentmail.log helpers — shared by preprocess (inbound/ping) and
 * tools (outbound). Mirrors Python `_log_aimail` / `aimail_log_path`:
 *   {AIMAIL_HOME}/systems/{sid}/{cleanAddr(email)}/agentmail.log (三层收口
 *   2026-09-23, 顶层不再有 logs/), one JSON line per
 * entry, keys: ts, dir, from, to, subj, email_id (optional), ping_id (ping).
 *
 * Extracted into its own module so tools.ts can log outbound lines without
 * importing preprocess.ts (which already imports tools.js — a reverse import
 * would create a cycle). Append failures are non-fatal by contract.
 */
import { promises as fsp } from 'node:fs'
import * as path from 'node:path'
import { AIMAIL_HOME, cleanAddr, systemIdForEmail } from './config.js'

export function aimailLogPath(email: string): string {
  const sid = systemIdForEmail(email) || '_unassigned'
  return path.join(AIMAIL_HOME(), 'systems', sid, cleanAddr(email), 'agentmail.log')
}

async function appendLog(email: string, entry: Record<string, unknown>): Promise<void> {
  try {
    const p = aimailLogPath(email)
    await fsp.mkdir(path.dirname(p), { recursive: true })
    await fsp.appendFile(p, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n', 'utf-8')
  } catch {
    /* non-fatal */
  }
}

/** Outbound log line (mirror Python `_log_aimail("outbound", ...)` — the
 * success branch only; welcome CLI polls this file for reply detection). */
export async function logAimailOutbound(
  email: string,
  from: string,
  to: string,
  subject: string,
  emailId: string,
): Promise<void> {
  await appendLog(email, { dir: 'outbound', from, to, subj: subject, email_id: emailId })
}

/** Inbound/ping log lines (preprocess.ts) share this sink. */
export { appendLog }
