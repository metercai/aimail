//! `aimail welcome` —— `cli/send_welcome.py:main` 的 Rust 复刻（P3c 收官）。
//!
//! API 模式（默认）：admin key 调 `POST /api/v1/system/welcome`（服务器固定 cc/subject/body）→
//! 轮询 agent 日志等回复 → 第 3 段身份审批（以 **manager 身份**发 `approve persona`）。
//! SMTP 模式（`--smtp`，旧路）：agent key + `auth.local` 发 welcome 信（advanced 失败回落 base 白名单直发）。
//!
//! 出口语义（照抄）：拿不到草案 ⇒ **2**（明确失败、不假装成功）；发送/等待失败 ⇒ 1；成功 ⇒ 0。
//! 命令面只有 `-s/-m/-w/--smtp`（脚本内部开关 `--to/--agent/--persona/--signature` **不进面**——
//! CLI 解析器才是契约面，内部开关从 `sys.argv` 直接读）。

use crate::cmd::report::fail;
use crate::core::welcome::{self, approve_message, welcome_message};

pub struct Args {
    pub system_id: String,
    pub manager: String,
    pub no_wait: bool,
    pub smtp: bool,
}

pub fn run(a: &Args) -> i32 {
    let systems = crate::core::config::systems_root_in(&crate::core::home::aimail_home());

    // ── 身份链：AGENT_HOME 指针（面上无 --agent-home）→ resolve_system_id ──
    let mut sid = a.system_id.clone();
    let mut email = String::new();
    let agent_home = std::env::var("AGENT_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(|s| crate::core::home::expand_user(&s));
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
                email = pd
                    .get("email")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
            }
        }
    }
    if sid.is_empty() {
        let ah = agent_home
            .as_ref()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let (s, _plat) = crate::cmd::uninstall::resolve_system_id(std::path::Path::new(&ah), "");
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
    let cfg_str = |k: &str| {
        cfg.get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    let gw_url = cfg_str("gateway_url");
    let manager = if !a.manager.is_empty() {
        a.manager.clone()
    } else {
        let e1 = crate::core::config::env_val("AIMAIL_MANAGER_ADDRESS", "");
        let e2 = crate::core::config::env_val("MANAGER", "");
        if !e1.is_empty() {
            e1
        } else if !e2.is_empty() {
            e2
        } else {
            cfg_str("manager_address")
        }
    };
    // 收件地址：--to（不在面）> 指针 email > config 派生
    let recipient = if !email.is_empty() {
        email
    } else {
        crate::cmd::ping::main_agent_email(&cfg)
    };
    if gw_url.is_empty() {
        println!("✗ Missing gateway_url");
        return 1;
    }
    let admin_key = cfg_str("admin_key");

    if !a.smtp {
        if admin_key.is_empty() {
            println!("✗ aimail_gateway.json 无 admin_key——API 模式需系统 admin key");
            return 1;
        }
        println!("  Gateway:     {}", gw_url);
        println!("  Mode:        API (system welcome, from the gateway system sender)");
        println!("  To:          {}", recipient);
        println!("  Cc:          (server-resolved from agent manager_address)");

        let (ok, email_id, msg_id, err) =
            welcome::api_send(&gw_url, &admin_key, &recipient, &cfg_str("system_id"));
        if !ok {
            println!("✗ Welcome API send failed: {}", err);
            return 1;
        }
        println!(
            "  ✓ Welcome email sent via API (email_id={}, message_id={})",
            if email_id.is_empty() { "?" } else { &email_id },
            if msg_id.is_empty() { "?" } else { &msg_id }
        );
        if a.no_wait {
            return 0;
        }
        let (ok, reply_id, _to) = welcome::poll_reply(&recipient, 120);
        if ok {
            println!(
                "  ✓ Bidirectional send/receive verified (reply email_id={})",
                if reply_id.is_empty() { "?" } else { &reply_id }
            );
            return approve_identity(&gw_url, &admin_key, &recipient, &manager);
        }
        println!(
            "  ✗ No reply within 120s (log: {})",
            welcome::agent_log_path_for(&recipient).display()
        );
        return 1;
    }

    // ── SMTP 模式（旧，--smtp 显式）──
    if manager.is_empty() {
        println!("✗ SMTP 模式需 manager 地址(发件人): --manager 或 config.manager_address");
        return 1;
    }
    let agent_dir = crate::core::bridge_wire::addr_clean(&recipient);
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
    let edition = crate::core::smtp::detect_edition(&gw_url, "base");
    println!("  Gateway:     {}", gw_url);
    println!("  Mode:        SMTP");
    println!(
        "  Edition:     {}({})",
        edition,
        if edition == "advanced" {
            "auth.local 认证"
        } else {
            "白名单直发"
        }
    );
    println!("  To:          {}", recipient);
    println!("  From:        {}", manager);

    let msg = welcome_message(&manager, &recipient);
    let mut resp = welcome::smtp_send(&gw_url, &ak, &recipient, &manager, &edition, &msg);
    if !resp.starts_with("250") && edition == "advanced" {
        let head: String = resp.chars().take(50).collect();
        println!("  ⚠ auth.local 发送失败({}),回落 base 白名单直发", head);
        resp = welcome::smtp_send(&gw_url, &ak, &recipient, &manager, "base", &msg);
    }
    if !resp.starts_with("250") {
        println!("✗ SMTP send failed: {}", resp);
        return 1;
    }
    println!("  ✓ Welcome email sent via SMTP");
    if a.no_wait {
        return 0;
    }
    let (ok, email_id, _to) = welcome::poll_reply(&recipient, 120);
    if ok {
        println!(
            "  ✓ Bidirectional send/receive verified (email_id={})",
            if email_id.is_empty() { "?" } else { &email_id }
        );
        return approve_identity(&gw_url, &ak, &recipient, &manager);
    }
    println!(
        "  ✗ No reply within 120s (log: {})",
        welcome::agent_log_path_for(&recipient).display()
    );
    1
}

/// 第 3 段：以 manager 身份发含触发词的审批邮件（网关唯一 UPSERT 写 persona/signature）。
fn approve_identity(gw_url: &str, api_key: &str, recipient: &str, manager: &str) -> i32 {
    // 面上无 --persona/--signature（脚本内部开关）⇒ 只能从回复快照解析
    let got = welcome::parse_draft_from_reply(recipient);
    let per = got
        .get("persona")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let sig = got
        .get("signature")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if per.is_empty() || sig.is_empty() {
        println!(
            "  ✗ 未取得 persona/signature(回复快照无三标签草案且未显式给出)⇒ 请用 --persona/--signature 提供"
        );
        return 2;
    }
    let edition = crate::core::smtp::detect_edition(gw_url, "base");
    let msg = approve_message(manager, recipient, &per, &sig);
    let mut resp = welcome::smtp_send(gw_url, api_key, recipient, manager, &edition, &msg);
    if !resp.starts_with("250") && edition == "advanced" {
        resp = welcome::smtp_send(gw_url, api_key, recipient, manager, "base", &msg);
    }
    if resp.starts_with("250") {
        let p36: String = per.chars().take(36).collect();
        let s28: String = sig.chars().take(28).collect();
        println!("  ✓ 身份审批已提交(persona={}…, signature={}…)", p36, s28);
        return 0;
    }
    let head: String = resp.chars().take(80).collect();
    let _ = fail;
    println!("  ✗ 审批邮件发送失败: {}", head);
    1
}
