/**
 * Host capability declaration (contract §11).
 *
 * The shared SDK serves hosts with different capabilities. Only ONE of them changes
 * behaviour here: whether the host may hold several active addresses.
 *
 * `single` hosts must REFUSE a second address before any gateway call — otherwise the
 * second activation silently replaces (or shadows) the first, consuming a one-time code
 * for a mailbox the host can never serve. Default is `multi` so a host that forgets to
 * declare anything keeps its previous behaviour.
 */
import { promises as fsp } from 'node:fs'
import * as path from 'node:path'

import { AIMAIL_HOME } from './config.js'
import { BINDING_FILE } from './contract.js'

export type AddressModel = 'single' | 'multi'

/**
 * Declared model. Two channels on purpose:
 *  - the setter (host entry calls it at load), and
 *  - `AIMAIL_HOST_ADDRESS_MODEL` in the environment.
 *
 * The environment wins because a bundled host can end up with two copies of this module
 * (one reached from the entry, one from the tool graph); in-memory state then splits and
 * the guard silently stops firing — measured on pi L2 s4, where the second bind went
 * straight to the gateway despite a single-address host already holding an address.
 */
let _model: AddressModel | undefined

export function setAddressModel(model: AddressModel | undefined): void {
  _model = model === 'single' ? 'single' : model === 'multi' ? 'multi' : undefined
}

export function getAddressModel(): AddressModel {
  const env = String(process.env.AIMAIL_HOST_ADDRESS_MODEL ?? '').trim().toLowerCase()
  if (env === 'single' || env === 'multi') return env
  return _model ?? 'multi'
}

/**
 * Another ACTIVE address (carries a key, not paused) in this SDK home.
 *
 * Deliberately a pure filesystem scan: it must work before any binding is loaded (the
 * refusal has to happen before the activation request is sent), and it must not depend
 * on the caller's own address.
 */
export async function otherActiveAddress(email: string): Promise<string | undefined> {
  const want = String(email ?? '').trim().toLowerCase()
  const base = path.join(AIMAIL_HOME(), 'systems')
  let roots: string[] = []
  try {
    roots = await fsp.readdir(base)
  } catch {
    return undefined
  }
  for (const root of roots) {
    let entries: import('node:fs').Dirent[] = []
    try {
      entries = await fsp.readdir(path.join(base, root), { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue
      const file = path.join(base, root, entry.name, BINDING_FILE)
      try {
        const cfg = JSON.parse(await fsp.readFile(file, 'utf-8')) as Record<string, unknown>
        const addr = String(cfg.email ?? '').trim().toLowerCase()
        if (!addr || addr === want) continue
        if (!String(cfg.api_key ?? '').trim()) continue
        if (cfg.paused === true) continue
        return addr
      } catch {
        continue
      }
    }
  }
  return undefined
}
