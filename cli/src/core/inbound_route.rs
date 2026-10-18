//! hidden 入站面（`address --inbound-live/--inbound-down`）的引擎件：
//! `_withdraw_inbound_routes`（撤本系统全部路由行，幂等）、`_gateway_backlog_count`
//! （网关只读积压查询，best-effort）、`live_confirmed`（入站 up 的**确认 URL 反馈**，
//! 契约 §4.3，owner 2026-10-13）。
//!
//! 与 `bridge_wire` 同域（桥路由），但属"动作编排"层：[`bridge_wire`] 管词表/单行同步/对账，
//! 这里管 hidden 面需要的动作。只动 CLI 一侧，零 SDK 依赖。

use serde_json::{json, Map, Value};
use std::path::Path;

use crate::core::bridge_wire as bw;

/// 本 sid 名下的**已注册地址**（`<aimail_home>/systems/<sid>/*/<binding_file>` 的 `email`）。
///
/// 空集 = 本地没有该 sid 的绑定（未注册/已清）⇒ 调用方退化为域名作用域，
/// 保证"撤本系统全部路由行"在绑定缺失时仍能清干净（不留残留）。
fn sid_owned_emails(aimail_home: &Path, sid: &str) -> Vec<String> {
    let dir = crate::core::config::system_dir_in(aimail_home, sid);
    let mut out: Vec<String> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .filter_map(|sub| {
            let f = sub.join(crate::core::contract::binding_file());
            let text = std::fs::read_to_string(f).ok()?;
            let v: Value = serde_json::from_str(&text).ok()?;
            let e = v.get("email").and_then(Value::as_str)?;
            (!e.is_empty()).then(|| e.to_string())
        })
        .collect();
    out.sort();
    out
}

