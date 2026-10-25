//! agent-side contract constants — **compile-time embedded single source** on the Rust side.
//!
//! Source of truth = repo root `contract/aimail-contract.json`; `include_str!` embeds it at **compile time** into
//! binary at **compile time**, no longer reading the file at runtime.
//!
//! Why this form (different from the hand-written constants in `pysdk/aimail_contract.py`):
//! - Artifacts are self-contained: pip/bundled forms do not have a repo root `contract/` directory at runtime ⇒ both sides must have "values in
//!   the artifact". The Python side relies on hand-written constants + gate assertions; the Rust side can let the **compiler** do this;
//! - Thus Rust source code has **zero contract literals** ⇒ `tests/contract/check-contract-single-source.py`
//!   literal ratchet rule (b) ("contract literal appearing in new file = new location = violation") is naturally unaffected,
//!   and no need to add Rust consumer entries to that checker (rule (a) only compares py/ts hand-written constants).
//!
//! Usage: Only get values via the accessors below, do not write contract literals elsewhere.

use serde_json::Value;
use std::sync::OnceLock;

/// Compile-time embedded contract manifest (path relative to this file: `cli/src/core/` → repo root).
const MANIFEST_JSON: &str = include_str!("../../../contract/aimail-contract.json");

fn manifest() -> &'static Value {
    static M: OnceLock<Value> = OnceLock::new();
    M.get_or_init(|| {
        serde_json::from_str(MANIFEST_JSON).expect("embedded contract manifest must be valid JSON")
    })
}

fn str_key(key: &str) -> &'static str {
    manifest()
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("contract manifest missing string key: {key}"))
}

/// Fixed inbound paths for the four platforms (openclaw / dsh / pi / deer-flow).
pub fn inbound_path() -> &'static str {
    str_key("inbound_path")
}

/// Inbound path for hermes (gateway's own webhook routing, the only exception).
pub fn hermes_inbound_path() -> &'static str {
    str_key("hermes_inbound_path")
}

/// hermes gateway route name (route key in webhook_subscriptions.json).
pub fn hermes_route_name() -> &'static str {
    str_key("hermes_route_name")
}

/// agent-side skill registration name (host looks up by `/<name>`).
pub fn agent_skill_name() -> &'static str {
    str_key("agent_skill_name")
}

/// agent-side toolset registration name.
pub fn agent_toolset_name() -> &'static str {
    str_key("agent_toolset_name")
}

/// Per-agent binding filename (write permission in SDK; CLI read-only — see [`crate::core::config`]).
pub fn binding_file() -> &'static str {
    str_key("binding_file")
}

/// Platform pointer file name.
pub fn pointer_file() -> &'static str {
    str_key("pointer_file")
}

/// Common default inbound port for hosts whose SDK owns the listener (dsh / pi).
/// On conflict the runtime re-selects an available port (bind probe = source of
/// truth); the confirmed callback pins the real endpoint. Hosts where the host
/// gateway owns the port (hermes / deerflow / openclaw) carry no default here —
/// per-host adaptation discovers the actual port.
pub fn inbound_port() -> u64 {
    manifest()
        .get("inbound_port")
        .and_then(Value::as_u64)
        .expect("contract manifest missing numeric key: inbound_port")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Required keys must be present and be non-empty strings/non-empty objects (deliberately **not** asserting specific literal values:
    /// values come from the manifest itself; hardcoding them in tests would create a second source of truth).
    #[test]
    fn manifest_keys_exist_and_are_usable() {
        for (name, v) in [
            ("inbound_path", inbound_path()),
            ("hermes_inbound_path", hermes_inbound_path()),
            ("hermes_route_name", hermes_route_name()),
            ("agent_skill_name", agent_skill_name()),
            ("agent_toolset_name", agent_toolset_name()),
            ("binding_file", binding_file()),
            ("pointer_file", pointer_file()),
        ] {
            assert!(!v.trim().is_empty(), "{name} must be non-empty");
        }
        let port = inbound_port();
        assert!(
            port > 0 && port < 65536,
            "inbound port out of range: {port}"
        );
    }

    #[test]
    fn manifest_is_a_single_compiled_copy() {
        // Embedded value == reading the same file directly from disk (source taken at compile time ⇒ the two can only be equal;
        // the value of this assertion is "the compile-time actually took the one at the repo root", immediately red if the path is wrong).
        let on_disk = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../contract/aimail-contract.json"),
        )
        .expect("repo-root contract manifest readable from cli/");
        assert_eq!(
            on_disk, MANIFEST_JSON,
            "embedded copy drifted from repo manifest"
        );
    }
}
