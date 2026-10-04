//! hidden 入站面（`address --inbound-live/--inbound-down`）的引擎件：
//! `_withdraw_inbound_routes`（撤本系统全部路由行，幂等）与 `_gateway_backlog_count`
//! （网关只读积压查询，best-effort）。
//!
//! 与 `bridge_wire` 同域（桥路由），但属"动作编排"层：[`bridge_wire`] 管词表/单行同步/对账，
//! 这里管 hidden 面需要的两个动作。只动 CLI 一侧，零 SDK 依赖。

use serde_json::{json, Map, Value};
use std::path::Path;

use crate::core::bridge_wire as bw;

/// `_withdraw_inbound_routes(sid, cfg)`：撤 **本系统** 全部路由行（幂等）。
///
/// 返回 `(withdrawn, failed, no_bridge, anchor_out)`：
/// · `no_bridge` ⇒ 未声明桥（调用方应静默、不改 rc）；
/// · `anchor_out` ⇒ 锚点那行的结果（供调用方渲染；锚点不在表里则为 `None`）。
pub fn withdraw_inbound_routes(
    sid: &str,
    cfg: &Value,
    bridge_cfg: &Path,
    anchor_email: &str,
) -> (usize, usize, bool, Option<Value>) {
    let _ = sid;
    let would_be_decl = bw::load_declaration(cfg, bridge_cfg);
    let routes = bw::read_routes(&bw::routes_file());
    let pred = bw::system_route_scope(cfg);
    let mut withdrawn: usize = 0;
    let mut failed: usize = 0;
    let mut anchor_out: Option<Value> = None;
    for (email, target) in routes.iter() {
        let in_scope = match &pred {
            Some(f) => f(email),
            None => false,
        };
        if !in_scope {
            continue;
        }
        let out = bw::sync_route(
            bw::ACTION_DOWN,
            email,
            &Value::String(target.clone()),
            cfg,
            bridge_cfg,
            bw::ADMIN_HOST,
        );
        let state = out.get("state").and_then(|v| v.as_str()).unwrap_or("");
        match state {
            "ok" => withdrawn += 1,
            "failed" => failed += 1,
            "no_bridge" => {
                if email == anchor_email {
                    anchor_out = Some(out.clone());
                }
                return (withdrawn, failed, true, anchor_out);
            }
            _ => {}
        }
        if email == anchor_email {
            anchor_out = Some(out.clone());
        }
    }
    let _ = would_be_decl;
    (withdrawn, failed, false, anchor_out)
}

/// `_gateway_backlog_count(cfg, emails)`：网关侧待投递件数（**只读**，best-effort）。
///
/// `POST /api/v1/admin/pending`，body `{"filter": [], "emails": [...]}`，逐桶累加 `deliveries`。
/// 缺 gw/ak/emails、响应非 2xx、结构异常 ⇒ `None`（调用方据此打 `; backlog unknown`），**绝不阻塞**。
pub fn gateway_backlog_count(cfg: &Value, emails: &[String]) -> Option<usize> {
    let gw = cfg
        .get("gateway_url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim_end_matches('/')
        .to_string();
    let ak = cfg
        .get("admin_key")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if gw.is_empty() || ak.is_empty() || emails.is_empty() {
        return None;
    }
    let body = json!({"filter": [], "emails": emails});
    let client = crate::core::gateway::GatewayClient::with_defaults(&gw, &ak);
    let res = client.post("/api/v1/admin/pending", &body);
    if res.get("status").and_then(|v| v.as_i64()).unwrap_or(0) != 200 {
        return None;
    }
    let batches = res.get("batches")?.as_array()?;
    let mut n = 0usize;
    for b in batches {
        n += b
            .get("deliveries")
            .and_then(|d| d.as_array())
            .map(|a| a.len())
            .unwrap_or(0);
    }
    Some(n)
}

/// `_finish_anchor(out)`：锚点结果的四态出口（**照抄** Python 的 fail-closed 语义）。
pub fn finish_anchor(out: Option<&Value>) -> i32 {
    use crate::cmd::report::{fail, ok, warn};
    let Some(o) = out else {
        return 0; // 锚点不在表里 ⇒ 无事件可播报
    };
    let state = o.get("state").and_then(|v| v.as_str()).unwrap_or("");
    let line = bw::format_line(o);
    match state {
        "no_bridge" => 0,
        "failed" => fail(&line),
        "skipped" => {
            warn(&line);
            0
        }
        "ok" => {
            ok(&line);
            0
        }
        _ => {
            warn(&line);
            0
        }
    }
}

/// 锚点是 pull（空 webhook）时的合成事件：`skipped` —— 幂等无动作。
pub fn skipped_pull_outcome(email: &str) -> Value {
    let mut m = Map::new();
    m.insert("state".into(), json!("skipped"));
    m.insert("action".into(), json!(bw::ACTION_LIVE));
    m.insert("email".into(), json!(email));
    m.insert(
        "reason".into(),
        json!("the binding carries no webhook_url (pull mode: the gateway routes to the agent directly) — nothing to do"),
    );
    Value::Object(m)
}

/// 锚点行不在路由表时的合成事件：`ok` / `route already absent` —— 幂等。
pub fn already_absent_outcome(email: &str) -> Value {
    let mut m = Map::new();
    m.insert("state".into(), json!("ok"));
    m.insert("action".into(), json!(bw::ACTION_DOWN));
    m.insert("email".into(), json!(email));
    m.insert("reason".into(), json!("route already absent"));
    Value::Object(m)
}
