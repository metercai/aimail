//! `aimail uninstall` —— Rust port of `cli/aimail:3250-3435`.
//!
//! Equivalent to `install`: removes aimail configuration from the agent system. Order:
//! sid/home resolution (`resolve_system_id`) → idempotent short-circuit (if neither data nor pointer exists ⇒ considered uninstalled) →
//! platform determination → **confirmation** (skipped with `-y`) → target gateway deregistration determination (`-g` > cfg; **deliberately ignores AIMAIL_GW_URL,
//! and does not fall back to production defaults**) → per-binding gateway deregistration (SDK single entry) → gateway-side fallback (reverse lookup address table by sid +
//! whitelist cleanup, SDK single entry) → platform `uninstall_steps` (table-driven) → local data (mail/system directory/original key).

use serde_json::{json, Value};
use std::io::Write;

use crate::core::{config, home, setup, steps, style};
use crate::hosts::Host;

pub struct Args {
    pub system_id: String,
    pub home: String,
    pub gateway_url: String,
    pub yes: bool,
    pub platform: String,
}

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

fn fail(msg: &str) -> i32 {
    println!("  {}{}{} {}", style::RED, style::CROSS, style::NC, msg);
    1
}

/// `resolve_system_id`: explicit sid > reverse lookup of configured system_home > pointer ownership > auto-detection.
pub(crate) fn resolve_system_id(
    system_home: &std::path::Path,
    explicit_sid: &str,
) -> (String, String) {
    let platform = if system_home.as_os_str().is_empty() {
        String::new()
    } else {
        crate::hosts::identity::detect_platform_from_home(system_home).to_string()
    };
    if explicit_sid.is_empty() {
        // Single system auto-determination (path promised in welcome/ping messages, C-6): exactly one valid system on this machine
        // (systems/<sid>/ contains aimail_gateway.json) ⇒ determine directly; zero or multiple ⇒ do not determine
        // (if multiple, caller reports "unresolved", do not guess).
        let ids = config::list_system_ids_in(&home::aimail_home());
        if ids.len() == 1 {
            return (ids.into_iter().next().unwrap(), platform);
        }
        return (String::new(), platform);
    }
    // Configuration reverse lookup: which platform does the cfg.system_home for this sid point to
    if let Some(c) = config::load_gateway_config(explicit_sid) {
        if !c.system_home.is_empty() {
            let p = home::abs_path(&home::expand_user(&c.system_home));
            let det = crate::hosts::identity::detect_platform_from_home(&p);
            if det != "unknown" {
                return (explicit_sid.to_string(), det.to_string());
            }
        }
    }
    // Fallback: which platform pointer points to this sid (driven by registry order)
    let uh = home::user_home();
    for p in crate::hosts::order() {
        if crate::hosts::pointer::sid_for_name(&uh, p) == explicit_sid {
            return (explicit_sid.to_string(), p.to_string());
        }
    }
    (explicit_sid.to_string(), platform)
}

/// `_is_readable_file`
fn readable_file(p: &std::path::Path) -> bool {
    p.is_file() && std::fs::File::open(p).is_ok()
}

/// `_confirm`
fn confirm(msg: &str, yes: bool) -> bool {
    if yes {
        return true;
    }
    print!("  {msg} [y/N] ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    if std::io::stdin().read_line(&mut line).is_err() {
        return false;
    }
    // EOF (reading 0 bytes) is equivalent to Python's EOFError
    if line.is_empty() {
        return false;
    }
    matches!(line.trim().to_lowercase().as_str(), "y" | "yes")
}

/// `list_system_domains` (`aimail_tools._GatewayClient`): GET domain table → array or `data` array.
fn list_system_domains(gw_url: &str, admin_key: &str, sid: &str) -> Option<Vec<Value>> {
    let path = format!("/api/v1/admin/systems/{sid}/domains");
    let headers = crate::core::sig::signed_headers(admin_key, "GET", &path, None, "");
    let url = format!("{}{}", gw_url.trim_end_matches('/'), path);
    let (status, body) = crate::core::http::json_req(&url, &headers, None, Some("GET"), 15);
    if !(200..300).contains(&status) {
        return None;
    }
    match body {
        Value::Array(a) => Some(a),
        Value::Object(o) => o.get("data").and_then(Value::as_array).cloned(),
        _ => None,
    }
}

