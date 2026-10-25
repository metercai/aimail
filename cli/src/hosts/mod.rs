//! Host (agent system) semantic layering.
//!
//! The old structure was a data table + a layer of value view algorithms (two core modules, retired on 2026-10-18);
//! Now split into six surfaces in this directory by functional semantics:
//!
//! | Surface | Module | Responsibility |
//! |----|------|------|
//! | ① Identity | [`identity`] | Product type / Language type / home location / Identification rules / Pointer shape |
//! | ② Runtime | `runtime` (P3) | Parser / Package manager / SDK root (only subprocess surface, lazy) |
//! | ③ Execution | [`exec`] | Registration / Full registration / Install steps / Uninstall steps / op entry |
//! | ④ Health | [`health`] | Health check / Payload reference / Remote self-proof |
//! | ⑤ Pointer | `pointer` (P6) | Pointer path / Owning sid / Scanning |
//! | ⑥ Directory | This module | Enumeration order / Get host by id / `spec --json` export |
//!
//! The only external contract is [`Host`]: callers only get hosts via [`get`] / [`all`], not touching any data table.
//! Adding a new agent type = adding `hosts/<name>.rs` + registering a line in [`ALL`], engine unchanged.

pub mod exec;
pub mod health;
pub mod identity;
pub mod pointer;
pub mod runtime;

mod deerflow;
mod dsh;
mod hermes;
mod openclaw;
mod pi;

use serde::Serialize;
use serde_json::{json, Map, Value};

use identity::{Detect, PointerKind};

/// Implementation language of the agent system. Derived from the kind in [`Host::register`] (single mapping point).
pub use identity::Lang;

/// The full capability surface of a host system.
///
/// Semantic boundary: identity/pointer is a read-only filesystem; the runtime (P3) is the only surface that spawns child processes;
/// execution/health-check only provides data; the engine that actually runs steps is in `core::steps` / `core::checks::l2r`.
pub trait Host: Sync {
    // ── ① Identity ──
    fn id(&self) -> &'static str;
    fn home_dir(&self) -> &'static str;
    fn detect(&self) -> Detect;
    fn pointer_kind(&self) -> PointerKind;

    /// Language type: the single derivation point (node_entry ⇒ Node, others ⇒ Python).
    fn lang(&self) -> Lang {
        match self.register() {
            exec::Register::NodeEntry { .. } => Lang::Node,
            _ => Lang::Python,
        }
    }

    /// Whether a python interpreter is required (node hosts do not need one: their steps do not reference `{python}`).
    fn uses_python(&self) -> bool {
        matches!(self.lang(), Lang::Python)
    }

    /// Who owns the `--agent-home` semantics (default: none; hermes is the only platform that uses it for location).
    fn uses_agent_home(&self) -> bool {
        false
    }

    /// Additional paths accepted during loose repair judgment (default: none; hermes historical semantics also accept `profiles/`).
    fn repair_hints(&self) -> &'static [&'static str] {
        &[]
    }

    /// op entry argv template for node host; `None` => python gate (`-m aimail.sdk_ops`).
    fn op_template(&self) -> Option<&'static [&'static str]> {
        match self.lang() {
            Lang::Node => Some(exec::NODE_OP_TEMPLATE),
            Lang::Python => None,
        }
    }

    // ── ③ Execution ──
    fn register(&self) -> exec::Register;
    fn register_all(&self) -> Option<exec::RegisterAll>;
    fn install_plan(&self) -> &'static [exec::Step];
    fn uninstall_plan(&self) -> &'static [exec::Step];

    // ── ④ Health Check ──
    fn health_checks(&self) -> &'static [health::Health];
    fn agent_check_remote(&self) -> bool;
}

/// Platform enumeration order (used for judgment order, scan order, and help grouping).
pub const ORDER: &[&str] = &["pi", "dsh", "hermes", "openclaw", "deerflow"];

/// Registry header comment (the `_comment` in `spec --json`).
pub const COMMENT: &str = "AIMail CLI Platform Host Table — The sole source of platform knowledge on the CLI side (detection/pointer/enumeration/registration delegation/installation steps). All adaptation implementations are in the SDK: pysdk (hermes/deer-flow) + tssdk package (openclaw host commands/dsh/pi node entry). The CLI reads this table for scheduling, with zero platform protocol.";

/// All hosts (order = [`ORDER`]).
pub static ALL: &[&HostDef] = &[
    &pi::PI,
    &dsh::DSH,
    &hermes::HERMES,
    &openclaw::OPENCLAW,
    &deerflow::DEERFLOW,
];

/// All hosts (order = [`ORDER`]).
pub fn all() -> &'static [&'static HostDef] {
    ALL
}

/// Get host by id (non-existent ⇒ None, no guessing).
pub fn get(id: &str) -> Option<&'static HostDef> {
    ALL.iter().copied().find(|h| h.id == id)
}

/// Enumeration order.
pub fn order() -> &'static [&'static str] {
    ORDER
}

