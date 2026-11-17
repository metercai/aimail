/**
 * System activation surface for non-DSH hosts.
 *
 * The implementation currently lives in dsh/system-core because it predates
 * the runtime extraction; this re-export is the stable boundary Pi/OpenClaw
 * consume. The next extraction step moves the body here without changing the
 * adapter imports.
 */
export {
  provisionSystem,
  systemStatus,
  findExistingSystem,
  type Args as SystemArgs,
  type SystemWorkspaceHost,
} from '../../dsh/system-core.js'
