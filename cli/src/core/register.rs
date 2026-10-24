//! Platform registration executor (Rust reimplementation of `cli/aimail:2174-2458`) — registry-driven, zero platform protocol in CLI.
//!
//! Two granularities:
//! · `register_agent`  = single agent (`register_default` / `reset` default path)
//! · `register_all`    = full (`--all-agents`; registry `register_all` block)
//!
//! Boundary (hard): **Rules are not in this file** — naming/alias normalization/direct reach determination is done by the SDK's `plan_address_name`
//! plan (called by name via `core::sdkcall` to published functions), and the cloud rename chain is completed in one go by the SDK's `rename_address`;
//! in one go; this file only does "read registry -> fill template -> spawn subprocess -> report/fail based on result".
//!
//! `_finish_registration` only reports the **effective address**, it does **not** push bridge routes: routes are a single contract on the platform side
//! (pysdk `register_bridge_route` / TS `mail-core`), a second writer would create dual sources of truth.

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::core::{setup, style};
use crate::hosts::Host;

const REGISTRAR_TIMEOUT: Duration = Duration::from_secs(120);

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

/// Candidate platform root: home provided by caller -> cfg.system_home -> `~/.<home_dir>`.
/// Shared by registrar and op entry (`core::sdk::ops_target`) — single source of truth for template paths.
pub(crate) fn candidate_homes(platform: &str, cfg: &Value, platform_home: &str) -> Vec<String> {
    let home_dir = crate::hosts::get(platform)
        .map(|h| h.home_dir())
        .unwrap_or("");
    let fallback = crate::core::home::user_home()
        .join(format!(".{home_dir}"))
        .to_string_lossy()
        .to_string();
    [
        platform_home.to_string(),
        setup::pget(cfg, "system_home"),
        fallback,
    ]
    .into_iter()
    .filter(|h| !h.is_empty())
    .collect()
}

/// `_run_registrar`: Subprocess invokes platform registrar -> (rc, stdout+stderr).
fn run_registrar(argv: &[String], env: &[(String, String)]) -> (i32, String) {
    let mut c = Command::new(&argv[0]);
    c.args(&argv[1..]);
    c.envs(env.iter().cloned());
    match c.output() {
        Ok(o) => {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            s.push_str(&String::from_utf8_lossy(&o.stderr));
            (o.status.code().unwrap_or(-1), s)
        }
        Err(e) => (127, format!("registrar not executable: {e}")),
    }
}

fn tail(s: &str, n: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    let start = chars.len().saturating_sub(n);
    chars[start..].iter().collect()
}

/// `_finish_registration`: only reports effective address + self-check hints (routes are not pushed here).
fn finish_registration(platform: &str, agent: &str, email: &str, sid: &str) {
    ok(&format!(
        "agent '{agent}' registered -> {email} (platform {platform})"
    ));
    println!("  verify: aimail address -s {sid} -a {agent}");
}

