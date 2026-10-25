//! `aimail prompt` -- Rust reimplementation of `cli/aimail:1517-1729` (`cmd_prompt`).
//!
//! Five nested faces: `list` (list rules sorted by name) · `add` · `rm` · `create-file` (scaffold role file) ·
//! `test` (dry-run, **never sends**).
//!
//! Ownership iron rule: `prompt_rules` written to per-agent binding files are **written only by the SDK** => CLI only triggers
//! SDK gate `update` (controlled fields persisted); name validity and L5 filter/matching judgment go through SDK gate **read-only op
//! `prompt-test`** (added in contract v1.0 §4.1, owner ruling 2026-10-10 (a)) —— CLI does not reimplement predicates.
//! Role file three-level lookup (address level -> system level -> `common.md`, the last one
//! board-channel only) is in the **same order and channel gating** as `aimail_base._read_role_file`.
//!
//! All five faces present: `list` / `add` / `rm` / `create-file` / `test` (L1–L5 single-source matching simulation, **never sends**).

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::cmd::report::fail;

pub struct Args {
    pub system_id: String,
    pub agent: Option<String>,
    pub email: Option<String>,
    pub paction: String,
    pub name: String,
    pub file: String,
    pub disable: bool,
    /// Keyword fields for the add face (same as Python: repeatable ⇒ list)
    pub k_subject: Vec<String>,
    pub k_body: Vec<String>,
    pub k_sender: Vec<String>,
    pub k_recipient: Vec<String>,
    /// Input for the test face (no split, whole string)
    pub subject: String,
    pub body: String,
    pub sender: String,
    pub to: Vec<String>,
    pub header: String,
    pub board_role: String,
}

struct Target {
    dir: PathBuf,
    email: String,
    jf: PathBuf,
    cfg: Value,
}

fn prompt_name_ok(sid: &str, email: &str, name: &str) -> Result<bool, String> {
    // Name validity = SDK gate read-only op `prompt-test`'s `name_ok` (Contract v1.0 §4.1 added, owner
    // 2026-10-10 (a) ruling) —— does not replicate predicates; both transport paths are isomorphic (python gate / node op entry).
    // **Failure must be loud** (never use `false` to impersonate "invalid name" —— that would disguise SDK unavailability as a validation result).
    let args = json!({ "system_id": sid, "email": email, "name": name });
    crate::core::sdk::sdk_ops_call(
        "prompt-test",
        &args,
        std::time::Duration::from_secs(30),
        &[],
    )
    .map(|v| v.get("name_ok").and_then(Value::as_bool).unwrap_or(false))
    .map_err(|e| match &e {
        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
        other => format!("{other:?}"),
    })
}

/// L5 rule matching = SDK gate read-only op `prompt-test` (filtering + intra-field OR/inter-field AND logic all in SDK,
/// both transport paths are isomorphic) —— CLI only prints miss/hit, role file existence is still determined locally.
/// Returns `[{name, file, match}]` sorted by name (same batch of rules as the runtime loader).
fn prompt_rules_test(
    sid: &str,
    email: &str,
    subj: &str,
    body: &str,
    sender: &str,
    recp: &str,
) -> Result<Vec<Value>, String> {
    let args = json!({
        "system_id": sid,
        "email": email,
        "subject": subj,
        "body": body,
        "sender": sender,
        "recipient": recp,
    });
    crate::core::sdk::sdk_ops_call(
        "prompt-test",
        &args,
        std::time::Duration::from_secs(30),
        &[],
    )
    .map(|v| {
        v.get("rules")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    })
    .map_err(|e| match &e {
        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
        other => format!("{other:?}"),
    })
}

fn update_binding(sid: &str, cfg: &Value, patch: &Value) -> Result<(), String> {
    // Contract v1.0 §4.1: via SDK gate `update` (judgment and persistence in SDK); action derived from patch fields.
    // Compatibility retention of `updates`/`fields` key names (SDK side reading is unaffected).
    let action = if patch.get("persona").is_some() {
        "persona"
    } else {
        "prompt"
    };
    let args = json!({
        "system_id": sid,
        "binding": cfg.clone(),
        "action": action,
        "updates": patch.clone(),
        "fields": patch.clone(),
    });
    crate::core::sdk::sdk_ops_call("update", &args, std::time::Duration::from_secs(60), &[])
        .map(|_| ())
        .map_err(|e| match &e {
            crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
            other => format!("{other:?}"),
        })
}

