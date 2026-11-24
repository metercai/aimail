/**
 * `agent-aimail/desktop` — the PI-Desktop (multi-address host) face.
 *
 * Two layers, deliberately separate:
 *   delivery  — how a mail reaches one session on this host (payload shapes the
 *               host validates, deterministic ids, capability probing);
 *   inbox     — which session a mail belongs to, and "never ack a mail that was
 *               not delivered".
 *
 * Neither imports `pi`: the plugin entry supplies it, which is what makes both
 * testable without an Electron app.
 */
export {
  createDesktopDelivery,
  desktopCapabilities,
  DESKTOP_DELIVERY_LIMITS,
  DESKTOP_IMPORT_SOURCE,
  DESKTOP_IMPORT_SOURCE_LABEL,
  destinationMessageId,
  importExternalId,
  sessionTitle,
} from './delivery.js'
export type {
  DesktopCapabilities,
  DesktopDelivery,
  DesktopDeliveryKind,
  DesktopDeliveryOptions,
  DesktopDeliveryRequest,
  DesktopDeliveryResult,
  DesktopHostApi,
} from './delivery.js'
export {
  createDesktopInbox,
  mailMessageId,
  mailText,
  mailThreadRoot,
  resolveDesktopConfig,
  selectTargetSession,
} from './inbox.js'
export type {
  DesktopConfigResult,
  DesktopInbox,
  DesktopInboxDeps,
  DesktopInboxHost,
  DesktopInboxOutcome,
  DesktopInboxSettings,
  DesktopSessionItem,
} from './inbox.js'
