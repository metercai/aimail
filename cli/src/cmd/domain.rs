//! `aimail domain` —— 查看指定系统的域名。S3 只移植**列表**面。
//!
//! `--add`（创建域名）与其参数校验留下一刀 ⇒ 本步显式走 not-yet-ported（rc 1），
//! 不做"先校验再假成功"。
//!
//! 现状码对照：`cli/aimail:2923-2969`（`cmd_domain`）+ `pysdk/aimail_tools.py:333-337`
//! （`list_system_domains`：GET `/api/v1/admin/systems/{sid}/domains`，非 list ⇒ 空表）。

use crate::cmd::report;
use crate::core::{config, gateway::GatewayClient, home};
use serde_json::Value;

pub struct Args {
    pub system_id: String,
    pub add: Option<String>,
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

    // 创建面未移植 ⇒ 在校验**之后**显式非零退出（顺序与 Python 一致：先校验再动作，
    // 这样"机器上没有该系统"这类判读与 Python 逐字相同；且绝不产生任何写入）。
    if args.add.is_some() {
        return crate::cmd::stub::not_yet_ported("domain --add");
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
