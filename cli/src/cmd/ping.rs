//! `aimail ping` — the Rust re-implementation of `cli/ping_test.py` (P3b).
//!
//! Flow: identity chain (`--agent-home`/`AGENT_HOME` **platform pointer file** → `resolve_system_id` → read system config) →
//! Main agent address (`-a` explicit → first local binding → alias normalization; naming defined by SDK, CLI no longer holds default name) → agent api_key (**only accepts
//! agent key**, auth.local is 1:1 semantic, no admin fallback) → probe edition → SMTP send (advanced
//! failure falls back to base whitelist direct send) → **three-stage log event** polling determination → snapshot check.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::cmd::report::{fail, warn};

pub struct Args {
    pub system_id: String,
    pub agent_home: String,
    pub agent: String,
    pub manager: String,
    pub timeout: i64,
    pub no_snapshot: bool,
}

/// Character normalization for the local-part of an email address (`[^A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]` → `_`).
pub(crate) fn sanitize_local(name: &str) -> String {
    let ok = |c: char| c.is_ascii_alphanumeric() || "!#$%&'*+-/=?^_`{|}~".contains(c);
    name.chars().map(|c| if ok(c) { c } else { '_' }).collect()
}

/// `email_for_agent`: shared domain constructs `<name>.<system_name>@<domain>`.
pub(crate) fn email_for_agent(name: &str, domain: &str, system_name: &str) -> String {
    let base = {
        let s = sanitize_local(name);
        if s.is_empty() {
            "agent".to_string()
        } else {
            s
        }
    };
    if !system_name.is_empty() {
        format!("{}.{}@{}", base, system_name, domain)
    } else {
        format!("{}@{}", base, domain)
    }
}

/// Primary agent address: first locally registered binding → derived from default name.
///
/// Naming is owned by SDK/host (owner removed `default_agent_name` on 2026-10-18): CLI no longer holds "primary agent name";
/// Use `aimail ping -a <agent>` when target needs to be specified (welcome's `--agent` is internal switch, not in command surface).
pub(crate) fn main_agent_email(cfg: &serde_json::Value) -> String {
    let dom = cfg.get("domain").and_then(|v| v.as_str()).unwrap_or("");
    let sysname = cfg
        .get("system_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let sid = cfg.get("system_id").and_then(|v| v.as_str()).unwrap_or("");
    let sys_dir = crate::core::config::systems_root_in(&crate::core::home::aimail_home()).join(sid);
    if !sid.is_empty() && sys_dir.is_dir() {
        let mut dirs: Vec<_> = std::fs::read_dir(&sys_dir)
            .map(|it| it.flatten().map(|e| e.path()).collect())
            .unwrap_or_default();
        dirs.sort();
        for d in dirs {
            let aj = d.join(crate::core::contract::binding_file());
            if let Ok(text) = std::fs::read_to_string(&aj) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    let e = v.get("email").and_then(|x| x.as_str()).unwrap_or("");
                    if !e.is_empty() {
                        return e.to_string();
                    }
                }
            }
        }
    }
    email_for_agent("default", dom, sysname)
}

