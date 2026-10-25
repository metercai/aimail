//! `aimail address` —— address view/maintenance. S3 only ports the **view surface** (`list` / `show`).
//!
//! The maintenance side (`-d/--default`, `set-name`, `set-manager`, hidden `--inbound-live|down`)
//! Keep command surface but all go through [`crate::cmd::stub::not_yet_ported`] (rc 1) —— **never** silently
//! falls back to `list`: Python side `cli/aimail:2045-2065` has two recorded incidents of silent degradation
//! (2026-09-25 unregistered flag fell back to list; 2026-09-29 `-e` appearing alone masked "target does not exist" in the list),
//! op order is copied exactly for this reason.
//!
//! Current code reference: `cli/aimail:1355-1381` (platform enum) · `:1384-1477` (`_list_agents`) ·
//! `:1480-1514` (pointer scan) · `:1974-2002` (header/body) · `:2030-2109` (op chain and view side).

use crate::cmd::report;
use crate::core::{config, contract, home};
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Args {
    pub system_id: String,
    pub agent: Option<String>,
    pub email: Option<String>,
    pub name: Option<String>,
    pub manager: Option<String>,
    pub inbound_live: bool,
    pub inbound_down: bool,
    /// Confirm inbound endpoint (contract §4.3, owner 2026-10-13): the "confirmed" URL carried by inbound up.
    /// If present ⇒ write the confirmed URL back to the route layer based on state (overwriting the install pre-registered value); if absent ⇒ proceed with existing full reconciliation.
    pub inbound_url: Option<String>,
}

pub(crate) struct Row {
    pub(crate) agent: String,
    pub(crate) email: String,
    pub(crate) manager: String,
    pub(crate) webhook: String,
    pub(crate) registered: bool,
    pub(crate) platform: String,
}

/// Address → directory key reverse lookup for owning system (`cli/aimail:2005-2027`).
fn sid_for_address(aimail_home: &Path, email: &str) -> String {
    let want = email.trim().to_lowercase();
    if want.is_empty() {
        return String::new();
    }
    let Ok(systems) = std::fs::read_dir(config::systems_root_in(aimail_home)) else {
        return String::new();
    };
    let mut dirs: Vec<PathBuf> = systems
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    for dir in dirs {
        for (_, agent_email, _) in pointers_in(&dir) {
            if agent_email.trim().to_lowercase() == want {
                return dir
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
            }
        }
    }
    String::new()
}

/// Binding file in each agent directory under the system directory → `(agent_name, email, sid)`.
fn pointers_in(dir: &Path) -> Vec<(String, String, String)> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut subs: Vec<PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    let mut out = Vec::new();
    for sub in subs {
        let path = sub.join(contract::binding_file());
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let email = value
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let sid = value
            .get("system_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !email.is_empty() {
            out.push((String::new(), email, sid));
        }
    }
    out
}

