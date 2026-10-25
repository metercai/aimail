//! `aimail welcome` — the Rust re-implementation of `cli/send_welcome.py:main` (P3c finale).
//!
//! API mode (default): admin key calls `POST /api/v1/system/welcome` (server fixed cc/subject/body) →
//! poll the agent log for the reply → stage-3 identity approval (send `approve persona` **as the manager**).
//! SMTP mode (`--smtp`, legacy path): agent key + `auth.local` sends welcome email (advanced failure falls back to base whitelist direct send).
//!
//! Exit semantics (copied): cannot get draft ⇒ **2** (explicit failure, do not pretend success); send/wait failure ⇒ 1; success ⇒ 0.
//! the command surface only has `-s/-m/-w/--smtp/--timeout` (the script-internal switches `--to/--agent/--persona/--signature` **do not enter the surface** —
//! CLI parser is the contract surface, internal switches read directly from `sys.argv`).

use crate::cmd::report::fail;
use crate::core::welcome::{self, approve_message, welcome_message};

pub struct Args {
    pub system_id: String,
    pub manager: String,
    pub no_wait: bool,
    pub smtp: bool,
    pub reply_timeout: i64,
}

pub fn run(a: &Args) -> i32 {
    let systems = crate::core::config::systems_root_in(&crate::core::home::aimail_home());

    // Reply wait window (seconds): default 120; slow runner (e.g. arm64) uses --timeout to relax (10..=1800, out of bounds ⇒ default).
    let reply_timeout = if (10..=1800).contains(&a.reply_timeout) {
        a.reply_timeout
    } else {
        120
    };

    // ── Identity chain: AGENT_HOME pointer (no --agent-home on surface) → resolve_system_id ──
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
    // Recipient address: --to (not on surface) > pointer email > config derived
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
            println!(
                "✗ aimail_gateway.json has no admin_key — API mode needs the system admin key"
            );
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
        let (ok, reply_id, _to) = welcome::poll_reply(&recipient, reply_timeout);
        if ok {
            println!(
                "  ✓ Bidirectional send/receive verified (reply email_id={})",
                if reply_id.is_empty() { "?" } else { &reply_id }
            );
            return approve_identity(&gw_url, &admin_key, &recipient, &manager);
        }
        println!(
            "  ✗ No reply within {}s (log: {})",
            reply_timeout,
            welcome::agent_log_path_for(&recipient).display()
        );
        return 1;
    }

    // ── SMTP mode (legacy, --smtp explicit) ──
    if manager.is_empty() {
        println!(
            "✗ SMTP mode requires manager address (sender): --manager or config.manager_address"
        );
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
            "✗ agent api_key not found (requires systems/{}/{}/{})——auth.local only accepts agent key",
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
            "auth.local authentication"
        } else {
            "whitelist direct send"
        }
    );
    println!("  To:          {}", recipient);
    println!("  From:        {}", manager);

    let msg = welcome_message(&manager, &recipient);
    let mut resp = welcome::smtp_send(&gw_url, &ak, &recipient, &manager, &edition, &msg);
    if !resp.starts_with("250") && edition == "advanced" {
        let head: String = resp.chars().take(50).collect();
        println!(
            "  ⚠ auth.local send failed ({}), falling back to the base allowlist direct send",
            head
        );
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
    let (ok, email_id, _to) = welcome::poll_reply(&recipient, reply_timeout);
    if ok {
        println!(
            "  ✓ Bidirectional send/receive verified (email_id={})",
            if email_id.is_empty() { "?" } else { &email_id }
        );
        return approve_identity(&gw_url, &ak, &recipient, &manager);
    }
    println!(
        "  ✗ No reply within {}s (log: {})",
        reply_timeout,
        welcome::agent_log_path_for(&recipient).display()
    );
    1
}

/// 3rd stage: send approval email containing trigger words as manager (gateway only UPSERT writes persona/signature).
fn approve_identity(gw_url: &str, api_key: &str, recipient: &str, manager: &str) -> i32 {
    // No --persona/--signature on surface (script internal switches) ⇒ can only parse from reply snapshot
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
            "  ✗ persona/signature not obtained (reply snapshot has no 3-tag draft and none given explicitly) ⇒ provide via --persona/--signature"
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
        println!(
            "  ✓ Identity approval submitted (persona={}…, signature={}…)",
            p36, s28
        );
        return 0;
    }
    let head: String = resp.chars().take(80).collect();
    let _ = fail;
    println!("  ✗ Approval email send failed: {}", head);
    1
}
