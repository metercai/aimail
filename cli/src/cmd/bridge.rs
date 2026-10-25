//! `aimail bridge` — the Rust re-implementation of `cli/aimail:2682-2808` (**status / re-flush routes per system**).
//!
//! `--restart` / `--upgrade` go through deployment surface (`core::bridge_deploy`: binary in place + start process + zip upgrade),
//! same semantics as `deploy_bridge.py`'s standalone branch.
//!
//! Scope (copied from Python):
//! · no `-s`/`-H` ⇒ status check (process / config / route table / log freshness);
//! · with `-s`/`-H` ⇒ refresh forwarding routes for that system: **target priority takes bound webhook_url** (single source of truth),
//!   fallback to historical route table, then fallback to platform default; `host` passes **full URL** (including `://`) or bare `host:port` two fields,
//!   Determined by `target_to_route_fields` (passing wrong values generates undeliverable targets —— tested 2026-09-21);
//! · bridge refresh **directly hits fixed admin address** `127.0.0.1:38081` (different from `sync_route`'s "declarative" addressing, copied verbatim).

use serde_json::json;

use crate::cmd::report::{fail, ok, warn};
use crate::core::bridge_wire::{
    bridge_cfg_file, bridge_log_file, bridge_pids, read_routes, routes_file, system_agents,
    target_to_route_fields, BRIDGE_ADDR,
};

pub struct Args {
    pub system_id: String,
    pub home: String,
    pub restart: bool,
    pub upgrade: bool,
    pub platform: String,
}

pub fn run(a: &Args) -> i32 {
    let pids = bridge_pids();
    let running = !pids.is_empty();

    if a.upgrade {
        return crate::core::bridge_deploy::upgrade_bridge();
    }
    if a.restart {
        // independent restart: start/restart local single-instance bridge (does not depend on system config, copied from Python's --restart branch)
        let bin = crate::core::home::aimail_home().join("bridge/bin/aimail-bridge");
        let cfg = crate::core::bridge_wire::bridge_cfg_file();
        let pid = crate::core::bridge_wire::bridge_pid_file();
        let started = crate::core::bridge_deploy::start_bridge(
            &bin.to_string_lossy(),
            &cfg.to_string_lossy(),
            &pid.to_string_lossy(),
        );
        let _ = started;
        return if pid.exists() { 0 } else { 1 };
    }

    if a.system_id.is_empty() && a.home.is_empty() {
        return status(&pids);
    }
    refresh(a, running)
}

/// Status check (no `-s`/`-H`).
fn status(pids: &[u32]) -> i32 {
    println!("  bridge status:");
    if !pids.is_empty() {
        let list: Vec<String> = pids.iter().map(|p| p.to_string()).collect();
        ok(&format!("Processes: {} ({})", pids.len(), list.join(", ")));
    } else {
        warn("process: not running");
    }

    let cfg = bridge_cfg_file();
    if cfg.is_file() {
        match std::fs::read_to_string(&cfg)
            .ok()
            .and_then(|t| t.parse::<toml::Value>().ok())
        {
            Some(td) => {
                let mode = td.get("mode").and_then(|v| v.as_str()).unwrap_or("?");
                // The key in config is `bind` (historical compatibility with `addr`) —— old code reading addr is always `?`
                let bind = td
                    .get("bind")
                    .or_else(|| td.get("addr"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("?");
                let sys_ids: Vec<String> = td
                    .get("pull")
                    .and_then(|p| p.get("systems"))
                    .and_then(|s| s.as_array())
                    .map(|arr| {
                        arr.iter()
                            .map(|s| {
                                let sid =
                                    s.get("system_id").and_then(|v| v.as_str()).unwrap_or("?");
                                let n = sid.chars().count();
                                sid.chars().skip(n.saturating_sub(12)).collect::<String>()
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                ok(&format!(
                    "Config: mode={} addr={} systems={:?}",
                    mode, bind, sys_ids
                ));
            }
            None => warn("Failed to read config: invalid TOML"),
        }
    } else {
        warn("config: aimail_bridge.toml does not exist");
    }

    let routes = read_routes(&routes_file());
    if !routes.is_empty() {
        ok(&format!("Routes: {}", routes.len()));
        for (email, target) in &routes {
            println!("       {} → {}", email, target);
        }
    } else {
        warn("route table is empty (aimail_routes.toml)");
    }

    let log = bridge_log_file();
    if let Ok(md) = std::fs::metadata(&log) {
        if let Ok(secs) = md
            .modified()
            .map(|t| t.elapsed().map(|d| d.as_secs()).unwrap_or(0))
        {
            ok(&format!("Log: updated {}s ago ({})", secs, log.display()));
        }
    }
    0
}

/// Refresh forwarding routes (with `-s`/`-H`).
fn refresh(a: &Args, running: bool) -> i32 {
    use crate::hosts::identity::{normalize_platform_home, platform_override, resolve_platform};

    let system_home = if a.home.is_empty() {
        std::path::PathBuf::new()
    } else {
        normalize_platform_home(&crate::core::home::expand_user(&a.home))
    };
    let (sid, platform0) = crate::cmd::uninstall::resolve_system_id(&system_home, &a.system_id);
    if sid.is_empty() {
        return fail("Cannot determine system_id (specify with --system-id or --home)");
    }
    let platform = match platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => {
            if !platform0.is_empty() {
                platform0
            } else {
                resolve_platform(&system_home)
            }
        }
        Err(msg) => return fail(&msg),
    };
    if platform.is_empty() {
        return fail(&format!("cannot determine platform (system {})", sid));
    }

    let cfg_path = crate::core::config::gateway_config_path(&sid);
    if !cfg_path.is_file() {
        return fail(&format!(
            "System config does not exist: {}",
            cfg_path.display()
        ));
    }
    if !running {
        warn("bridge not running —— only updating local route table, no hot reload (run --restart first)");
    }

    let routes = read_routes(&routes_file());
    let mut refreshed = 0usize;
    let mut missing = 0usize;
    for (email, target) in system_agents(&sid) {
        let old = routes.get(&email).cloned();
        let (host, port) = target_to_route_fields(&target);
        // When host passes a full URL, the bridge uses from_url (URL fidelity); port is only for required field validation
        let body = json!({"email": email, "host": host, "port": port});
        let (okcall, detail) = crate::core::http::raw_req_method(
            "POST",
            &format!("http://{}/api/v1/routes", BRIDGE_ADDR),
            Some(body.to_string().as_bytes()),
            Some("application/json"),
            5,
        )
        .map(|(code, _)| ((200..300).contains(&code), format!("HTTP {}", code)))
        .unwrap_or_else(|e| (false, e));
        if okcall {
            refreshed += 1;
            if old.as_deref() != Some(target.as_str()) {
                println!(
                    "  ↻ {}: {} → {}",
                    email,
                    old.as_deref().unwrap_or("(none)"),
                    target
                );
            } else {
                println!("  ✓ {}: {}(unchanged)", email, target);
            }
        } else {
            missing += 1;
            warn(&format!(
                "  ✗ {} route registration failed: {}",
                email, detail
            ));
        }
    }

    if refreshed > 0 {
        ok(&format!(
            "Refresh complete: {} routes registered/confirmed",
            refreshed
        ));
    }
    if missing > 0 {
        warn(&format!("{} failed (check if bridge is running)", missing));
        return 1;
    }
    0
}