/// 路由行是否属于本 sid：域名作用域命中，且（能确定归属时）地址在该 sid 名下。
fn route_in_scope(email: &str, domain_hit: bool, owned: &[String]) -> bool {
    domain_hit && (owned.is_empty() || owned.iter().any(|e| e == email))
}

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
    let owned = sid_owned_emails(&crate::core::home::aimail_home(), sid);
    let would_be_decl = bw::load_declaration(cfg, bridge_cfg);
    let routes = bw::read_routes(&bw::routes_file());
    let pred = bw::system_route_scope(cfg);
    let mut withdrawn: usize = 0;
    let mut failed: usize = 0;
    let mut anchor_out: Option<Value> = None;
    for (email, target) in routes.iter() {
        let domain_hit = match &pred {
            Some(f) => f(email),
            None => false,
        };
        let in_scope = route_in_scope(email, domain_hit, &owned);
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

// ── 入站 up 的确认 URL 反馈（契约 §4.3，owner 2026-10-13）─────────────────────
//
// install 只落"最佳已知"入站态；入站 server 实际 listen 后端口才确定，SDK 逐地址回调
// `address --inbound-live --inbound-url <确认URL>`，把**确定**的入站端点写回路由层。
// 按 `webhook_host` 三态分派（单真源 = 系统配置）：
//   S1 键在=URL  ⇒ 外部桥 push：向该桥 admin 直接 upsert route（target=确认URL）；
//   S2 键在=空串  ⇒ 本机桥 pull：向本机桥 admin upsert route（同 API，127.0.0.1）；
//   S3 键缺席     ⇒ 直连 push：narrow 刷新网关 per-address webhook（只动 webhook 字段）。
// 返回与 `sync_route` 同形的 outcome（供 `finish_anchor` 渲染四态）。

/// S1 的桥 admin 端点解析：`webhook_host` = `http(s)://host:port`（无 path）⇒ `(host, port)`。
/// 解析不出 ⇒ `None`（调用方报 failed）。
fn s1_admin_from_webhook_host(webhook_host: &str) -> Option<(String, u16)> {
    let u = bw::parse_abs_url(webhook_host).ok()?;
    if !(u.scheme == "http" || u.scheme == "https") {
        return None;
    }
    if u.hostname.is_empty() {
        return None;
    }
    let port = u.port.unwrap_or(if u.scheme == "https" { 443 } else { 80 });
    Some((u.hostname, port))
}

/// S3 直连 push 的确认写回：narrow 刷新网关 per-address webhook（**只动 webhook 字段**）。
///
/// 载体 = `GET /api/v1/admin/systems/{sid}/domains`（system scope）取 `domain==email` 的
/// 记录 id，再 `PUT /api/v1/admin/system-domains/{id}` `{webhook_url, webhook_secret}`。
/// 选它而非"重发注册"：重发注册走 `create_domain` 的同系统 upsert，会**无条件**把
/// `domain_addr_meta` 的 `agent_signature`/`agent_persona` 刷成空（`storage.rs:951-952`），
/// 清掉 SDK 落盘的签名/人格；narrow PUT 只 UPDATE `webhook_url`/`webhook_secret`，
/// `is_active = COALESCE(NULL, is_active)` 不动（`storage.rs:677`），agent meta 不碰。
/// 与 TS SDK `syncAddressWebhook`（`auto-bind.ts:305`）同一载体。
fn s3_refresh_webhook(
    cfg: &Value,
    sid: &str,
    email: &str,
    confirmed_url: &str,
    webhook_secret: &str,
) -> Value {
    let out = outcome_base(bw::ACTION_LIVE, email, confirmed_url);
    let gw = cfg
        .get("gateway_url")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim_end_matches('/');
    let ak = cfg.get("admin_key").and_then(|v| v.as_str()).unwrap_or("");
    if gw.is_empty() || ak.is_empty() {
        return with_reason(
            out,
            bw::STATE_FAILED,
            "no gateway_url/admin_key in system config to refresh the address webhook",
        );
    }
    let client = crate::core::gateway::GatewayClient::with_defaults(gw, ak);
    // 1) 取 per-address 记录 id
    let list = client.get(&format!("/api/v1/admin/systems/{sid}/domains"));
    let arr = list.get("data").and_then(|v| v.as_array());
    let row = match arr.and_then(|a| {
        a.iter()
            .find(|r| r.get("domain").and_then(|v| v.as_str()) == Some(email))
    }) {
        Some(r) => r,
        None => {
            return with_reason(
                out,
                bw::STATE_FAILED,
                "address not found in the gateway domain list (register it first)",
            );
        }
    };
    let id = row.get("id").and_then(|v| v.as_str()).unwrap_or("");
    if id.is_empty() {
        return with_reason(out, bw::STATE_FAILED, "gateway domain row carries no id");
    }
    // 2) narrow PUT：只刷 webhook 字段（secret 与绑定同源；缺失则保留网关现值）
    let mut body = serde_json::Map::new();
    body.insert("webhook_url".into(), json!(confirmed_url));
    if !webhook_secret.is_empty() {
        body.insert("webhook_secret".into(), json!(webhook_secret));
    }
    let put = client.request(
        "PUT",
        &format!("/api/v1/admin/system-domains/{id}"),
        Some(&Value::Object(body)),
    );
    let status = put.get("status").and_then(|v| v.as_i64()).unwrap_or(0);
    if (200..300).contains(&status) {
        with_reason(
            out,
            bw::STATE_OK,
            "gateway address webhook refreshed (direct push)",
        )
    } else {
        let detail = put
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("gateway refresh failed");
        with_reason(out, bw::STATE_FAILED, detail)
    }
}

/// outcome 骨架（与 `sync_route` 同形，供 `finish_anchor`/`format_line` 渲染）。
fn outcome_base(action: &str, email: &str, target: &str) -> Value {
    let mut out = Map::new();
    out.insert("state".into(), json!(bw::STATE_FAILED));
    out.insert("action".into(), json!(action));
    out.insert("email".into(), json!(email));
    out.insert("target".into(), json!(target));
    out.insert("reason".into(), json!(""));
    out.insert("admin_port".into(), json!(bw::DEFAULT_ADMIN_PORT));
    out.insert("mode".into(), json!(""));
    out.insert("bind".into(), json!(""));
    out.insert("source".into(), json!(""));
    Value::Object(out)
}

fn with_reason(out: Value, state: &str, reason: &str) -> Value {
    let mut m = match out {
        Value::Object(m) => m,
        _ => Map::new(),
    };
    m.insert("state".into(), json!(state));
    m.insert("reason".into(), json!(reason));
    Value::Object(m)
}

/// 入站 up 的确认 URL 反馈（契约 §4.3）。**永不 panic**。
///
/// `confirmed_url` 为空 ⇒ `skipped`（回调没带确认端点，不动路由，说清原因）。
/// 非空但非绝对 http(s) URL ⇒ `failed`（不重建、不猜端口，同 `validate_target` 纪律）。
pub fn live_confirmed(
    sid: &str,
    email: &str,
    confirmed_url: &str,
    cfg: &Value,
    bridge_cfg: &Path,
) -> Value {
    let raw = confirmed_url.trim();
    if raw.is_empty() {
        return with_reason(
            outcome_base(bw::ACTION_LIVE, email, ""),
            bw::STATE_SKIPPED,
            "no --inbound-url given — nothing to confirm (run with the confirmed endpoint)",
        );
    }
    // 目标必须已是可投递的绝对 URL（复用 validate_target：拒绝裸 host:port、不补前缀）
    let (target, why) = bw::validate_target(&Value::String(raw.to_string()));
    if target.is_empty() {
        return with_reason(
            outcome_base(bw::ACTION_LIVE, email, raw),
            bw::STATE_FAILED,
            &why,
        );
    }

    let decl = bw::load_declaration(cfg, bridge_cfg);
    let webhook_host = cfg
        .get("webhook_host")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let key_absent = cfg.get("webhook_host").is_none();

    if key_absent {
        // S3 直连 push：narrow 刷新网关 per-address webhook
        let secret = binding_webhook_secret(sid, email);
        return s3_refresh_webhook(cfg, sid, email, &target, &secret);
    }

    // S1（URL）/ S2（空串）⇒ 桥 route upsert。admin 端点按态解析：
    let (admin_host, admin_port, src) = if !webhook_host.is_empty() {
        // S1 外部桥：webhook_host = http(s)://host:port
        match s1_admin_from_webhook_host(&webhook_host) {
            Some((h, p)) => (h, p, "external bridge (webhook_host)".to_string()),
            None => {
                return with_reason(
                    outcome_base(bw::ACTION_LIVE, email, &target),
                    bw::STATE_FAILED,
                    &format!("webhook_host '{webhook_host}' is not a usable http(s)://host:port"),
                );
            }
        }
    } else {
        // S2 本机桥：走声明（127.0.0.1 + 声明端口）
        if !decl.declared || !decl.usable {
            return with_reason(
                outcome_base(bw::ACTION_LIVE, email, &target),
                bw::STATE_FAILED,
                &format!("bridge not declared/usable for local pull: {}", decl.reason),
            );
        }
        (decl.admin_host, decl.admin_port, decl.source)
    };

    let (reachable, detail) = bw::admin_reachable(&admin_host, admin_port);
    let mut out = outcome_base(bw::ACTION_LIVE, email, &target);
    let mut m = match &out {
        Value::Object(m) => m.clone(),
        _ => Map::new(),
    };
    m.insert("admin_port".into(), json!(admin_port));
    m.insert("source".into(), json!(src));
    out = Value::Object(m);
    if !reachable {
        return with_reason(
            out,
            bw::STATE_FAILED,
            &format!("bridge declared ({src}) but {detail} — nothing changed"),
        );
    }
    let (ok, detail) = bw::upsert_route(email, &target, &admin_host, admin_port);
    if ok {
        with_reason(out, bw::STATE_OK, "route upserted to confirmed endpoint")
    } else {
        with_reason(
            out,
            bw::STATE_FAILED,
            &format!("could not upsert the route: {detail}"),
        )
    }
}

/// 绑定文件里的 `webhook_secret`（只读；S3 刷新与网关现值同源，缺失 ⇒ 空串保留网关值）。
fn binding_webhook_secret(sid: &str, email: &str) -> String {
    let p = crate::core::config::binding_file_path(sid, email);
    let Ok(text) = std::fs::read_to_string(&p) else {
        return String::new();
    };
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        return String::new();
    };
    v.get("webhook_secret")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .trim()
        .to_string()
}

