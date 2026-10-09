//! `aimail renew` —— `cli/aimail:2974-3049` 的 Rust 复刻（P3a）。
//!
//! `--code` 给出 ⇒ `POST /api/v1/admin/renew-system`（叠加式：max(now,到期)+新码 validity）；
//! `--code` 省略或 `--status` ⇒ `GET /api/v1/quotas` 只读展示 expires_at/配额。
//!
//! **一处已登记分歧（不隐瞒）**：Python 的目标解析 `_resolve_renew_target` 在
//! "没有系统 / 多系统 / 指定系统不在本地" 三种情形下只 `_fail(...)` **不 return**（`_fail` 返回
//! 1 但不中断），于是继续用**空 admin_key + 生产默认网关 URL** 发请求 —— 会在真机上打到生产网关。
//! 我们打印**同样的 fail 文案**后 `rc=1` 返回，**不发网络**（生产边界：未授权不触生产）。
//! 文案逐字相同、流向相同（stdout），差别只在 rc 与"没有那次请求"。

use serde_json::json;

use crate::cmd::report::{fail, ok, warn};
use crate::core::gateway::GatewayClient;
use crate::core::pyjson::{repr_python, str_python};

pub struct Args {
    pub system_id: String,
    pub code: Option<String>,
    pub status: bool,
    pub gateway_url: Option<String>,
}

pub fn run(a: &Args) -> i32 {
    let (sid, gw_url, admin_key) = match resolve_target(a) {
        Ok(v) => v,
        Err(lines) => {
            for l in lines {
                fail(&l);
            }
            return 1; // 分歧点：Python 此处会继续并打生产网关（见文件头）
        }
    };
    let client = GatewayClient::with_defaults(&gw_url, &admin_key);

    let want_status = a.status || a.code.is_none();
    if want_status {
        let r = client.request("GET", "/api/v1/quotas", None);
        if !(r.is_object() && r.get("status").and_then(|v| v.as_i64()) == Some(200)) {
            fail(&format!("quotas query failed: {}", repr_python(&r)));
        }
        let exp_raw = r.get("expires_at").and_then(|v| v.as_str()).unwrap_or("");
        let exp = if exp_raw.is_empty() {
            "unlimited"
        } else {
            exp_raw
        };
        println!("  system {}", sid);
        println!(
            "    product: {} · validity: {} days",
            str_python(r.get("product_id")),
            str_python(r.get("validity_days"))
        );
        println!("    expires_at: {}", exp);
        // ≤3 天预警（与 stats 同规则）
        if !exp_raw.is_empty() {
            if let Some(ts) = crate::core::time::parse_rfc3339_secs(exp_raw) {
                let days = (ts - crate::core::time::now_secs()) / 86400;
                if days <= 3 {
                    warn(&format!(
                        "expires in {} day(s) — renew with: aimail renew --system-id {} --code <activation-code>",
                        days, sid
                    ));
                } else {
                    println!("    {} day(s) remaining", days);
                }
            }
        }
        println!(
            "    quotas: domains {} · addresses {} · daily {} · attachments {}",
            str_python(r.get("max_domains")),
            str_python(r.get("max_addresses")),
            str_python(r.get("max_daily_emails")),
            str_python(r.get("max_attachments"))
        );
        return 0;
    }

    let code = a.code.as_deref().unwrap_or("").trim().to_string();
    let shown: String = code.chars().take(12).collect();
    println!(
        "  renewing system {} with activation code {}...",
        sid, shown
    );
    let r = client.request(
        "POST",
        "/api/v1/admin/renew-system",
        Some(&json!({"code": code})),
    );
    let st = r.get("status");
    let st_ok = matches!(st, Some(serde_json::Value::Number(n)) if n.as_i64() == Some(200))
        || matches!(st, Some(serde_json::Value::String(s)) if s == "renewed" || s == "200");
    if !(r.is_object() && st_ok) {
        fail(&format!("renew failed: {}", repr_python(&r)));
    }
    ok(&format!(
        "renewed: expires_at {}",
        str_python(r.get("expires_at"))
    ));
    let q_empty = !r
        .get("quota")
        .map(|q| q.is_object() && !q.as_object().unwrap().is_empty())
        .unwrap_or(false);
    if !q_empty {
        let q = &r["quota"];
        println!(
            "    merged quotas: domains {} · addresses {} · daily {} · attachments {}",
            str_python(q.get("max_domains")),
            str_python(q.get("max_addresses")),
            str_python(q.get("max_daily_emails")),
            str_python(q.get("max_attachments"))
        );
    }
    println!(
        "    product: {} ({})",
        str_python(r.get("product_id")),
        str_python(r.get("product_name"))
    );
    0
}

/// `_resolve_renew_target`：`--system-id` 显式；省略时唯一系统自动选定、多系统报错列出。
fn resolve_target(a: &Args) -> Result<(String, String, String), Vec<String>> {
    use crate::core::config;
    // 候选 = SYSTEMS_DIR 下有配置文件的目录（sorted）
    let mut candidates: Vec<String> = Vec::new();
    let systems = config::systems_root_in(&crate::core::home::aimail_home());
    if systems.is_dir() {
        let mut dirs: Vec<_> = std::fs::read_dir(&systems)
            .map(|it| it.flatten().map(|e| e.path()).collect())
            .unwrap_or_default();
        dirs.sort();
        for d in dirs {
            if !d.is_dir() {
                continue;
            }
            let name = d
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_string();
            if config::gateway_config_path(&name).is_file() {
                candidates.push(name);
            }
        }
    }
    let mut errs: Vec<String> = Vec::new();
    let sid = if a.system_id.is_empty() {
        if candidates.is_empty() {
            errs.push("no aimail systems configured; use --system-id".to_string());
            String::new()
        } else if candidates.len() == 1 {
            candidates[0].clone()
        } else {
            errs.push(format!(
                "multiple systems installed: {}; use --system-id",
                candidates.join(", ")
            ));
            String::new()
        }
    } else if !candidates.contains(&a.system_id) {
        errs.push(format!(
            "system not found locally: {} (installed: {})",
            a.system_id,
            if candidates.is_empty() {
                "none".to_string()
            } else {
                candidates.join(", ")
            }
        ));
        a.system_id.clone()
    } else {
        a.system_id.clone()
    };
    if !errs.is_empty() {
        return Err(errs);
    }

    let cfg = config::load_gateway_config(&sid);
    let cfg_gw = cfg
        .as_ref()
        .map(|c| c.gateway_url.clone())
        .unwrap_or_default();
    let gw_url = a
        .gateway_url
        .clone()
        .filter(|s| !s.is_empty())
        .or_else(|| (!cfg_gw.is_empty()).then_some(cfg_gw))
        .or_else(|| {
            let e = crate::core::config::env_val("AIMAIL_GW_URL", "");
            (!e.is_empty()).then_some(e)
        })
        .unwrap_or_else(|| "https://aimail.token.tm".to_string());
    let admin_key = cfg
        .as_ref()
        .map(|c| c.admin_key.clone())
        .unwrap_or_default();
    if admin_key.is_empty() {
        return Err(vec![format!(
            "no admin_key in aimail_gateway.json for {}",
            sid
        )]);
    }
    Ok((sid, gw_url, admin_key))
}
