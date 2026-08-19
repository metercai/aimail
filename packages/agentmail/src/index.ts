/**
 * agentmail — dsh bundle entry. The bundle patch (cordis.patch.yml) mounts
 * mail / mail-inbound / tool-mail + persona; this entry exposes the shared
 * core API for programmatic use. Installed via:
 *   dsh plugin --profile web add agentmail
 */
export * from '@aimail/mail-core'