/// The **static definition** of a host: the sole content of each `hosts/<name>.rs`.
///
/// The serialization shape is the entry in `platform spec --json` (field declaration order = key order);
/// The two fields with `#[serde(skip)]` are host-private semantics (`--agent-home` ownership, repair loose clues),
/// and do not enter the value view. `impl Host` is written only in this file; host modules only fill in data.
#[derive(Serialize)]
pub struct HostDef {
    #[serde(skip)]
    pub id: &'static str,
    pub detect: Detect,
    pub pointer: PointerKind,
    pub register: exec::Register,
    pub home_dir: &'static str,
    pub install_steps: &'static [exec::Step],
    pub uninstall_steps: &'static [exec::Step],
    pub health_checks: &'static [health::Health],
    #[serde(skip_serializing_if = "Option::is_none")]
    pub register_all: Option<exec::RegisterAll>,
    #[serde(skip_serializing_if = "is_false")]
    pub agent_check_remote: bool,
    #[serde(skip)]
    pub uses_agent_home: bool,
    #[serde(skip)]
    pub repair_hints: &'static [&'static str],
    /// agent key container directory (inside host home): `profiles` (hermes/dsh), `agents` (openclaw),
    /// `""` (single agent host). Only for `platform spec` agent_keys scanning, does not enter the value view.
    #[serde(skip)]
    pub agents_container: &'static str,
}

impl Host for HostDef {
    fn id(&self) -> &'static str {
        self.id
    }
    fn home_dir(&self) -> &'static str {
        self.home_dir
    }
    fn detect(&self) -> Detect {
        self.detect
    }
    fn pointer_kind(&self) -> PointerKind {
        self.pointer
    }
    fn uses_agent_home(&self) -> bool {
        self.uses_agent_home
    }
    fn repair_hints(&self) -> &'static [&'static str] {
        self.repair_hints
    }
    fn register(&self) -> exec::Register {
        self.register
    }
    fn register_all(&self) -> Option<exec::RegisterAll> {
        self.register_all
    }
    fn install_plan(&self) -> &'static [exec::Step] {
        self.install_steps
    }
    fn uninstall_plan(&self) -> &'static [exec::Step] {
        self.uninstall_steps
    }
    fn health_checks(&self) -> &'static [health::Health] {
        self.health_checks
    }
    fn agent_check_remote(&self) -> bool {
        self.agent_check_remote
    }
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// Full registry view (data source for `aimail platform spec --json`).
pub fn describe_all() -> Value {
    let mut platforms = Map::new();
    for h in ALL {
        platforms.insert(
            h.id.to_string(),
            serde_json::to_value(h).expect("host def must serialize"),
        );
    }
    json!({ "_comment": COMMENT, "order": ORDER, "platforms": platforms })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Directory integrity: `all()` corresponds one-to-one with `ORDER` and has consistent order.
    #[test]
    fn catalog_matches_order() {
        let ids: Vec<&str> = ALL.iter().map(|h| h.id()).collect();
        assert_eq!(ids, ORDER.to_vec());
        for id in ORDER {
            assert!(get(id).is_some(), "catalog missing {id}");
        }
        assert!(get("nope").is_none());
    }

    /// Platform registry JSON is deprecated (P2c S3b), must not regress: file does not exist + zero load references in source code.
    #[test]
    fn retired_platforms_json_must_not_return() {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let needle = ["plat", "forms.json"].concat();
        assert!(
            !manifest.join(&needle).exists(),
            "Platform registry JSON is deprecated (true source = hosts/ host modules), do not restore this file"
        );
        let mut offenders: Vec<String> = Vec::new();
        let mut stack = vec![manifest.join("src")];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else {
                continue;
            };
            for e in entries.flatten() {
                let path = e.path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|x| x == "rs") {
                    if let Ok(t) = std::fs::read_to_string(&path) {
                        if t.contains(&needle) {
                            offenders.push(path.display().to_string());
                        }
                    }
                }
            }
        }
        assert!(
            offenders.is_empty(),
            "Platform registry JSON load reference found in source code: {offenders:?}"
        );
    }

    /// Entries are always **SDK root relative**; appearance of `pysdk/` means layout knowledge reflux ⇒ red.
    #[test]
    fn registry_data_has_no_repo_layout_paths() {
        let raw = describe_all().to_string();
        assert!(
            !raw.contains("\"pysdk/"),
            "Repository layout path found in registry data"
        );
        for entry in [
            "hermes/register_profiles.py",
            "deer-flow/manage.py",
            "hermes",
        ] {
            assert!(
                raw.contains(entry),
                "Registry should contain SDK root relative entry {entry}"
            );
        }
    }

    /// Installation steps for each host can be serialized into an object with kind (serde regression pin).
    #[test]
    fn step_tables_serialize_with_kind() {
        for h in ALL {
            for st in h.install_plan().iter().chain(h.uninstall_plan()) {
                let v = serde_json::to_value(st).expect("step serializes");
                assert!(
                    v.get("kind").is_some(),
                    "{} steps missing kind: {v}",
                    h.id()
                );
            }
        }
    }

    /// Language type has only one mapping point: node hosts declare node op templates, python hosts do not.
    #[test]
    fn lang_matches_register_kind_and_op_template() {
        for h in ALL {
            match h.register() {
                exec::Register::NodeEntry { .. } => {
                    assert_eq!(h.lang(), Lang::Node, "{}", h.id());
                    assert!(
                        h.op_template().is_some(),
                        "{} is a node host, must declare op template",
                        h.id()
                    );
                }
                _ => {
                    assert_eq!(h.lang(), Lang::Python, "{}", h.id());
                    assert!(
                        h.op_template().is_none(),
                        "{} is a python host, must not declare node op template",
                        h.id()
                    );
                }
            }
        }
    }
}
