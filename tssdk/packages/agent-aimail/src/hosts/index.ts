// agent-aimail `.` entry — host identification + core API surface.
//
// This is what `import 'agent-aimail'` resolves to. It does NOT activate any
// host (the per-host entries do, via their own loaders). It exposes:
//   - the host registry + detection (for diagnostics / meta-tools)
//   - the host-agnostic core API (re-exported), so a direct import gets the
//     same surface as `import ... from 'agent-aimail/core'`.

export { HOSTS, HOST_KEYS, type HostSpec } from './registry.js'
export { detectHost, detectHostsPresent } from './detect.js'
export { activateHost, type ActivationResult } from './activate.js'

// Core API re-export (same surface as 'agent-aimail/core').
export * from '../core/index.js'