pub(crate) fn list_agents(aimail_home: &Path, sid: &str, cfg: &config::GatewayConfig) -> Vec<Row> {
    let mut rows: Vec<Row> = Vec::new();
    let base = config::system_dir_in(aimail_home, sid);
    if base.is_dir() {
        let mut entries: Vec<PathBuf> = std::fs::read_dir(&base)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        entries.sort();
        for dir in entries {
            let jf = dir.join(contract::binding_file());
            if !jf.is_file() {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&jf) else {
                continue;
            };
            let Ok(value) = serde_json::from_str::<Value>(&text) else {
                continue;
            };
            let email = value
                .get("email")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if email.is_empty() {
                continue;
            }
            // agent name = directory name where the platform pointer resides (pointer scans cfg.system_home / home in bindings)
            let mut agent_name = String::new();
            for key in [
                cfg.system_home.as_str(),
                value
                    .get("system_home")
                    .and_then(Value::as_str)
                    .unwrap_or(""),
                value.get("home").and_then(Value::as_str).unwrap_or(""),
            ] {
                if key.is_empty() || !Path::new(key).is_dir() {
                    continue;
                }
                for (nm, ptr_email, ptr_sid) in crate::hosts::pointer::scan(Path::new(key)) {
                    if ptr_email == email && ptr_sid == sid {
                        agent_name = nm;
                        break;
                    }
                }
                if !agent_name.is_empty() {
                    break;
                }
            }
            if agent_name.is_empty() {
                let local = email.split('@').next().unwrap_or("").to_string();
                agent_name = local.split('.').next().unwrap_or("").to_string();
            }
            rows.push(Row {
                agent: agent_name,
                email,
                manager: value
                    .get("manager_address")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                webhook: value
                    .get("webhook_url")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                registered: true,
                platform: String::new(),
            });
        }
    }

    // Pointer supplement: agent directories already carrying this system's pointer (rename/takeover scenarios; name taken from directory)
    let platform = if cfg.system_home.is_empty() {
        String::new()
    } else {
        crate::hosts::identity::detect_platform_from_home(Path::new(&cfg.system_home)).to_string()
    };
    if !cfg.system_home.is_empty() {
        let home_path = Path::new(&cfg.system_home);
        let mut known: Vec<String> = rows.iter().map(|r| r.agent.clone()).collect();
        // pointer-first: profile/agent directories already carrying this system's pointer are registered by their pointer email (rename scenario)
        for (nm, pe, psid) in crate::hosts::pointer::scan(home_path) {
            if psid != sid || nm.is_empty() {
                continue;
            }
            if !known.contains(&nm) && !rows.iter().any(|r| r.agent == nm) {
                rows.push(Row {
                    agent: nm.clone(),
                    email: pe,
                    manager: String::new(),
                    webhook: String::new(),
                    registered: true,
                    platform: platform.clone(),
                });
                known.push(nm);
            }
        }
    }

    // Registered first, then by agent name (Python same key: `(not registered, agent)`; stable sort)
    rows.sort_by_key(|r| (!r.registered, r.agent.clone()));
    rows
}

fn header_lines(sid: &str, cfg: &config::GatewayConfig) -> Vec<String> {
    let platform = if cfg.system_home.is_empty() {
        String::new()
    } else {
        crate::hosts::identity::detect_platform_from_home(Path::new(&cfg.system_home)).to_string()
    };
    let mut lines = vec![format!(
        "  system: {sid} ({})",
        if platform.is_empty() { "?" } else { &platform }
    )];
    lines.push(String::new());
    lines
}

fn table_lines(rows: &[Row]) -> Vec<String> {
    let mut out = vec![format!(
        "  {:<18} {:<46} {:<30} webhook   status",
        "agent", "email", "manager"
    )];
    for a in rows {
        let webhook: String = a.webhook.chars().take(28).collect();
        if a.registered {
            out.push(format!(
                "  {:<18} {:<46} {:<30} {webhook}  Registered",
                a.agent, a.email, a.manager
            ));
        } else {
            let plat = if a.platform.is_empty() {
                "?"
            } else {
                a.platform.as_str()
            };
            out.push(format!(
                "  {:<18} {:<46} {:<30} {:<28}  Not registered (platform {plat} has this agent, install/bind to register first)",
                a.agent, a.email, "", ""
            ));
        }
    }
    out
}