/// Rule file name (`_stem_from`: `{serial}_{filename}` takes the segment after the underscore, no underscore ⇒ empty).
fn stem_from(name: &str) -> String {
    match name.split_once('_') {
        Some((_, rest)) => rest.to_string(),
        None => String::new(),
    }
}

fn stem_ok(stem: &str) -> bool {
    !stem.is_empty()
        && stem.len() <= 64
        && stem
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

/// `_role_path`: address level → system level → `common.md` (board channel only,
/// `allow_common`; ruling 2026-10-25 — same channel gating as `aimail_base._read_role_file`).
fn role_path(addr_role_dir: &Path, sys_role_dir: &Path, stem: &str, allow_common: bool) -> String {
    if stem.is_empty() {
        return String::new();
    }
    let p = addr_role_dir.join(format!("{stem}.md"));
    if p.is_file() {
        return p.to_string_lossy().to_string();
    }
    let p = sys_role_dir.join(format!("{stem}.md"));
    if p.is_file() {
        return p.to_string_lossy().to_string();
    }
    if allow_common {
        let c = sys_role_dir.join("common.md");
        if c.is_file() {
            return format!("{} (common.md fallback)", c.to_string_lossy());
        }
    }
    String::new()
}

fn pyjson_str(v: &Value) -> String {
    crate::core::pyjson::str_python(Some(v))
}

/// Rule field rendering (`list`): `subject=[a | b]`; empty/missing ⇒ not rendered (Python: `if not v: continue`).
fn field_render(r: &Value, f: &str) -> Option<String> {
    let v = r.get(f)?;
    let empty = match v {
        Value::Null => true,
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        _ => false,
    };
    if empty {
        return None;
    }
    let items: Vec<String> = match v {
        Value::String(s) => vec![s.clone()],
        Value::Array(a) => a.iter().map(pyjson_str).collect(),
        other => vec![pyjson_str(other)],
    };
    Some(format!("{f}=[{}]", items.join(" | ")))
}

/// Target resolution (three `_fail` messages verbatim, same as `cli/aimail:1530-1549`).
fn resolve_target(home: &Path, sid: &str, a: &Args) -> Result<Target, String> {
    let cfg_path = crate::core::config::gateway_config_path(sid);
    if !cfg_path.is_file() {
        return Err(format!("no system config: {}", cfg_path.to_string_lossy()));
    }
    let gcfg = crate::core::config::load_gateway_config_in(home, sid).unwrap_or_default();
    let agents = crate::cmd::address::list_agents(home, sid, &gcfg);
    let target = if let Some(em) = &a.email {
        agents.iter().find(|r| r.email == em.to_lowercase())
    } else if let Some(ag) = &a.agent {
        agents.iter().find(|r| {
            r.agent == *ag
                || r.email
                    .split('@')
                    .next()
                    .unwrap_or("")
                    .starts_with(ag.as_str())
        })
    } else {
        return Err(format!(
            "prompt needs -a <agent> or -e <email> to locate {}",
            crate::core::contract::binding_file()
        ));
    };
    let Some(t) = target else {
        if let Some(em) = &a.email {
            return Err(format!(
                "no local {} for {em} (run install on that agent first)",
                crate::core::contract::binding_file()
            ));
        }
        let mut names: Vec<String> = agents.iter().map(|r| r.agent.clone()).collect();
        names.sort();
        let known = if names.is_empty() {
            "none".to_string()
        } else {
            format!("{names:?}")
        };
        return Err(format!(
            "agent '{}' not found locally; known agents: {known}",
            a.agent.clone().unwrap_or_default()
        ));
    };
    let jf = home
        .join("systems")
        .join(sid)
        .join(crate::core::bridge_wire::addr_clean(&t.email))
        .join(crate::core::contract::binding_file());
    if !jf.is_file() {
        return Err(format!("missing {}", jf.to_string_lossy()));
    }
    let cfg: Value = std::fs::read_to_string(&jf)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| Value::Object(Map::new()));
    Ok(Target {
        // Row does not carry a directory field => derive leaf directory from email (consistent with the binding file persistence criterion `addr_clean(email)`)
        dir: home
            .join("systems")
            .join(sid)
            .join(crate::core::bridge_wire::addr_clean(&t.email)),
        email: t.email.clone(),
        jf,
        cfg,
    })
}