pub fn run(a: &Args) -> i32 {
    let home = crate::core::home::aimail_home();
    let systems = crate::core::config::systems_root_in(&home);

    // ── Identity chain: platform pointer file (--agent-home / AGENT_HOME) → resolve_system_id ──
    let mut sid = a.system_id.clone();
    let mut email = a.agent.clone();
    let agent_home = if !a.agent_home.is_empty() {
        Some(crate::core::home::expand_user(&a.agent_home))
    } else {
        std::env::var("AGENT_HOME")
            .ok()
            .map(|s| crate::core::home::expand_user(&s))
    };
    if let Some(ah) = &agent_home {
        let ptr = ah.join(crate::core::contract::pointer_file());
        if let Ok(text) = std::fs::read_to_string(&ptr) {
            if let Ok(pd) = serde_json::from_str::<serde_json::Value>(&text) {
                if sid.is_empty() {
                    sid = pd
                        .get("system_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
                if email.is_empty() {
                    email = pd
                        .get("email")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                }
            }
        }
    }
    if sid.is_empty() {
        let ah = agent_home
            .as_ref()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let (s, _plat) =
            crate::cmd::uninstall::resolve_system_id(&crate::core::home::expand_user(&ah), "");
        sid = s;
    }
    if sid.is_empty() {
        println!("✗ system_id unresolved (needs --system-id, or single-system local / platform pointer auto-detect)");
        return 1;
    }

    let config_path = systems.join(&sid).join("aimail_gateway.json");
    if !config_path.exists() {
        println!("✗ aimail_gateway.json not found: {}", config_path.display());
        return 1;
    }
    let cfg: serde_json::Value = std::fs::read_to_string(&config_path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| serde_json::json!({}));
    let gw_url = cfg
        .get("gateway_url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if email.is_empty() {
        email = main_agent_email(&cfg);
    }
    let manager = if !a.manager.is_empty() {
        a.manager.clone()
    } else {
        cfg.get("manager_address")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };

    // agent api_key (only accepts agent key; auth.local has no admin fallback)
    let agent_dir = crate::core::bridge_wire::addr_clean(&email);
    let agent_cfg = systems
        .join(&sid)
        .join(&agent_dir)
        .join(crate::core::contract::binding_file());
    let ak = std::fs::read_to_string(&agent_cfg)
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| {
            v.get("api_key")
                .and_then(|x| x.as_str())
                .map(|s| s.to_string())
        })
        .unwrap_or_default();
    if ak.is_empty() {
        println!(
            "✗ agent api_key not found (requires systems/{}/{}/{})——auth.local only accepts agent key",
            sid,
            agent_dir,
            crate::core::contract::binding_file()
        );
        return 1;
    }
    if gw_url.is_empty() || email.is_empty() || manager.is_empty() {
        println!("✗ Missing required config fields(gateway_url/admin_key/email/manager)");
        return 1;
    }

    let leaf = systems.join(&sid).join(&agent_dir);
    let mail_dir = leaf.join("mail");
    let log_path = leaf.join("agentmail.log");

    let edition = crate::core::smtp::detect_edition(&gw_url, "advanced");
    let ping_id = short_hex12();
    println!("  edition={} system_id={} email={}", edition, sid, email);

    let t_sent = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let mut resp = crate::core::smtp::send_ping(&gw_url, &ak, &email, &manager, &ping_id, &edition);
    if !resp.starts_with("250") && edition == "advanced" {
        let head: String = resp.chars().take(50).collect();
        println!(
            "  ⚠ auth.local send failed ({}), falling back to the base allowlist direct send",
            head
        );
        resp = crate::core::smtp::send_ping(&gw_url, &ak, &email, &manager, &ping_id, "base");
    }
    if !resp.starts_with("250") {
        println!("✗ SMTP ping send failed: {}", resp);
        return 1;
    }
    println!("  Ping sent: {}{}", crate::core::ping::PING_PREFIX, ping_id);

    // ── Three-stage event polling ──
    let mut w = crate::core::ping::Watcher::new(&ping_id, t_sent);
    let deadline = t_sent + a.timeout as f64;
    loop {
        if let Ok(text) = std::fs::read_to_string(&log_path) {
            w.observe(&text);
        }
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        if w.done() || now >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_secs(3));
    }
    for l in &w.out {
        println!("{}", l);
    }
    let (lines, ok) = w.verdict(a.timeout, &log_path.to_string_lossy());
    for l in lines {
        println!("{}", l);
    }
    if !ok {
        return 1;
    }

    if !a.no_snapshot {
        println!(
            "{}",
            crate::core::ping::snapshot_line(&mail_dir, &sid, &agent_dir)
        );
    }
    let _ = warn;
    let _ = fail;
    0
}

fn short_hex12() -> String {
    let seed = std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
        ^ (std::process::id() as u128);
    format!("{:012x}", seed & 0xffffffffffff)
}
