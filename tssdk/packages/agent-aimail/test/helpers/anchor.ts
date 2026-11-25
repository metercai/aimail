/**
 * Test helper: the activation stamp a real binding carries.
 *
 * `binding_source` + `activated_at` are written by `activateAddressCodePersist` and judged by
 * `core/binding-provenance.ts` at arming (an unstamped V2 binding never arms). The ANCHOR relation
 * is not judged locally any more — the gateway holds the anchor identifier and checks it on every
 * pull (see `core/anchor-id.ts`), so fixtures do not need to write any anchor file.
 */

/** Fields the activation stamp needs (spread into a fixture's binding object). */
export const ACTIVATION_STAMP = {
  binding_source: 'activate-address-code',
  activated_at: '2026-11-25T10:00:00.000Z',
} as const
