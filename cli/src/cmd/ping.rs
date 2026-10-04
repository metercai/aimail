//! `aimail ping` —— `cli/ping_test.py` 的 Rust 复刻（P3b）。
//!
//! 流程：身份链（`--agent-home`/`AGENT_HOME` 下的**平台指针文件** → `resolve_system_id` → 读系统配置）→
//! 主 agent 地址（`default_agent_name` → 首个本地绑定 → 别名归一）→ agent api_key（**只认
//! agent key**，auth.local 是 1:1 语义、无 admin 回退）→ 探测 edition → SMTP 发送（advanced
//! 失败回落 base 白名单直发）→ **三阶段日志事件**轮询判定 → 快照检查。

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

/// 邮箱 local-part 的字符归一（`[^A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]` → `_`）。
fn sanitize_local(name: &str) -> String {
    let ok = |c: char| c.is_ascii_alphanumeric() || "!#$%&'*+-/=?^_`{|}~".contains(c);
    name.chars().map(|c| if ok(c) { c } else { '_' }).collect()
}

/// `email_for_agent`：共享域拼 `<name>.<system_name>@<domain>`。
fn email_for_agent(name: &str, domain: &str, system_name: &str) -> String {
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

/// `_main_agent_email`：显式默认名 → 首个本地已注册绑定 → 别名归一。
fn main_agent_email(cfg: &serde_json::Value) -> String {
    let dom = cfg.get("domain").and_then(|v| v.as_str()).unwrap_or("");
    let sysname = cfg
        .get("system_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let name = cfg
        .get("default_agent_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !name.is_empty() {
        return email_for_agent(if name == "agent" { "default" } else { name }, dom, sysname);
    }
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

    // ── 身份链：平台指针文件（--agent-home / AGENT_HOME）→ resolve_system_id ──
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
        println!("✗ system_id 未解析(需 --system-id,或本机单系统/平台指针可自动判定)");
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

    // agent api_key（只认 agent key；auth.local 无 admin 回退）
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
            "✗ agent api_key 未找到(需 systems/{}/{}/{})——auth.local 只接受 agent key",
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
        println!("  ⚠ auth.local 发送失败({}),回落 base 白名单直发", head);
        resp = crate::core::smtp::send_ping(&gw_url, &ak, &email, &manager, &ping_id, "base");
    }
    if !resp.starts_with("250") {
        println!("✗ SMTP ping send failed: {}", resp);
        return 1;
    }
    println!("  Ping sent: {}{}", crate::core::ping::PING_PREFIX, ping_id);

    // ── 三阶段事件轮询 ──
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
