/**
 * dsh-aimail — AIMail plugin for dsh (single self-contained bundle).
 *
 * Bundle rows (cordis.patch.yml):
 *   dsh-aimail/mail-service  → ctx.mail (binding resolution)
 *   dsh-aimail/tools         → shared 15 AIMail tools (dsh `aimail_address` override)
 *   dsh-aimail/mail-hub      → SSE pull hub (one system stream or per-address streams)
 *
 * No HTTP inbound listener / webhook / HMAC in the dsh path.
 */
export * from '../core/index.js'
export * from '../mail/index.js'
export { type MailService } from './mail-service.js'
export { runAddressTool } from './address.js'
export { MailHub } from './mail-hub.js'