pub fn run(a: &Args) -> i32 {
    let mut sid = a.system_id.clone();
    if a.home.is_empty() && sid.is_empty() {
        return fail("uninstall requires --system-id (or locate the platform with --home)");
    }
    let system_home = if !a.home.is_empty() {
        crate::hosts::identity::normalize_platform_home(&home::expand_user(&a.home))
    } else {
        // Has sid but no --home: authoritative cfg reverse lookup for system_home; leave empty if no local config (hand off to resolve_system_id)
        let sh0 = config::load_gateway_config(&sid)
            .map(|c| c.system_home)
            .unwrap_or_default();
        if sh0.is_empty() {
            std::path::PathBuf::new()
        } else {
            home::expand_user(&sh0)
        }
    };
    let (sid2, mut platform) = resolve_system_id(&system_home, &sid);
    sid = sid2;
    if sid.is_empty() {
        return fail("uninstall requires --system-id (or locate the platform with --home)");
    }
    // Idempotent: if neither system data nor pointer exists ⇒ considered uninstalled
    let systems_dir = config::systems_root_in(&home::aimail_home());
    let has_dir = systems_dir.join(&sid).is_dir();
    let uh = home::user_home();
    let has_ptr = crate::hosts::order()
        .iter()
        .any(|p| crate::hosts::pointer::sid_for_name(&uh, p) == sid);
    if !has_dir && !has_ptr {
        println!("  system {sid} not installed (already uninstalled?)");
        return 0;
    }
    platform = match crate::hosts::identity::platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => {
            if !platform.is_empty() {
                platform
            } else {
                crate::hosts::identity::resolve_platform(&system_home)
            }
        }
        Err(msg) => return fail(&msg),
    };
    if platform.is_empty() {
        return fail(&format!(
            "cannot determine platform (system {sid}) — specify the platform root with --home"
        ));
    }

    println!("  uninstall system={sid} platform={platform}");
    if !confirm(
        &format!("Uninstall the aimail integration of system {sid} (incl. gateway deregistration and local data)?"),
        a.yes,
    ) {
        println!("  cancelled");
        return 1;
    }

    // Uninstall target gateway determination: -g > cfg (**do not check AIMAIL_GW_URL, do not fall back to production default**)
    let mut gw: Value = config::load_gateway_config(&sid)
        .map(|c| Value::Object(c.to_json()))
        .unwrap_or_else(|| json!({}));
    let gw_flag = a.gateway_url.trim().to_string();
    let gw_cfg = setup::pget(&gw, "gateway_url").trim().to_string();
    if !gw_flag.is_empty() {
        gw["gateway_url"] = json!(gw_flag);
        ok(&format!("gateway_url: {gw_flag}(explicit -g)"));
        if !gw_cfg.is_empty() && gw_cfg != gw_flag {
            warn(&format!(
                "-g {gw_flag} overrides cfg.gateway_url={gw_cfg}(uninstall per -g)"
            ));
        }
    } else if !gw_cfg.is_empty() {
        ok(&format!(
            "gateway_url: {gw_cfg}(from cfg; can be overridden by -g)"
        ));
    } else {
        warn(
            "cfg missing gateway_url and -g not provided —— cannot determine deregistration target, skipping gateway-side deregistration (no production default fallback); local data will still be cleared",
        );
    }

    // transport dispatch (contract §4.1(2)): **only python host** needs SDK root; node host op entry is
    // direct via registry `ops.argv` ⇒ must not hard exit due to missing pysdk (uninstall_steps sdk_uninstall only exists for python host).
    // Platform value priority is **system-level cfg recorded `platform`** (written by `aimail install`, authoritative);
    // platform name resolved by caller (`--platform` / probe) may not be available in shared domain scenarios.
    let recorded = setup::pget(&gw, "platform");
    let transport_platform = if recorded.is_empty() {
        platform.clone()
    } else {
        recorded
    };
    // Single source of truth symmetry (same as install side): **first bind in-domain host interpreter** (store in process local state, P8: process env write delete;
    // python_bin()/SDK root resolution via binding read —— uses the same interpreter as install for SDK installation,
    // otherwise pip uninstall cannot find target environment).
    // Iron rule of order (2026-10-12 owner): bind **before** `resolve_or_placeholder`, otherwise the SDK root will use
    // the CLI's own `python3` to find it ⇒ installed SDK in the host venv will be judged as "not installed" (verified with deerflow).
    if !system_home.as_os_str().is_empty() {
        let _cn = setup::pget(&gw, "container");
        crate::core::sdkroot::bind_domain(&_cn, &system_home.to_string_lossy());
    }
    let core_dir = if crate::hosts::get(&transport_platform)
        .map(|h| h.op_template().is_some())
        .unwrap_or(false)
    {
        std::path::PathBuf::new()
    } else {
        crate::core::sdkroot::resolve_or_placeholder().path
    };
    let gw_url = setup::pget(&gw, "gateway_url");
    let admin_key = setup::pget(&gw, "admin_key");
    let manager = setup::pget(&gw, "manager_address");
    let sid_dir = systems_dir.join(&sid);

    // 1. Per-binding gateway uninstall (public chain: api-key → domain → whitelist, all in SDK)
    if sid_dir.is_dir() {
        let mut subs: Vec<std::path::PathBuf> = std::fs::read_dir(&sid_dir)
            .map(|rd| rd.flatten().map(|e| e.path()).collect())
            .unwrap_or_default();
        subs.sort();
        for sub in subs {
            let aj = sub.join(crate::core::contract::binding_file());
            if !readable_file(&aj) {
                continue;
            }
            let email = std::fs::read_to_string(&aj)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("email")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                })
                .unwrap_or_default();
            if email.is_empty() {
                continue;
            }
            if gw_url.is_empty() || admin_key.is_empty() {
                warn(&format!(
                    "no gateway credentials — skip gateway deregister for {email}"
                ));
                continue;
            }
            let mgr_binding = std::fs::read_to_string(&aj)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("manager_address")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                })
                .unwrap_or_default();
            let mgr_use = if mgr_binding.is_empty() {
                manager.clone()
            } else {
                mgr_binding
            };
            // Contract v1.0 §4.1: go through SDK gate teardown (determination and persistence in SDK; CLI does not pass client/core_dir)
            match crate::core::sdk::sdk_ops_call_for(
                &platform,
                &[system_home.to_string_lossy().to_string()],
                "teardown",
                &json!({
                    "system_id": sid,
                    "email": email,
                    "manager_address": mgr_use,
                    "gw": gw_url,
                    "admin_key": admin_key,
                    "mode": {"unregister": true, "whitelist": false, "backfill": false},
                }),
                std::time::Duration::from_secs(120),
                &[],
            ) {
                Ok(env) => {
                    let st = action_of(
                        &env.get("result").cloned().unwrap_or(env),
                        "deregister_agent_email",
                    );
                    ok(&format!(
                        "gateway deregister {email} (api-key={} domain={} whitelist={})",
                        st.get("api_key").map(|v| v.to_string()).unwrap_or_default(),
                        st.get("domain").map(|v| v.to_string()).unwrap_or_default(),
                        st.get("whitelist")
                            .map(|v| v.to_string())
                            .unwrap_or_default()
                    ))
                }
                Err(e) => warn(&format!(
                    "gateway deregister {email} failed: {}",
                    e.display_like_python()
                )),
            }
        }
    }

    // 1b. Gateway-side fallback: addresses and whitelist lines not covered by local bindings (SDK single entry)
    if !gw_url.is_empty() && !admin_key.is_empty() {
        let local_emails: Vec<String> = if sid_dir.is_dir() {
            std::fs::read_dir(&sid_dir)
                .map(|rd| {
                    rd.flatten()
                        .map(|e| e.path())
                        .filter(|p| p.is_dir())
                        .filter_map(|d| {
                            let aj = d.join(crate::core::contract::binding_file());
                            if !readable_file(&aj) {
                                return None;
                            }
                            std::fs::read_to_string(&aj)
                                .ok()
                                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                                .and_then(|v| {
                                    v.get("email")
                                        .and_then(Value::as_str)
                                        .map(|s| s.to_string())
                                })
                        })
                        .collect()
                })
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        let doms = match list_system_domains(&gw_url, &admin_key, &sid) {
            Some(d) => d,
            None => {
                warn("gateway reverse lookup failed(skipping gateway-side fallback)");
                Vec::new()
            }
        };
        let mut addrs: Vec<String> = Vec::new();
        let mut domains: Vec<String> = Vec::new();
        for d in &doms {
            let v = d
                .get("domain")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if v.is_empty() {
                continue;
            }
            if v.contains('@') {
                addrs.push(v);
            } else {
                domains.push(v);
            }
        }
        let mut deregistered: Vec<String> = Vec::new();
        for ad in &addrs {
            if local_emails.contains(ad) || (!manager.is_empty() && *ad == manager) {
                continue;
            }
            match crate::core::sdk::sdk_ops_call_for(
                &platform,
                &[system_home.to_string_lossy().to_string()],
                "teardown",
                &json!({
                    "system_id": sid,
                    "email": ad,
                    "manager_address": manager,
                    "gw": gw_url,
                    "admin_key": admin_key,
                    "mode": {"unregister": true, "whitelist": false, "backfill": false},
                }),
                std::time::Duration::from_secs(120),
                &[],
            ) {
                Ok(_) => {
                    deregistered.push(ad.clone());
                    ok(&format!("gateway deregister (no local binding) {ad}"));
                }
                Err(e) => warn(&format!(
                    "gateway deregister (no local binding) {ad} failed: {}",
                    e.display_like_python()
                )),
            }
        }
        match crate::core::sdk::sdk_ops_call_for(
            &platform,
            &[system_home.to_string_lossy().to_string()],
            "teardown",
            &json!({
                "system_id": sid,
                "email": "",
                "gw": gw_url,
                "admin_key": admin_key,
                "mode": {"unregister": false, "whitelist": true,
                          "addresses": addrs, "domains": domains, "deregistered": deregistered},
            }),
            std::time::Duration::from_secs(120),
            &[],
        ) {
            Ok(env) => {
                let sw = action_of(
                    &env.get("result").cloned().unwrap_or(env),
                    "cleanup_system_whitelists",
                );
                if let Some(per_key) = sw.get("per_key").and_then(Value::as_object) {
                    for (k, n) in per_key {
                        if n.as_i64().unwrap_or(0) != 0 {
                            ok(&format!("gateway whitelist sweep({k}): removed {n} row(s)"));
                        }
                    }
                }
                if let Some(errs) = sw.get("errors").and_then(Value::as_array) {
                    for e in errs {
                        warn(&format!(
                            "whitelist sweep: {}",
                            e.as_str().unwrap_or(&e.to_string())
                        ));
                    }
                }
                if let Some(r) = sw.get("removed") {
                    if r.as_i64().unwrap_or(0) != 0 {
                        ok(&format!(
                            "gateway whitelist sweep: {r} row(s) removed (system-owned scope)"
                        ));
                    }
                }
            }
            Err(e) => warn(&format!(
                "whitelist sweep failed: {}",
                e.display_like_python()
            )),
        }
    }

    // 2. Platform-side cleanup (uninstall_steps table-driven)
    let mut ctx = serde_json::Map::new();
    ctx.insert(
        "home".into(),
        json!(system_home.to_string_lossy().to_string()),
    );
    ctx.insert("sid".into(), json!(sid));
    ctx.insert("cfg".into(), gw.clone());
    // Contract single source: steps write `{pointer_file}`, value taken from contract (host table has zero contract literals).
    ctx.insert(
        "pointer_file".into(),
        json!(crate::core::contract::pointer_file()),
    );
    if let Err(e) = steps::run_uninstall_steps(&platform, &mut ctx, &core_dir) {
        warn(&e);
    }

    // 3. Local data
    if sid_dir.is_dir() {
        if let Ok(rd) = std::fs::read_dir(&sid_dir) {
            for e in rd.flatten() {
                let aj = e.path().join(crate::core::contract::binding_file());
                let email = std::fs::read_to_string(&aj)
                    .ok()
                    .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                    .and_then(|v| {
                        v.get("email")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_default();
                if email.is_empty() {
                    continue;
                }
                let mdir = e.path().join("mail");
                if mdir.is_dir() {
                    let _ = std::fs::remove_dir_all(&mdir);
                    ok(&format!("removed mail data {}", mdir.to_string_lossy()));
                }
            }
        }
        let _ = std::fs::remove_dir_all(&sid_dir);
        ok(&format!(
            "removed system data {}",
            sid_dir.to_string_lossy()
        ));
    }
    let raw = sid_dir.join(setup::SYSTEM_RAW_KEY_FILE);
    if raw.is_file() {
        let _ = std::fs::remove_file(&raw);
        ok(&format!("removed raw key {}", raw.to_string_lossy()));
    }

    println!("  uninstall done for {sid}");
    0
}

/// Contract v1.0 §4.1: In gate op's `result.actions[]`, get that item's payload by action name.
fn action_of(res: &Value, key: &str) -> Value {
    res.get("actions")
        .and_then(Value::as_array)
        .and_then(|a| a.iter().find_map(|x| x.get(key).cloned()))
        .unwrap_or(Value::Null)
}
