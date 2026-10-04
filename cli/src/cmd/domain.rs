//! `aimail domain` —— 查看/创建指定系统的域名（P4 切片1：**创建面**落地）。
//!
//! 创建面（`--add`）：校验在**动作之前**（非法域名直接失败、不发请求）；
//! `id` 省略 ⇒ `d{epoch}`；`-w/--webhook-url` 有值才带；响应带 `error` ⇒ 失败文案
//! `创建失败: {error} {detail}`（`.strip()`）。
//!
//! 现状码对照：`cli/aimail:2923-2969`（`cmd_domain`）+ `pysdk/aimail_tools.py:333-337`
//! （`list_system_domains`：GET `/api/v1/admin/systems/{sid}/domains`，非 list ⇒ 空表）。

use crate::cmd::report;
use crate::core::{config, gateway::GatewayClient, home};
use serde_json::Value;

pub struct Args {
    pub system_id: String,
    pub add: Option<String>,
    pub id: String,
    pub webhook_url: String,
}

pub fn run(args: Args) -> i32 {
    let sid = args.system_id;
    if sid.is_empty() {
        return report::fail("domain 需要 --system-id");
    }
    let aimail_home = home::aimail_home();
    let cfg_path = config::gateway_config_path_in(&aimail_home, &sid);
    if !cfg_path.is_file() {
        return report::fail(&format!("无系统配置: {}", cfg_path.display()));
    }
    let cfg = match config::load_gateway_config_in(&aimail_home, &sid) {
        Some(c) => c,
        None => {
            return report::fail(&format!("配置读取失败: {}", cfg_path.display()));
        }
    };
    let gw = cfg.gateway_url.trim_end_matches('/');
    if gw.is_empty() || cfg.admin_key.is_empty() {
        return report::fail(&format!("无网关凭据(system {sid})"));
    }

    // ── 创建面（`--add`）：校验在动作之前（非法域名不发请求）──
    if let Some(raw_add) = args.add.as_deref() {
        let domain = raw_add.trim().to_lowercase();
        if domain.is_empty() || domain.contains('@') || !domain.contains('.') {
            // 文案打**原文**（Python 用 args.add 而非归一后的值）
            return report::fail(&format!("非法域名: '{}'", raw_add));
        }
        let id = if args.id.is_empty() {
            format!("d{}", crate::core::time::now_secs())
        } else {
            args.id.clone()
        };
        let mut body = serde_json::json!({ "id": id, "domain": domain });
        if !args.webhook_url.is_empty() {
            body["webhook_url"] = Value::String(args.webhook_url.clone());
        }
        let client = GatewayClient::with_defaults(gw, &cfg.admin_key);
        let result = client.post(&format!("/api/v1/admin/systems/{sid}/domains"), &body);
        if let Some(err) = result.get("error").filter(|v| !v.is_null()) {
            let detail = result.get("detail").and_then(|v| v.as_str()).unwrap_or("");
            let msg = format!(
                "创建失败: {} {}",
                crate::core::pyjson::str_python(Some(err)),
                detail
            );
            return report::fail(msg.trim());
        }
        report::ok(&format!("domain created: {} (system {})", domain, sid));
        return 0;
    }

    let client = GatewayClient::with_defaults(gw, &cfg.admin_key);
    let result = client.get(&format!("/api/v1/admin/systems/{sid}/domains"));
    // 复刻 list_system_domains：有 `data` 取 `data`，否则整份；非数组 ⇒ 空表
    let payload = match result.get("data") {
        Some(d) => d.clone(),
        None => result,
    };
    let items: Vec<Value> = payload.as_array().cloned().unwrap_or_default();

    if items.is_empty() {
        println!("  no domains");
        return 0;
    }
    println!("  Domains of system {sid}:");
    for d in items {
        let addr = d
            .get("domain")
            .or_else(|| d.get("domain_addr"))
            .and_then(Value::as_str)
            .unwrap_or("?");
        let active = d.get("is_active").and_then(Value::as_bool).unwrap_or(true);
        let webhook = d.get("webhook_url").and_then(Value::as_str).unwrap_or("");
        let mut line = format!("    {addr}  {}", if active { "active" } else { "inactive" });
        if !webhook.is_empty() {
            line.push_str(&format!("  webhook={webhook}"));
        }
        println!("{line}");
    }
    0
}