/// `_register_agent_now` (single agent).
pub fn register_agent(
    platform: &str,
    agent: &str,
    cfg: &Value,
    manager: &str,
    platform_home: &str,
    core_dir: &Path,
) -> Result<(), String> {
    let sid = setup::pget(cfg, "system_id");
    let _home = setup::pget(cfg, "system_home");
    let domain = setup::pget(cfg, "domain");
    let system_name = setup::pget(cfg, "system_name");
    let Some(host) = crate::hosts::get(platform) else {
        return Err(format!("Unknown platform {platform} (no such item in registry)"));
    };
    let rdef = crate::hosts::exec::register_json(host);

    // manager resolution chain (args -> cfg -> env, three names mutually recognized); if still empty, **hard gate** (no registration with '')
    let mgr = setup::resolve_mgr(manager, cfg);
    if mgr.is_empty() {
        return Err(format!(
            "manager missing (not given via arg or env) — {platform} register/allowlist does not accept empty values (owner contract 2026-09-30).\n  provide a value: -m/--manager <addr>, or env {}",
            setup::MANAGER_ENV_NAMES.join(" / ")
        ));
    }

    // ── CLI domain: host path resolution (registry stores template/wildcard paths; only CLI knows host layout) ──
    let cand_homes = candidate_homes(platform, cfg, platform_home);
    let kind = rdef.get("kind").and_then(Value::as_str).unwrap_or("");
    let mut spec = json!({
        "kind": kind,
        "args_template": rdef.get("args").cloned().unwrap_or(json!([])),
        "manager_arg": rdef.get("manager_arg").cloned().unwrap_or(json!([])),
        "fail_hint": rdef.get("fail_hint").cloned().unwrap_or(json!("")),
    });
    {
        let o = spec.as_object_mut().unwrap();
        match kind {
            "python_module" => {
                // hermes: adapter layer directory (PYTHONPATH) + profile directory (rule table driven)
                let mut extra: Vec<String> = Vec::new();
                if let Some(arr) = rdef.get("pythonpath").and_then(Value::as_array) {
                    for rel in arr {
                        let q =
                            crate::core::sdkroot::entry_path(core_dir, rel.as_str().unwrap_or(""));
                        if q.is_dir() {
                            extra.push(q.to_string_lossy().to_string());
                        }
                    }
                }
                let home_path = PathBuf::from(cand_homes.first().cloned().unwrap_or_default());
                let prof = rdef.get("profile").cloned().unwrap_or(json!({}));
                let root_when: Vec<String> = prof
                    .get("root_when")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default();
                let home_name = home_path
                    .file_name()
                    .map(|x| x.to_string_lossy().to_string())
                    .unwrap_or_default();
                let profile_dir = if root_when.iter().any(|w| w == agent) || agent == home_name {
                    home_path.to_string_lossy().to_string()
                } else {
                    let pd = home_path.join("profiles").join(agent);
                    if !pd.is_dir() {
                        // owner 2026-10-07: when only the main agent exists (no other agents), the profile directory **may not exist** ✓
                        // ⇒ Do not block registration (host agent creates it on startup) ✓; only notify ✓
                        eprintln!(
                            "⚠ hermes profile directory does not exist yet (agent will create it upon startup, does not block registration): {}",
                            pd.to_string_lossy()
                        );
                    }
                    pd.to_string_lossy().to_string()
                };
                o.insert(
                    "module".into(),
                    rdef.get("module").cloned().unwrap_or(json!("")),
                );
                o.insert("fn".into(), rdef.get("fn").cloned().unwrap_or(json!("")));
                o.insert("pythonpath".into(), json!(extra));
                o.insert("profile_dir".into(), json!(profile_dir));
                o.insert("python".into(), json!(setup::python_bin()));
            }
            "python_script" => {
                let rel = rdef.get("python").and_then(Value::as_str).unwrap_or("");
                let script = crate::core::sdkroot::entry_path(core_dir, rel);
                if !script.is_file() {
                    return Err(format!(
                        "{platform} registrar missing: {} (SDK not installed on this machine)",
                        script.to_string_lossy()
                    ));
                }
                o.insert("script".into(), json!(script.to_string_lossy().to_string()));
                o.insert("python".into(), json!(setup::python_bin()));
            }
            "node_entry" => {
                let node = crate::hosts::exec::resolve_path_template(
                    rdef.get("node_path").and_then(Value::as_str).unwrap_or(""),
                    &cand_homes,
                );
                if node.is_empty() {
                    return Err(format!(
                        "{platform} platform package not installed (node entry missing)\n  {}",
                        rdef.get("fail_hint").and_then(Value::as_str).unwrap_or("")
                    ));
                }
                o.insert("node_path".into(), json!(node));
                o.insert("node_bin".into(), json!("node"));
            }
            other => {
                return Err(format!(
                    "Unknown registration delegate kind '{other}'(platform {platform})——registry corrupted"
                ));
            }
        }
    }

    // ── Contract v1.0 §4.1: naming + registrar execution + assembly-time renaming + binding persistence —— all within SDK gate assemble ──
    // transport dispatch (§4.1(2)): node host (node_entry) directly calls the platform package's own op entry via registry `ops.argv`
    // Call the op entry bundled with the platform package ⇒ **do not pass register_spec** (the registrar is itself; CLI does not run it on its behalf);
    // python host (python_module / python_script) still runs external registrar via python gate ⇒ includes register_spec.
    let mut asm = json!({
        "system_id": sid,
        "system_cfg": cfg.clone(),
        "domain": domain,
        "system_name": system_name,
        "manager_address": mgr,
        "home": cand_homes.first().cloned().unwrap_or_default(),
    });
    if matches!(host.lang(), crate::hosts::Lang::Python) {
        asm["register_spec"] = spec.clone();
    }
    // Host identity key (node multi-agent host like openclaw's `main`) —— naming/alias normalization **still within SDK**
    // (owner 2026-10-02 C1); empty = SDK takes default name (single-agent host remains unchanged).
    if !agent.is_empty() {
        asm["requested_name"] = json!(agent);
    }
    // Local inbound endpoint (contract inbound_ports single source of truth) —— three states ①: registered value = local endpoint.
    // node host (node_entry) opAssemble is a shared library, does not guess platform ⇒ CLI injects contract facts
    // as parameters (`local_webhook_url`); published mail-core opAssemble already reads
    // this parameter (`args['local_webhook_url'] || args['webhook_url'] || ''`), in three states ① (key
    // absent) it is persisted as-is. python host (PY gate self-calculates local_webhook_url) and legacy path
    // (platform package self-calculates) unaffected —— hence only inject for node_entry, PY host envelope zero change.
    // Contract literal ratchet: port/path taken from contract single source of truth; host 127.0.0.1 = inbound listening convention
    // (consistent with legacy `inboundUrl(port, host='127.0.0.1')` default).
    if matches!(host.lang(), crate::hosts::Lang::Node) {
        if let Some(port) = crate::core::contract::inbound_port(platform) {
            asm["local_webhook_url"] =
                json!(format!("http://127.0.0.1:{}", port) + crate::core::contract::inbound_path());
        }
        // G1b(2026-10-13)→P3-C(2026-10-17):preset self-reported by SDK side(dsh register-cli
        // assembleExtra host layout data,10-11 three-source ruling 'host self-report/internal → SDK internal');
        // P3-F(2026-10-17): node host register.args removed -- CLI has no responsibility for fixed naming,
        // declaration does not produce behavior (envelope does not carry it, production code has zero calls to plan_address_name).
        // dsh check session dimension criteria only recognizes top-level preset binding (adapters.rs:895).
    }
    let res = crate::core::sdk::sdk_ops_call_for(
        platform,
        &cand_homes,
        "assemble",
        &asm,
        REGISTRAR_TIMEOUT,
        &[],
    )
    .map(|e| e.get("result").cloned().unwrap_or(e))
    .map_err(|e| match e {
        crate::core::sdk::AbiError::Call { msg, .. } => msg,
        other => other.display_like_python(),
    })?;
    let target_email = res
        .get("email")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    finish_registration(platform, agent, &target_email, &sid);
    Ok(())
}

