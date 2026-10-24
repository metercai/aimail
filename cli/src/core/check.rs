//! Record model and output of the check engine (replica of `cli/check_status.py:131-171`).
//!
//! Shape relied upon by downstream (`repair`, `--json` consumers):
//!
//! ```text
//! record = {"level","check","pass","detail","fix"}   ← exact key set, pass must be bool
//! JSON = {"all_pass": bool, "timestamp": ISO8601Z, "checks": [records...]}
//! ```
//!
//! Text output is rendered in **fixed group order** (see [`GROUPS`] for group table); empty groups are not printed;
//! Failed items append a `→ fix` line under `--verbose`.
//!
//! Discipline: this module only provides "record assembly + rendering". Each layer's probes hook in at their respective steps;
//! **The `check` subcommand remains not-ported until all of L0–L4 are implemented** (half a check =
//! missing level ⇒ false green, the most dangerous failure mode in safety-critical commands).

use crate::core::style::{BROWN, CHECK, CROSS, GREEN, NC, RED, YELLOW};
use crate::core::time;
use serde_json::{json, Map, Value};

/// `(level, group title)` —— order is the grouping order for text output (`check_status.py:146-155`).
pub const GROUPS: &[(&str, &str)] = &[
    ("system", "system (check scope)"),
    ("config", "config (config files: gateway/bridge/agent)"),
    ("gateway", "aimail-gateway (external mail gateway)"),
    ("bridge", "aimail-bridge (local NAT traversal bridge)"),
    (
        "runtime",
        "runtime (platform resources: patches/skills/plugin)",
    ),
    ("agent", "agent (platform config + hook interface)"),
    ("agent-gw", "agent-gateway (Hermes gateway)"),
    ("profile", "agent-profile (agent entity)"),
];

/// A single check record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub level: String,
    pub check: String,
    pub pass: bool,
    pub detail: String,
    pub fix: String,
}

/// Check collection.
#[derive(Debug, Default, Clone)]
pub struct Check {
    pub checks: Vec<Record>,
    pub verbose: bool,
}

impl Check {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, level: &str, name: &str, pass: bool, detail: &str, fix: &str) {
        self.checks.push(Record {
            level: level.to_string(),
            check: name.to_string(),
            pass,
            detail: detail.to_string(),
            fix: fix.to_string(),
        });
    }

    pub fn all_pass(&self) -> bool {
        self.checks.iter().all(|c| c.pass)
    }

    /// Text view (groups, colors, optional fix lines).
    pub fn print_table(&self) {
        for (level, title) in GROUPS {
            let items: Vec<&Record> = self.checks.iter().filter(|c| c.level == *level).collect();
            if items.is_empty() {
                continue;
            }
            println!("\n  {BROWN}╓─ {title}{NC}");
            for chk in items {
                let icon = if chk.pass {
                    format!("{GREEN}{CHECK}{NC}")
                } else {
                    format!("{RED}{CROSS}{NC}")
                };
                println!("  {icon} {}: {}", chk.check, chk.detail);
                if self.verbose && !chk.pass && !chk.fix.is_empty() {
                    println!("     {YELLOW}→{NC} {}", chk.fix);
                }
            }
        }
    }

    /// JSON view (`all_pass` / `timestamp` / `checks`, 2-space indent, non-ASCII not escaped).
    pub fn print_json(&self) {
        println!("{}", self.json_string());
    }

    /// JSON text for testing and reuse.
    pub fn json_string(&self) -> String {
        let mut root = Map::new();
        root.insert("all_pass".into(), Value::Bool(self.all_pass()));
        root.insert("timestamp".into(), Value::String(time::now_iso8601_z()));
        root.insert(
            "checks".into(),
            Value::Array(
                self.checks
                    .iter()
                    .map(|c| {
                        json!({
                            "level": c.level,
                            "check": c.check,
                            "pass": c.pass,
                            "detail": c.detail,
                            "fix": c.fix,
                        })
                    })
                    .collect(),
            ),
        );
        serde_json::to_string_pretty(&Value::Object(root)).expect("serialize check json")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    #[test]
    fn record_shape_matches_the_pinned_contract() {
        // Corresponds to Python tests/test_check_contract.py:test_check_record_shape
        let mut c = Check::new();
        c.add("config", "system_home", true, "/home/.hermes", "");
        c.add("agent", "hook", false, "404", "reinstall plugin");
        assert_eq!(c.checks.len(), 2);
        let rec = &c.checks[0];
        assert!(rec.pass);
        assert!(!c.all_pass(), "second record failed");
    }

    #[test]
    fn json_view_has_the_three_root_keys_and_record_keys() {
        // Corresponds to test_check_json_output
        let mut c = Check::new();
        c.add("bridge", "process", false, "not running", "start_bridge");
        let data: Value = serde_json::from_str(&c.json_string()).expect("valid json");
        assert_eq!(data.get("all_pass").and_then(Value::as_bool), Some(false));
        assert!(data.get("timestamp").and_then(Value::as_str).is_some());
        let rec = &data["checks"][0];
        assert_eq!(rec.get("level").and_then(Value::as_str), Some("bridge"));
        assert_eq!(rec.get("check").and_then(Value::as_str), Some("process"));
        assert_eq!(rec.get("pass").and_then(Value::as_bool), Some(false));
        assert_eq!(rec.get("fix").and_then(Value::as_str), Some("start_bridge"));
        // Exact key set (one extra key breaks downstream)
        let keys: Vec<&str> = rec
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(keys, vec!["level", "check", "pass", "detail", "fix"]);
    }

    #[test]
    fn every_production_level_has_a_group() {
        // Corresponds to test_known_levels_printable: all levels used in production must be in the group table
        for lvl in [
            "system", "config", "gateway", "bridge", "runtime", "agent", "agent-gw", "profile",
        ] {
            assert!(
                GROUPS.iter().any(|(l, _)| *l == lvl),
                "level {lvl} has no group ⇒ text view will miss printing"
            );
        }
    }
}
