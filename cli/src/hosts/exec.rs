//! ③ Execution plane: step model + registration entry. Data (step tables for each host) resides in each host module,
//! this module only defines the model and generic execution semantics (the engine that actually runs steps is still `core::steps`).

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// `env` pair → JSON object (serde by default serializes `[(k, v)]` into a nested array; here we preserve the object shape).
#[derive(Clone, Copy)]
pub struct EnvPairs(pub &'static [(&'static str, &'static str)]);

impl EnvPairs {
    fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl Serialize for EnvPairs {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(self.0.len()))?;
        for (k, v) in self.0 {
            m.serialize_entry(k, v)?;
        }
        m.end()
    }
}

/// `when` preconditions (predicate union, AND semantics).
#[derive(Serialize, Clone, Copy)]
pub struct When {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cfg_complete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sid_set: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path_exists: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command_exists: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_not: Option<&'static str>,
}

/// `skip_if` probe: run argv, output contains `match` (case-insensitive) ⇒ "already present".
#[derive(Serialize, Clone, Copy)]
pub struct SkipIf {
    pub argv: &'static [&'static str],
    #[serde(rename = "match")]
    pub match_: &'static str,
}

/// Install/uninstall actions (9 kinds).
#[derive(Serialize, Clone, Copy)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Step {
    Print {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        text: &'static str,
    },
    Warn {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        text: &'static str,
    },
    Spawn {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        argv: &'static [&'static str],
        #[serde(skip_serializing_if = "Option::is_none")]
        env: Option<EnvPairs>,
        #[serde(skip_serializing_if = "Option::is_none")]
        skip_if: Option<SkipIf>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_missing: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        fail_hint: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_error: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warn_hint: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        skipped_text: Option<&'static str>,
    },
    RegisterDefault {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_error: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warn_hint: Option<&'static str>,
    },
    RegisterAll {
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warn_hint: Option<&'static str>,
    },
    SdkInstall {
        target: &'static str,
        #[serde(rename = "fn")]
        fn_name: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        fail_hint: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_error: Option<&'static str>,
    },
    SdkUninstall {
        target: &'static str,
        #[serde(rename = "fn")]
        fn_name: &'static str,
    },
    RmPointer {
        path: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
    },
    RmDir {
        path: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
    },
}

/// hermes profile determination table (`root_when` hit ⇒ root profile).
#[derive(Serialize, Clone, Copy)]
pub struct Profile {
    pub kind: &'static str,
    pub root_when: &'static [&'static str],
}

/// Registrar entry (3 kinds: node host node_entry / python host python_module|python_script).
#[derive(Serialize, Clone, Copy)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Register {
    NodeEntry {
        node_path: &'static str,
        fail_hint: &'static str,
    },
    PythonModule {
        module: &'static str,
        #[serde(rename = "fn")]
        fn_name: &'static str,
        profile: Profile,
        pythonpath: &'static [&'static str],
    },
    PythonScript {
        python: &'static str,
        args: &'static [&'static str],
        fail_hint: &'static str,
    },
}

/// Full registration entry (2 kinds; undefined ⇒ single agent platform).
#[derive(Serialize, Clone, Copy)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RegisterAll {
    NodeEntry {
        node_path: &'static str,
        fail_hint: &'static str,
    },
    PythonScript {
        python: &'static str,
        args: &'static [&'static str],
        #[serde(skip_serializing_if = "EnvPairs::is_empty")]
        env: EnvPairs,
        fail_hint: &'static str,
    },
}

/// node host op entry argv template (owner 2026-10-11 three-source ruling "one constant for each of the two op entry template types"
/// node type; python type = `sdk.rs::python_door_command` function). Shared by the three node hosts
/// (pi/dsh/openclaw), no longer copied per platform. Placeholders: `{op}`/`{args}`/`{node_path}`.
pub const NODE_OP_TEMPLATE: &[&str] = &["node", "{node_path}", "--op", "{op}", "--args", "{args}"];

use serde_json::Value;

use crate::hosts::{Host, Lang};

/// JSON view of install steps (input for the generic engine [`crate::core::steps`]; empty table ⇒ no CLI-side install action for this host).
pub fn install_steps_json(h: &dyn Host) -> Vec<Value> {
    h.install_plan()
        .iter()
        .map(|s| serde_json::to_value(s).expect("install step must serialize"))
        .collect()
}

/// JSON view of uninstall steps.
pub fn uninstall_steps_json(h: &dyn Host) -> Vec<Value> {
    h.uninstall_plan()
        .iter()
        .map(|s| serde_json::to_value(s).expect("uninstall step must serialize"))
        .collect()
}

/// JSON view of registration entry (input for `core::register`).
pub fn register_json(h: &dyn Host) -> Value {
    serde_json::to_value(h.register()).expect("register entry must serialize")
}

/// Full registration entry (undefined ⇒ single agent host).
pub fn register_all_json(h: &dyn Host) -> Option<Value> {
    h.register_all()
        .map(|r| serde_json::to_value(r).expect("register_all entry must serialize"))
}

/// Whether this host uses the python registrar (determines if SDK root is needed: node hosts go through the op gate, so it is not needed).
pub fn needs_sdk_root(h: &dyn Host) -> bool {
    matches!(h.lang(), Lang::Python)
}

/// Whether this host has a self-contained SDK install (install steps contain `sdk_install` ⇒ its target; otherwise `""`).
pub fn sdk_install_target(h: &dyn Host) -> String {
    for st in h.install_plan() {
        if let Step::SdkInstall { target, .. } = st {
            return target.to_string();
        }
    }
    String::new()
}

/// node entry template expansion: `{home}` is replaced candidate by candidate; if it contains `*`, glob takes the first sorted hit; otherwise checks file existence.
/// No hit ⇒ `""` (do not guess).
pub fn resolve_path_template(tmpl: &str, homes: &[String]) -> String {
    for h in homes {
        let cand = tmpl.replace("{home}", h);
        if cand.contains('*') {
            if let Ok(hits) = glob::glob(&cand) {
                let mut v: Vec<std::path::PathBuf> = hits.flatten().collect();
                v.sort();
                if let Some(first) = v.first() {
                    return first.to_string_lossy().to_string();
                }
            }
        } else if std::path::Path::new(&cand).is_file() {
            return cand;
        }
    }
    String::new()
}