#[cfg(test)]
mod tests {
    #[test]
    fn sid_scope_limits_to_owned_addresses_and_falls_back_when_unknown() {
        let owned = vec!["a@d.test".to_string()];
        assert!(route_in_scope("a@d.test", true, &owned));
        assert!(
            !route_in_scope("b@d.test", true, &owned),
            "别的 sid 的地址不得撤"
        );
        assert!(
            !route_in_scope("a@d.test", false, &owned),
            "域名不命中不得撤"
        );
        assert!(
            route_in_scope("x@d.test", true, &[]),
            "归属未知时退化为域名作用域"
        );
    }

    use super::*;

    fn state_of(out: &Value) -> &str {
        out.get("state").and_then(|v| v.as_str()).unwrap_or("")
    }
    fn reason_of(out: &Value) -> &str {
        out.get("reason").and_then(|v| v.as_str()).unwrap_or("")
    }
    fn nonexistent_bridge_cfg() -> std::path::PathBuf {
        std::path::PathBuf::from("/nonexistent/aimail_bridge.toml")
    }
    /// 确认入站 URL（路径经契约单真源取值，不落字面量 —— 字面量棘轮 rule (b)）。
    fn confirmed_url() -> String {
        format!(
            "http://127.0.0.1:9099{}",
            crate::core::contract::inbound_path()
        )
    }

