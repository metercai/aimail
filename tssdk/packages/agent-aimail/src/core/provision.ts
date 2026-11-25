/**
 * dsh self-provisioning — the no-CLI closed loop.
 *
 * The dsh plugin owns its environment: it redeems a one-time system/address
 * activation code, writes the system gateway config, mints the least-privilege
 * keys it needs, and registers the default worker address. No `aimail` CLI is
 * involved.
 *
 * Contract notes:
 * - `aimail_gateway.json` is normally CLI-owned. The dsh self-provisioning
 *   path is an audited exception (see the `file-ownership-allowed` marker at
 *   the write site); the CLI remains read-only and can still read the file.
 * - Raw system keys are never returned to the model and are removed after the
 *   per-domain keys are verified.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'

import { GatewayClient } from './gateway.js'
import { AIMAIL_HOME, gatewayConfigPath } from './config.js'
import { POINTER_FILE } from './contract.js'

export interface ActivationPreflight {
  valid: boolean
  code_type: 'address' | 'product' | string
  scope: 'system' | 'shared' | string
  system_id?: string | undefined
  domain?: string | undefined
  system_name?: string | undefined
  email_address?: string | undefined
  manager_address?: string | undefined
  expires_at?: string | undefined
  claimed?: boolean
  is_shipped?: boolean | undefined
  error?: string | undefined
}

export interface ActivatedSystem {
  success: true
  system_id: string
  domain?: string | undefined
  system_name?: string | undefined
  manager_address?: string | undefined
  domains: string[]
  raw_key: string
  kind: string
}

export interface ActivatedAddress {
  success: true
  system_id: string
  email_address: string
  manager_address?: string | undefined
  raw_key: string
  kind: string
  expires_at?: string | undefined
}

export interface SystemGatewayConfig {
  gateway_url: string
  admin_key: string
  system_id: string
  system_name?: string | undefined
  domain?: string | undefined
  manager_address?: string | undefined
  save_raw_snapshots?: boolean
  system_home?: string
  domains?: string[]
  domain_keys?: Record<string, string>
  [k: string]: unknown
}

export class ProvisionError extends Error {
  readonly errorCode: string
  readonly retryable: boolean
  constructor(errorCode: string, message: string, retryable = false) {
    super(message)
    this.name = 'ProvisionError'
    this.errorCode = errorCode
    this.retryable = retryable
  }
}

function asRecord(v: unknown): Record<string, unknown> {
  return typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {}
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

/** Unsigned public POST (activation code is the credential). */
async function publicPost(
  gatewayUrl: string,
  pathname: string,
  body: Record<string, unknown>,
  headers?: Record<string, string>,
): Promise<Record<string, unknown>> {
  const client = new GatewayClient(gatewayUrl.replace(/\/+$/, ''), '', 30_000)
  return asRecord(await client.request('POST', pathname, body, headers))
}

async function signedRequest(
  gatewayUrl: string,
  apiKey: string,
  identity: string,
  method: string,
  pathname: string,
  body?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const client = new GatewayClient(gatewayUrl.replace(/\/+$/, ''), apiKey, 30_000, identity)
  return asRecord(await client.request(method, pathname, body))
}

export async function preflightActivationCode(
  gatewayUrl: string,
  code: string,
): Promise<ActivationPreflight> {
  const res = await publicPost(gatewayUrl, '/api/v1/public/activation-code/preflight', { code })
  const status = Number(res.status ?? 0)
  if (status !== 200) {
    throw new ProvisionError(
      status === 0 ? 'gateway_unreachable' : 'activation_invalid',
      str(res.error) || `preflight failed (HTTP ${status})`,
      status === 0,
    )
  }
  return {
    valid: res.valid !== false,
    code_type: str(res.code_type),
    scope: str(res.scope),
    system_id: str(res.system_id) || undefined,
    domain: str(res.domain) || undefined,
    system_name: str(res.system_name) || undefined,
    email_address: str(res.email_address) || undefined,
    manager_address: str(res.manager_address) || undefined,
    expires_at: str(res.expires_at) || undefined,
    claimed: Boolean(res.claimed),
    is_shipped: res.is_shipped === undefined ? undefined : Boolean(res.is_shipped),
    error: str(res.error) || undefined,
  }
}

export async function activateAddressCode(opts: {
  gatewayUrl: string
  code: string
  email: string
  agentSystemId?: string
  /** sha256(email + anchor) — the anchor relation registered with the gateway (contract §7.1). */
  anchorId?: string
}): Promise<ActivatedAddress> {
  const anchor = String(opts.anchorId ?? '').trim()
  const res = await publicPost(
    opts.gatewayUrl,
    '/api/v1/activate-address-code',
    {
      code: opts.code,
      email_address: opts.email.trim().toLowerCase(),
      ...(anchor ? { anchor_id: anchor } : {}),
    },
    opts.agentSystemId ? { 'X-Agent-System-Id': opts.agentSystemId } : undefined,
  )
  const status = Number(res.status ?? 0)
  const raw = str(res.raw_key)
  const sid = str(res.system_id)
  if (status !== 200 || !raw || !sid) {
    throw new ProvisionError(
      status === 0 ? 'gateway_unreachable' : status === 403 ? 'activation_invalid' : 'activation_failed',
      str(res.error) || str(res.detail) || `activate-address-code failed (HTTP ${status})`,
      status === 0,
    )
  }
  return {
    success: true,
    system_id: sid,
    email_address: str(res.email_address) || opts.email.toLowerCase(),
    manager_address: str(res.manager_address) || undefined,
    raw_key: raw,
    kind: str(res.kind) || 'new',
    expires_at: str(res.expires_at) || undefined,
  }
}

export async function listSystemDomains(opts: {
  gatewayUrl: string
  adminKey: string
  identity: string
  systemId: string
}): Promise<string[]> {
  const res = await signedRequest(
    opts.gatewayUrl,
    opts.adminKey,
    opts.identity,
    'GET',
    `/api/v1/admin/systems/${encodeURIComponent(opts.systemId)}/domains`,
  )
  const status = Number(res.status ?? 0)
  if (status !== 200) return []
  const raw = (res as { data?: unknown }).data
  const list = Array.isArray(raw) ? raw : []
  return list.map((d) => str(asRecord(d).domain)).filter((d) => d.length > 0 && !d.includes('@'))
}

/** Register an address in pull mode (no webhook) and mint its agent key. */
export async function readSystemGatewayConfig(systemId: string): Promise<SystemGatewayConfig | undefined> {
  try {
    const raw = await fs.readFile(gatewayConfigPath(systemId), 'utf-8')
    return JSON.parse(raw) as SystemGatewayConfig
  } catch {
    return undefined
  }
}

export function aimailHome(): string {
  return AIMAIL_HOME()
}

export function systemHome(): string {
  return (process.env.AIMAIL_SYSTEM_HOME || process.env.DSH_HOME || path.join(os.homedir(), '.dsh')).replace(/\/+$/, '')
}

export async function readAgentmailPointer(): Promise<{ system_id?: string; email?: string } | undefined> {
  try {
    return JSON.parse(await fs.readFile(path.join(systemHome(), POINTER_FILE), 'utf-8'))
  } catch {
    return undefined
  }
}

/** Resolve the default worker address for an activated system. */
export const PROVIDER_NAME = 'dsh-self-provisioning'
