//! `aimail domain` — view/create the domain of a given system (P4 slice 1: **creation surface** landed).
//!
//! Creation surface (`--add`): validation happens **before action** (invalid domain fails directly, no request sent);
//! `id` omitted ⇒ `d{epoch}`; `-w/--webhook-url` included only if set; response contains `error` ⇒ failure message
//! `create failed: {error} {detail}` (`.strip()`).
//!
//! Current code reference: `cli/aimail:2923-2969` (`cmd_domain`) + `pysdk/aimail_tools.py:333-337`
//! (`list_system_domains`: GET `/api/v1/admin/systems/{sid}/domains`, non-list ⇒ empty table).

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
        return report::fail("domain requires --system-id");
    }
    let aimail_home = home::aimail_home();
    let cfg_path = config::gateway_config_path_in(&aimail_home, &sid);
    if !cfg_path.is_file() {
        return report::fail(&format!("no system config: {}", cfg_path.display()));
    }
    let cfg = match config::load_gateway_config_in(&aimail_home, &sid) {
        Some(c) => c,
        None => {
            return report::fail(&format!("failed to read config: {}", cfg_path.display()));
        }
    };
    let gw = cfg.gateway_url.trim_end_matches('/');
    if gw.is_empty() || cfg.admin_key.is_empty() {
        return report::fail(&format!("No gateway credentials (system {sid})"));
    }

    // ── Creation side (`--add`): validation before action (invalid domain no request sent) ──
    if let Some(raw_add) = args.add.as_deref() {
        let domain = raw_add.trim().to_lowercase();
        if domain.is_empty() || domain.contains('@') || !domain.contains('.') {
            // Message prints **original text** (Python uses args.add not normalized value)
            return report::fail(&format!("Invalid domain: '{}'", raw_add));
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
                "create failed: {} {}",
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
    // Replicate list_system_domains: if `data` exists take `data`, else whole payload; non-array ⇒ empty table
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