pub fn run(args: Args) -> i32 {
    let aimail_home = home::aimail_home();
    let mut sid = args.system_id.clone();
    if sid.is_empty() && (args.inbound_live || args.inbound_down) {
        let target = args
            .email
            .clone()
            .or_else(|| args.agent.clone())
            .unwrap_or_default();
        sid = sid_for_address(&aimail_home, &target);
    }
    if sid.is_empty() {
        return report::fail("address requires --system-id");
    }
    let cfg_path = config::gateway_config_path_in(&aimail_home, &sid);
    if !cfg_path.is_file() {
        return report::fail(&format!("no system config: {}", cfg_path.display()));
    }
    let cfg = match config::load_gateway_config_in(&aimail_home, &sid) {
        Some(c) => c,
        None => return report::fail(&format!("failed to read config: {}", cfg_path.display())),
    };
    // In-domain host interpreter binding (Contract §4.1(2) single source of truth): maintenance side (set-manager/set-name/inbound live) goes through SDK gate,
    // if not bound, it degrades to CLI's own `python3` starting the gate ⇒ mismatch with target system.
    let _ = crate::core::sdkroot::bind_domain_for_system(&sid);

    // ── Inbound live/down: must precede op chain (otherwise flag will silently fall back to list) ──────────
    if args.inbound_live || args.inbound_down {
        // ①② Mutual exclusion + location (copied from the three messages in `cli/aimail:_cmd_inbound_route`)
        if args.inbound_live && args.inbound_down {
            return crate::cmd::report::fail(
                "inbound-live and inbound-down are mutually exclusive, pick only one",
            );
        }
        let loc = if args.email.is_some() {
            args.email.clone().unwrap_or_default()
        } else if let Some(a) = &args.agent {
            a.clone()
        } else {
            return crate::cmd::report::fail(
                "inbound live/down needs -a <agent> (or -e <email>) to locate the binding",
            );
        };
        let agents = list_agents(&aimail_home, &sid, &cfg);
        let target = agents.iter().find(|r| {
            if loc.contains('@') {
                r.email == loc
            } else {
                r.agent == loc || r.email.split('@').next().unwrap_or("") == loc
            }
        });
        let Some(t) = target else {
            let msg = if loc.contains('@') {
                format!("no local address for {loc} (run install/bind for that agent first)")
            } else {
                let mut names: Vec<String> = agents.iter().map(|r| r.agent.clone()).collect();
                names.sort();
                format!(
                    "no local address for -a {loc} (local: {})",
                    names.join(", ")
                )
            };
            return crate::cmd::report::fail(&msg);
        };
        let anchor = t.email.clone();
        if !binding_registered(&aimail_home, &sid, &anchor) {
            return crate::cmd::report::fail(&format!(
                "no local binding for {anchor} (run install/bind for that agent first)"
            ));
        }
        let cfgj = Value::Object(cfg.to_json());
        let bcfg = crate::core::bridge_wire::bridge_cfg_file();

        if args.inbound_down {
            let (withdrawn, failed, no_bridge, anchor_out) =
                crate::core::inbound_route::withdraw_inbound_routes(&sid, &cfgj, &bcfg, &anchor);
            if no_bridge {
                return crate::core::inbound_route::finish_anchor(anchor_out.as_ref());
            }
            let note = match crate::core::inbound_route::gateway_backlog_count(
                &cfgj,
                std::slice::from_ref(&anchor),
            ) {
                Some(n) => format!("; backlog {n} pending"),
                None => "; backlog unknown".to_string(),
            };
            crate::cmd::report::ok(&format!(
                "inbound down: {withdrawn} route(s) withdrawn for system {sid}{note}"
            ));
            if failed > 0 {
                crate::cmd::report::warn(&format!("{failed} FAILED (nothing changed for those)"));
            }
            let synth = if anchor_out.is_none() {
                Some(crate::core::inbound_route::already_absent_outcome(&anchor))
            } else {
                anchor_out.clone()
            };
            return crate::core::inbound_route::finish_anchor(synth.as_ref());
        }

        // live：
        // · With confirmed URL (`--inbound-url`, Contract §4.3) ⇒ write the confirmed endpoint back to the route layer based on state,
        //   only touching the anchor address (S1/S2 bridge route upsert / S3 gateway per-address webhook narrow refresh);
        // · Without confirmed URL ⇒ proceed with existing full reconciliation (expected state = local registered bindings; anchor not in table ⇒ synthesize skipped).
        if let Some(confirmed) = &args.inbound_url {
            let out =
                crate::core::inbound_route::live_confirmed(&sid, &anchor, confirmed, &cfgj, &bcfg);
            return crate::core::inbound_route::finish_anchor(Some(&out));
        }
        // No confirmed URL: a defect (owner ruling 2026-10-25 — every `live`
        // carries the real serving endpoint). Warn so the host sees it, then
        // fall back to full reconciliation so the route still converges from
        // the registered bindings.
        crate::cmd::report::warn(&format!(
            "inbound live without a confirmed URL for {anchor}: falling back to full reconciliation — report the real serving endpoint with --inbound-url"
        ));
        let (_reported, anchor_out) =
            crate::core::bridge_wire::reconcile_inbound_routes(&sid, &cfgj, &anchor, &bcfg);
        let synth = if anchor_out.is_none() {
            Some(crate::core::inbound_route::skipped_pull_outcome(&anchor))
        } else {
            anchor_out.clone()
        };
        return crate::core::inbound_route::finish_anchor(synth.as_ref());
    }

    // ── Op derivation (order copied from Python; only "no location parameters at all" is list) ────────────
    let op = if args.manager.is_some() {
        "set-manager"
    } else if args.name.is_some() {
        "set-name"
    } else if args.email.is_some() {
        "show"
    } else {
        "list"
    };

    match op {
        "list" => {
            let agents = list_agents(&aimail_home, &sid, &cfg);
            for line in header_lines(&sid, &cfg) {
                println!("{line}");
            }
            if agents.is_empty() {
                println!("  No locally registered agent (address key directory is empty).");
                return 0;
            }
            for line in table_lines(&agents) {
                println!("{line}");
            }
            0
        }
        "add" | "rm" => {
            // Contract v1.0 §4.1(2): Reserved for SDK reverse calls (agent add/remove belongs to address subcommand).
            // Unimplemented must **fail loudly** (non-0), must not silently treat as success.
            crate::cmd::stub::not_yet_ported("address add|rm")
        }
        "show" => {
            let agents = list_agents(&aimail_home, &sid, &cfg);
            let want = args.email.clone().unwrap_or_default().to_lowercase();
            match agents.iter().find(|r| r.email == want) {
                None => report::fail(&format!(
                    "address {want} not found on this machine ({}: run install on that agent first to register)",
                    contract::binding_file()
                )),
                Some(target) => {
                    for line in header_lines(&sid, &cfg) {
                        println!("{line}");
                    }
                    for line in table_lines(std::slice::from_ref(target)) {
                        println!("{line}");
                    }
                    0
                }
            }
        }
        "set-manager" => {
            // `-m` validation (Python: empty or missing `@` ⇒ same message text)
            let mgr = args.manager.clone().unwrap_or_default();
            if mgr.is_empty() || !mgr.contains('@') {
                return report::fail("address set-manager requires -m <manager email>");
            }
            let agents = list_agents(&aimail_home, &sid, &cfg);
            let target = locate(&agents, &args);
            let Some(t) = target else {
                return report::fail(
                    "this operation requires -a <agent> or -e <email> to locate the target address",
                );
            };
            // Contract v1.0 §4.1: go through SDK gate update(action=set-manager)——judgment and persistence are in SDK, CLI does not read intermediate values.
            let res = match crate::core::sdk::sdk_ops_call(
                "update",
                &serde_json::json!({
                    "system_id": sid.clone(),
                    "email": t.email.clone(),
                    "manager_address": mgr.clone(),
                    "action": "set-manager",
                }),
                std::time::Duration::from_secs(60),
                &[],
            ) {
                Ok(v) => v.get("result").cloned().unwrap_or(v),
                Err(e) => {
                    let msg = match &e {
                        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
                        other => format!("{other:?}"),
                    };
                    return report::fail(&format!("Failed to update manager: {msg}"));
                }
            };
            let _ = &res;
            report::ok(&format!(
                "manager: {} → {} (cloud + local synced)",
                t.email, mgr
            ));
            0
        }
        "set-name" => {
            let new_name = args.name.clone().unwrap_or_default();
            if new_name.is_empty() {
                return report::fail("address set-name requires -n <new address name>");
            }
            let agents = list_agents(&aimail_home, &sid, &cfg);
            let Some(t) = locate(&agents, &args) else {
                return report::fail(
                    "this operation requires -a <agent> or -e <email> to locate the target address",
                );
            };
            // Rename = SDK's CRUD (owner adjudication): validation/derivation/conflict pre-check/cloud rename/whitelist cleanup/
            // local migration/pointers all in SDK; CLI only triggers, does not assemble names itself, does not bridge itself. **Do not replicate algorithm**.
            // Contract v1.0 §4.1: Via SDK gate `update(action=rename)` (judgment/derivation/persistence all in SDK;
            // CLI only triggers, does not assemble names itself, does not take intermediate values).
            let res = match crate::core::sdk::sdk_ops_call(
                "update",
                &serde_json::json!({
                    "system_id": sid.clone(),
                    "email": t.email.clone(),
                    "old_email": t.email.clone(),
                    "new_name": new_name.clone(),
                    "action": "rename",
                }),
                std::time::Duration::from_secs(60),
                &[],
            ) {
                Ok(v) => v.get("result").cloned().unwrap_or(v),
                Err(e) => {
                    let msg = match &e {
                        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
                        other => format!("{:?}", other),
                    };
                    return report::fail(&format!("Address rename failed: {msg}"));
                }
            };
            if res
                .get("unchanged")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                println!("  Already this address: {}", t.email);
                return 0;
            }
            if res.get("merged").and_then(|v| v.as_bool()).unwrap_or(false) {
                report::warn(&format!(
                    "Local directory {} already exists, merging content fields (keeping both directories)",
                    res.get("dir").and_then(|v| v.as_str()).unwrap_or("")
                ));
            }
            if !res
                .get("migrated")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                report::warn(
                    "Local migration failed (cloud renamed, rerun install to rebuild local)",
                );
            }
            report::ok(&format!(
                "Address renamed: {} → {}",
                t.email,
                res.get("new_email").and_then(|v| v.as_str()).unwrap_or("")
            ));
            println!(
                "  Server-side resources (whitelist/contacts/kanban/keys) and local {} all inherited",
                contract::binding_file()
            );
            let sig = res
                .get("signal")
                .cloned()
                .unwrap_or_else(|| serde_json::json!({}));
            let state = sig.get("state").and_then(|v| v.as_str()).unwrap_or("");
            if state == "notified" {
                println!("  Effective: same key continues to be used; online signal triggered route reconciliation, new address sends/receives from today");
            } else {
                report::warn(&format!(
                    "Online signal not delivered ({}): {} — route not refreshed, self-heals after restarting agent or inbound-live",
                    if state.is_empty() { "skipped" } else { state },
                    sig.get("detail").and_then(|v| v.as_str()).unwrap_or("")
                ));
            }
            0
        }
        other => crate::cmd::stub::not_yet_ported(&format!("address {other}")),
    }
}