    #[test]
    fn s1_admin_parses_http_host_port() {
        assert_eq!(
            s1_admin_from_webhook_host("http://203.0.113.7:38080"),
            Some(("203.0.113.7".to_string(), 38080))
        );
    }

    #[test]
    fn s1_admin_defaults_port_by_scheme() {
        // 无端口 ⇒ http→80 / https→443
        assert_eq!(
            s1_admin_from_webhook_host("http://203.0.113.7"),
            Some(("203.0.113.7".to_string(), 80))
        );
        assert_eq!(
            s1_admin_from_webhook_host("https://203.0.113.7"),
            Some(("203.0.113.7".to_string(), 443))
        );
    }

    #[test]
    fn s1_admin_rejects_schemeless_and_hostless() {
        // 裸 host:port / 无 scheme ⇒ 不可解析为 admin 端点
        assert_eq!(s1_admin_from_webhook_host("203.0.113.7:38080"), None);
        assert_eq!(s1_admin_from_webhook_host("ftp://203.0.113.7:38080"), None);
    }

    #[test]
    fn live_confirmed_empty_url_is_skipped() {
        let cfg = json!({});
        let out = live_confirmed("sid", "a@x.test", "   ", &cfg, &nonexistent_bridge_cfg());
        assert_eq!(state_of(&out), bw::STATE_SKIPPED);
    }

    #[test]
    fn live_confirmed_bare_hostport_is_failed() {
        // 裸 host:port 不可投递 ⇒ failed（不补前缀、不猜端口）
        let cfg = json!({});
        let out = live_confirmed(
            "sid",
            "a@x.test",
            "127.0.0.1:9099",
            &cfg,
            &nonexistent_bridge_cfg(),
        );
        assert_eq!(state_of(&out), bw::STATE_FAILED);
        assert!(reason_of(&out).contains("http(s)"));
    }

    #[test]
    fn live_confirmed_s3_without_gateway_is_failed() {
        // S3（键缺席）但系统配置无 gateway_url/admin_key ⇒ failed
        let cfg = json!({});
        let out = live_confirmed(
            "sid",
            "a@x.test",
            &confirmed_url(),
            &cfg,
            &nonexistent_bridge_cfg(),
        );
        assert_eq!(state_of(&out), bw::STATE_FAILED);
        assert!(reason_of(&out).contains("gateway_url"));
    }

    #[test]
    fn live_confirmed_s1_bridge_unreachable_is_failed() {
        // S1（webhook_host=url）外部桥不可达 ⇒ fail closed（不动路由）
        let cfg = json!({"webhook_host": "http://127.0.0.1:1"});
        let out = live_confirmed(
            "sid",
            "a@x.test",
            &confirmed_url(),
            &cfg,
            &nonexistent_bridge_cfg(),
        );
        assert_eq!(state_of(&out), bw::STATE_FAILED);
        assert!(reason_of(&out).contains("not reachable"));
    }

    #[test]
    fn live_confirmed_s2_bridge_not_declared_is_failed() {
        // S2（webhook_host=空串）但无声明桥 ⇒ failed（pull 必须走桥）
        let cfg = json!({"webhook_host": ""});
        let out = live_confirmed(
            "sid",
            "a@x.test",
            &confirmed_url(),
            &cfg,
            &nonexistent_bridge_cfg(),
        );
        assert_eq!(state_of(&out), bw::STATE_FAILED);
        assert!(reason_of(&out).contains("not declared"));
    }
}