fn rules_of(cfg: &Value) -> Vec<Value> {
    cfg.get("prompt_rules")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter(|r| r.is_object()).cloned().collect())
        .unwrap_or_default()
}

fn kws(v: &[String]) -> Vec<String> {
    v.iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

pub fn run(a: &Args) -> i32 {
    let sid = a.system_id.clone();
    if sid.is_empty() {
        return fail("prompt needs --system-id");
    }
    // In-domain host interpreter binding (Contract §4.1(2) single source of truth): this command goes via SDK gate `update`/`prompt-test`.
    let _ = crate::core::sdkroot::bind_domain_for_system(&sid);
    let home = crate::core::home::aimail_home();
    let t = match resolve_target(&home, &sid, a) {
        Ok(t) => t,
        Err(msg) => return fail(&msg),
    };
    let addr_role_dir = t.dir.join("role_prompt");
    let sys_role_dir = home
        .join("systems")
        .join(&sid)
        .join("board")
        .join("role_prompt");
    let rules = rules_of(&t.cfg);

    match a.paction.as_str() {
        "list" => {
            if rules.is_empty() {
                println!("  (no prompt rules in {})", t.jf.to_string_lossy());
                return 0;
            }
            let mut sorted = rules.clone();
            sorted.sort_by_key(|r| r.get("name").map(pyjson_str).unwrap_or_default());
            for r in &sorted {
                let mut flds: Vec<String> = Vec::new();
                for f in ["subject", "body", "sender", "recipient"] {
                    if let Some(s) = field_render(r, f) {
                        flds.push(s);
                    }
                }
                let name = r.get("name").map(pyjson_str).unwrap_or_else(|| "-".into());
                let file = r.get("file").map(pyjson_str).unwrap_or_else(|| "-".into());
                let dis = if r.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
                    "  [disabled]"
                } else {
                    ""
                };
                println!("  {name:<28} file={file}{dis}");
                println!(
                    "      {}",
                    if flds.is_empty() {
                        "(NO FIELD — loader skips this rule)".to_string()
                    } else {
                        flds.join("; ")
                    }
                );
                let rp = role_path(&addr_role_dir, &sys_role_dir, &file, false);
                println!(
                    "      role: {}",
                    if rp.is_empty() {
                        "MISSING (loader will skip on match)".to_string()
                    } else {
                        rp
                    }
                );
            }
            0
        }
        "add" => {
            let name = a.name.trim().to_string();
            let ok = match prompt_name_ok(&sid, &t.email, &name) {
                Ok(v) => v,
                Err(e) => return fail(&format!("prompt-test (name) failed: {e}")),
            };
            if !ok {
                return fail(&format!(
                    "bad -n {name:?}: need {{serial}}_{{filename}} with serial 10-99 and filename [a-z0-9_-] (1-64 chars)"
                ));
            }
            let stem = if !a.file.trim().is_empty() {
                a.file.trim().to_string()
            } else {
                stem_from(&name)
            };
            if !stem_ok(&stem) {
                return fail(&format!(
                    "bad file stem {stem:?} (from -n or --file): [a-z0-9_-] 1-64 chars"
                ));
            }
            let mut rule = Map::new();
            rule.insert("name".into(), json!(name));
            rule.insert("file".into(), json!(stem));
            let mut any = false;
            for (f, vals) in [
                ("subject", kws(&a.k_subject)),
                ("body", kws(&a.k_body)),
                ("sender", kws(&a.k_sender)),
                ("recipient", kws(&a.k_recipient)),
            ] {
                if !vals.is_empty() {
                    rule.insert(f.into(), json!(vals));
                    any = true;
                }
            }
            if !any {
                return fail(
                    "need at least one non-empty keyword: --subject/--body/--sender/--recipient",
                );
            }
            if a.disable {
                rule.insert("enabled".into(), json!(false));
            }
            if rules
                .iter()
                .any(|r| r.get("name").map(pyjson_str) == Some(name.clone()))
            {
                return fail(&format!(
                    "rule {name} already exists — remove it first: aimail prompt rm -s {sid} -e {} -n {name}",
                    t.email
                ));
            }
            let mut next = rules.clone();
            next.push(Value::Object(rule));
            next.sort_by_key(|r| r.get("name").map(pyjson_str).unwrap_or_default());
            // Persistence **only via SDK** (per-agent binding file written only by SDK)
            if let Err(e) = update_binding(&sid, &t.cfg, &json!({"prompt_rules": next})) {
                return fail(&format!("update_binding failed: {e}"));
            }
            println!(
                "  added {name} → role_prompt/{}.md",
                stem_from(&name).max(stem.clone())
            );
            println!(
                "  {}: {}",
                crate::core::contract::binding_file(),
                t.jf.to_string_lossy()
            );
            if role_path(&addr_role_dir, &sys_role_dir, &stem, false).is_empty() {
                println!(
                    "  note: role file not present yet — scaffold it with: aimail prompt create-file -s {sid} -e {} -n {name}",
                    t.email
                );
            }
            0
        }
        "rm" => {
            let name = a.name.trim().to_string();
            let keep: Vec<Value> = rules
                .iter()
                .filter(|r| r.get("name").map(pyjson_str) != Some(name.clone()))
                .cloned()
                .collect();
            if keep.len() == rules.len() {
                return fail(&format!("rule {name} not found for this agent"));
            }
            if let Err(e) = update_binding(&sid, &t.cfg, &json!({"prompt_rules": keep})) {
                return fail(&format!("update_binding failed: {e}"));
            }
            println!("  removed {name} ({})", t.jf.to_string_lossy());
            0
        }
        "create-file" => {
            let name = a.name.trim().to_string();
            let ok = match prompt_name_ok(&sid, &t.email, &name) {
                Ok(v) => v,
                Err(e) => return fail(&format!("prompt-test (name) failed: {e}")),
            };
            if !ok {
                return fail(&format!(
                    "bad -n {name:?}: need {{serial}}_{{filename}} with serial 10-99 and filename [a-z0-9_-] (1-64 chars)"
                ));
            }
            let stem = if !a.file.trim().is_empty() {
                a.file.trim().to_string()
            } else {
                stem_from(&name)
            };
            if !stem_ok(&stem) {
                return fail(&format!("bad file stem {stem:?}: [a-z0-9_-] 1-64 chars"));
            }
            let _ = std::fs::create_dir_all(&addr_role_dir);
            let p = addr_role_dir.join(format!("{stem}.md"));
            if p.exists() {
                return fail(&format!("already exists: {}", p.to_string_lossy()));
            }
            let body = format!(
                "# {stem}\n\n\
Role prompt injected when a prompt rule (or the gateway\n\
X-AIMail-Prompt header) selects `{stem}` for an inbound mail.\n\n\
Template variables available: {{{{AGENTMAIL_ADDRESS}}}}, {{{{FROM_ROLE}}}}, {{{{INQUIRY_SUBJECT}}}}, {{{{SOUL_MD_CONTENT}}}}, {{{{SKILLS_LIST}}}}, {{{{TOOLSET_LIST}}}}.\n\n\
Describe how this agent should behave for that class of mail here.\n"
            );
            if let Err(e) = std::fs::write(&p, body) {
                return fail(&format!("write failed: {e}"));
            }
            println!("  created {}", p.to_string_lossy());
            0
        }
        "test" => {
            // ── L1–L5 single-source matching simulation (**never sends**); all matching logic delegated to SDK ──
            let subj = a.subject.clone();
            let body = a.body.clone();
            let sender = a.sender.clone();
            let recp = a.to.join(" ");
            let s_l = subj.to_lowercase();
            let hit = |layer: &str, stem: &str, allow_common: bool| -> i32 {
                let path = role_path(&addr_role_dir, &sys_role_dir, stem, allow_common);
                println!("  {layer} → file={stem}");
                println!(
                    "  role: {}",
                    if path.is_empty() {
                        "MISSING (no injection; chain continues at runtime)".to_string()
                    } else {
                        path
                    }
                );
                0
            };
            // L1 built-in [WHOAMI]
            if s_l.starts_with("[whoami]") {
                return hit("L1 [WHOAMI] (built-in)", "whoami", false);
            }
            // L2 built-in welcome (marker + three tags, same literals as preprocess)
            let marker = s_l.contains("welcome to aimail world");
            let labels_ok = ["persona", "signature", "current_time"].iter().all(|k| {
                body.lines()
                    .any(|ln| ln.trim_start().starts_with(&format!("{k}:")))
            });
            if marker && labels_ok {
                return hit("L2 welcome (built-in)", "role_calibrator", false);
            }
            let mut line = "  L1 [WHOAMI]: no    L2 welcome: no".to_string();
            if marker || labels_ok {
                line.push_str(&format!(
                    "  (MISMATCH warn at runtime: marker={marker} labels={labels_ok})"
                ));
            }
            println!("{line}");
            // L3 board level (gateway injection; --board-role simulation)
            if !a.board_role.trim().is_empty() {
                // board channel: common.md fallback is ALLOWED (ruling 2026-10-25, channel 1 kept)
                return hit("L3 board (simulated)", a.board_role.trim(), true);
            }
            println!("  L3 board: gateway-injected — pass --board-role to simulate");
            // L4 X-AIMail-Prompt header
            if !a.header.trim().is_empty() {
                let stem = a.header.trim();
                if !role_path(&addr_role_dir, &sys_role_dir, stem, false).is_empty() {
                    return hit("L4 X-AIMail-Prompt (gateway header)", stem, false);
                }
                println!(
                    "  L4 header={}: role file MISSING (no common.md fallback on this channel) — runtime logs error, falls through to L5",
                    crate::core::pyjson::repr_python(&json!(stem))
                );
            }
            // L5 local prompt_rules: filtering + matching **all in SDK gate read-only op `prompt-test`**
            // (both transport paths are isomorphic) —— CLI only prints miss/hit, role file existence is still determined locally.
            let sorted = match prompt_rules_test(&sid, &t.email, &subj, &body, &sender, &recp) {
                Ok(v) => v,
                Err(e) => return fail(&format!("prompt-test (L5) failed: {e}")),
            };
            let mut matched_any = false;
            for rule in &sorted {
                let name = rule.get("name").map(pyjson_str).unwrap_or_default();
                let is_match = rule.get("match").and_then(Value::as_bool).unwrap_or(false);
                if !is_match {
                    println!("  L5 miss {name}");
                    continue;
                }
                let file = rule.get("file").map(pyjson_str).unwrap_or_default();
                let path = role_path(&addr_role_dir, &sys_role_dir, &file, false);
                if path.is_empty() {
                    println!(
                        "  L5 hit {name} but role file '{file}' MISSING (no common.md fallback on this channel) — next rule"
                    );
                    continue;
                }
                println!("  L5 HIT {name} → file={file}");
                println!("  role: {path}");
                matched_any = true;
                break;
            }
            if !matched_any {
                println!("  L5: no rule matched — chain falls through (default prompt)");
            }
            println!(
                "  (matched against: subject={} sender={} to={})",
                crate::core::pyjson::repr_python(&json!(subj)),
                crate::core::pyjson::repr_python(&json!(sender)),
                crate::core::pyjson::repr_python(&json!(recp))
            );
            0
        }
        other => fail(&format!("unknown prompt action: {other}")),
    }
}