/// Target location (copied from `cli/aimail:2082-2098`): `-e` exact email; `-a` by agent name or
/// email local part prefix match. No hit ⇒ return None (caller provides unified failure message).
fn locate<'a>(agents: &'a [Row], args: &Args) -> Option<&'a Row> {
    if let Some(em) = &args.email {
        return agents.iter().find(|r| r.email == em.to_lowercase());
    }
    if let Some(ag) = &args.agent {
        return agents.iter().find(|r| {
            r.agent == *ag
                || r.email
                    .split('@')
                    .next()
                    .map(|l| l.starts_with(ag.as_str()))
                    .unwrap_or(false)
        });
    }
    None
}

/// Whether binding is "registered" (binding file exists and can be parsed as an object)—— used by hidden surface anchor guard.
fn binding_registered(home: &std::path::Path, sid: &str, email: &str) -> bool {
    let leaf = home
        .join("systems")
        .join(sid)
        .join(crate::core::bridge_wire::addr_clean(email));
    let f = leaf.join(crate::core::contract::binding_file());
    std::fs::read_to_string(&f)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .map(|v| v.is_object())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_header_matches_python_layout() {
        let lines = table_lines(&[]);
        assert_eq!(
            lines[0],
            format!(
                "  {:<18} {:<46} {:<30} webhook   status",
                "agent", "email", "manager"
            )
        );
    }
}
