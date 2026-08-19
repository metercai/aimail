/**
 * dsh-aimail — AgentMail plugin for dsh (single self-contained bundle).
 *
 * The bundle patch (cordis.patch.yml) self-mounts three subpath entries:
 *   dsh-aimail/mail-service  → ctx.mail (config resolution binding)
 *   dsh-aimail/tools         → 12 AgentMail bare tools
 *   dsh-aimail/inbound       → node:http inbound endpoint + delivery
 *
 * Installed via: dsh plugin --profile web add dsh-aimail
 * This entry re-exports the shared core API for programmatic use.
 */
export * from '@aimail/mail-core'
export * from '@aimail/mail'
export { type MailService } from './mail-service.js'