/// `_run_register_all`: full registration driven by the `register_all` block in the registry (undefined ⇒ single agent platform).
pub fn register_all(
    platform: &str,
    cfg: &Value,
    mgr: &str,
    platform_home: &str,
    core_dir: &Path,
) -> Result<(), String> {
    let Some(host) = crate::hosts::get(platform) else {
        warn(&format!("{platform} is not in the host table (register_all skipped)"));
        return Ok(());
    };
    let rall = crate::hosts::exec::register_all_json(host);
    let sid = setup::pget(cfg, "system_id");
    let home_dir = crate::hosts::get(platform)
        .map(|h| h.home_dir())
        .unwrap_or("");
    let home = {
        let h = setup::pget(cfg, "system_home");
        if h.is_empty() {
            crate::core::home::user_home()
                .join(format!(".{home_dir}"))
                .to_string_lossy()
                .to_string()
        } else {
            h
        }
    };
    let Some(rall) = rall else {
        register_agent(platform, "", cfg, mgr, platform_home, core_dir)?;
        ok(&format!(
            "{platform} single agent platform (register_all undefined) -- default agent registered"
        ));
        return Ok(());
    };
    let fill = |t: &str| -> String {
        t.replace("{sid}", &sid)
            .replace("{home}", &home)
            .replace("{mgr}", mgr)
    };
    let kind = rall.get("kind").and_then(Value::as_str).unwrap_or("");
    // Contract v1.0 §4.1(2): node side **always via op gate** (`--op assemble`) -- no longer assembling legacy flag argv
    // (that argv set only holds for python_script form). Multi-agent hosts enumerate identity keys by host container
    // (openclaw: `agents/*`), registering each through the gate; empty enumeration ⇒ single registration. Naming/alias normalization belongs to SDK.
    if matches!(host.lang(), crate::hosts::Lang::Node) {
        let homes = candidate_homes(platform, cfg, platform_home);
        let node = crate::hosts::exec::resolve_path_template(
            rall.get("node_path").and_then(Value::as_str).unwrap_or(""),
            &homes,
        );
        if node.is_empty() {
            warn(&format!(
                "{platform} platform package not installed (register-all node entry missing)\n  {}",
                rall.get("fail_hint").and_then(Value::as_str).unwrap_or("")
            ));
            return Ok(());
        }
        let keys = crate::core::detect::detect_static(std::path::Path::new(&home), Some(platform))
            .map(|i| i.agent_keys)
            .unwrap_or_default();
        if keys.is_empty() {
            register_agent(platform, "", cfg, mgr, platform_home, core_dir)?;
            ok(&format!(
                "{platform} register_all complete (single agent, via gate assemble)"
            ));
            return Ok(());
        }
        let mut done = 0usize;
        for k in &keys {
            match register_agent(platform, k, cfg, mgr, platform_home, core_dir) {
                Ok(()) => done += 1,
                Err(e) => warn(&format!("{platform} agent '{k}' registration failed: {e}")),
            }
        }
        ok(&format!(
            "{platform} register_all complete ({done}/{} agents via gate assemble)",
            keys.len()
        ));
        return Ok(());
    }
    let argv: Vec<String> = match kind {
        "python_script" => {
            let script = crate::core::sdkroot::entry_path(
                core_dir,
                rall.get("python").and_then(Value::as_str).unwrap_or(""),
            );
            if !script.is_file() {
                warn(&format!(
                    "{platform} full registrar missing: {} (SDK not installed with local machine)",
                    script.to_string_lossy()
                ));
                return Ok(());
            }
            let mut v = vec![setup::python_bin(), script.to_string_lossy().to_string()];
            if let Some(args) = rall.get("args").and_then(Value::as_array) {
                v.extend(args.iter().map(|x| fill(x.as_str().unwrap_or(""))));
            }
            v
        }
        other => {
            warn(&format!(
                "register_all: unknown kind '{other}' ({platform}) — registry corrupted"
            ));
            return Ok(());
        }
    };
    let mut env: Vec<(String, String)> = Vec::new();
    if let Some(m) = rall.get("env").and_then(Value::as_object) {
        for (k, v) in m {
            env.push((k.clone(), fill(v.as_str().unwrap_or(""))));
        }
    }
    let (rc, out) = run_registrar(&argv, &env);
    if rc != 0 {
        warn(&format!(
            "{platform} full registration exit {rc}: {}{}\n  {}",
            tail(&out, 400),
            "",
            rall.get("fail_hint").and_then(Value::as_str).unwrap_or("")
        ));
        return Ok(());
    }
    ok(&format!(
        "{platform} register_all complete (SDK registered all enumerated agents)"
    ));
    Ok(())
}

/// cfg -> ctx shape reused by `reset`/`install` (`Map` for future extension).
pub fn ctx_of(cfg: &Value) -> Map<String, Value> {
    cfg.as_object().cloned().unwrap_or_default()
}
