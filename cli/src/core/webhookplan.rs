//! 入站 webhook 判定 —— 系统级 install 的**单一判定函数**（owner 2026-10-13 定稿）。
//!
//! 输入（env + 本机事实 + 两个探测端点的分类结果，全部由调用方采集后注入）：
//! - `gw_reachable`  前置：GW 从本机可达（否 ⇒ 所有态依赖 GW，直接 Fail）；
//! - `bridge_host`   `AIMAIL_BRIDGE_HOST`（`http(s)://host:port`，无 path；缺省/无效 ⇒ None）；
//! - `probe_gw_to_bridge`     probe1：GW 视角探 BRIDGE_HOST（桥服务端点）；
//! - `probe_bridge_to_agent`  probe2：桥视角探 agent 入站目标（第二跳）；
//! - `gw_is_local`  L：GW 解析 IP ∈ 本机列表；
//! - `probe_gw_to_agent` R：GW 视角探 AGENT_HOST（直连入站端点）；
//! - `bridge_deployable` BR：本机桥可部署。
//!
//! 判定（owner 裁定逐字）：
//! ```text
//! B = probe1(桥服务级: connected) ∧ probe2(路径级: connected ∨ connection_refused)
//!     —— 任一缺失或无效 ⇒ B=false（BRIDGE_HOST 降级为不存在）
//! B                       ⇒ S1 经桥 push   webhook_host = BRIDGE_HOST
//! ¬B ∧ (L ∨ R)            ⇒ S3 直连 push   webhook_host 键缺席
//! ¬B ∧ ¬L ∧ ¬R ∧ BR       ⇒ S2 桥 pull     webhook_host = ""（空串）
//! ¬B ∧ ¬L ∧ ¬R ∧ ¬BR      ⇒ Fail 需桥而不可部署（大声失败，无静默降级，非 S4）
//! ```
//!
//! **纯函数**：探测在调用方执行（`POST /api/v1/admin/probe-webhook`、桥
//! `POST /api/v1/probe`），本模块只解释分类结果并落态。两条解释语义不同、不可混用：
//! - 桥**服务**端点（probe1）：只有 `connected` = 活（refused = 桥没起 = 死）；
//! - agent **入站**端点（probe2 / R）：`connection_refused` = 路径通（agent 进程
//!   install 后才起，端口未监听是预期的）= 可用。

use crate::core::netenv::ProbeResult;

/// 判定结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WebhookPlan {
    /// S1 经桥 push：`webhook_host = BRIDGE_HOST`（`http(s)://host:port`，无 path；
    /// GW 填到桥，桥转发时才拼上 agent 真实入站 path）。
    BridgePush { bridge_host: String },
    /// S2 桥 pull：`webhook_host = ""`（空串；GW 侧 pending，桥长轮询取并 push agent）。
    BridgePull,
    /// S3 直连 push：`webhook_host` 键缺席（GW 直接 push agent 入站端点，自带 path）。
    Direct,
    /// install 大声失败（需桥而不可部署 / GW 不可达）。
    Fail { reason: String },
}

impl WebhookPlan {
    /// webhook_host 的三值（owner 裁定：S1 URL / S2 空串 / S3 键缺席）。
    /// `Fail` 时返回 `None`（不应落盘）。
    pub fn webhook_host(&self) -> Option<String> {
        match self {
            WebhookPlan::BridgePush { bridge_host } => Some(bridge_host.clone()),
            WebhookPlan::BridgePull => Some(String::new()),
            WebhookPlan::Direct => None, // 键缺席
            WebhookPlan::Fail { .. } => None,
        }
    }

    pub fn is_fail(&self) -> bool {
        matches!(self, WebhookPlan::Fail { .. })
    }
}

/// 判定输入（全部由调用方采集后注入）。
#[derive(Debug, Clone)]
pub struct PlanInputs {
    /// 前置：GW 从本机可达。
    pub gw_reachable: bool,
    /// `AIMAIL_BRIDGE_HOST`（解析成功；缺省/无效 ⇒ None）。
    pub bridge_host: Option<String>,
    /// probe1：GW 视角探 BRIDGE_HOST（无 bridge_host ⇒ None）。
    pub probe_gw_to_bridge: Option<ProbeResult>,
    /// probe2：桥视角探 agent 入站目标（无 bridge_host ⇒ None）。
    pub probe_bridge_to_agent: Option<ProbeResult>,
    /// L：GW 解析 IP ∈ 本机列表（GW 在本机）。
    pub gw_is_local: bool,
    /// R：GW 视角探 AGENT_HOST（无有效 AGENT_HOST ⇒ None）。
    pub probe_gw_to_agent: Option<ProbeResult>,
    /// BR：本机桥可部署。
    pub bridge_deployable: bool,
}

/// 桥**服务**端点解释：只有 `connected` = 活。
fn bridge_service_alive(p: &ProbeResult) -> bool {
    p.reachable
}

/// agent **入站**端点解释（路径级）：`connected` ∨ `connection_refused` = 路径通。
fn inbound_path_open(p: &ProbeResult) -> bool {
    p.reachable || p.error == "connection_refused"
}

/// 单一判定函数。
pub fn plan(i: &PlanInputs) -> WebhookPlan {
    // 前置：所有态依赖 GW。
    if !i.gw_reachable {
        return WebhookPlan::Fail {
            reason: "gateway is not reachable from this machine".into(),
        };
    }

    // B = probe1(桥活) ∧ probe2(路径通)；任一缺失/无效 ⇒ false（降级）。
    let b = match (i.probe_gw_to_bridge.as_ref(), i.probe_bridge_to_agent.as_ref()) {
        (Some(p1), Some(p2)) => bridge_service_alive(p1) && inbound_path_open(p2),
        _ => false,
    };
    if b {
        // bridge_host 必然在场（否则 probe 不会是 Some）。
        let host = i
            .bridge_host
            .clone()
            .unwrap_or_else(|| unreachable!("B true requires bridge_host"));
        return WebhookPlan::BridgePush { bridge_host: host };
    }

    // R：直连入站路径通（路径级）。
    let r = i.probe_gw_to_agent.as_ref().map_or(false, inbound_path_open);
    if i.gw_is_local || r {
        return WebhookPlan::Direct;
    }

    // 需桥而直连不可。
    if i.bridge_deployable {
        WebhookPlan::BridgePull
    } else {
        WebhookPlan::Fail {
            reason: "gateway is remote and the agent inbound endpoint is not reachable \
                     from it, and no local bridge is deployable — an inbound bridge is \
                     required (set AIMAIL_BRIDGE_HOST, or make the local bridge deployable)"
                .into(),
        }
    }
}

// ── 采集层（install 调用；探测走既有端点，分类结果解释在上方纯函数）──────────────

/// 解析一个探测端点的 JSON 应答 → `ProbeResult`；缺字段/非对象 ⇒ `None`（视为未探测）。
pub fn parse_probe(v: &serde_json::Value) -> Option<ProbeResult> {
    let o = v.as_object()?;
    let reachable = o.get("reachable")?.as_bool()?;
    let error = o
        .get("error")
        .and_then(|e| e.as_str())
        .unwrap_or("unknown")
        .to_string();
    Some(ProbeResult { reachable, error })
}

/// GW 视角探一个 `host:port`（`POST /api/v1/admin/probe-webhook`，system-scope key）。
/// GW 不可达/未认证/非对象 ⇒ `None`（调用方按"未探测"降级）。
pub fn probe_gw(gw_url: &str, admin_key: &str, addr: &str) -> Option<ProbeResult> {
    if admin_key.is_empty() {
        return None;
    }
    let base = gw_url.trim_end_matches('/');
    let path = "/api/v1/admin/probe-webhook";
    let body = serde_json::json!({ "addr": addr }).to_string();
    let headers = crate::core::sig::signed_headers(admin_key, "POST", path, Some(body.as_bytes()), "");
    let (code, resp) = crate::core::http::json_req(
        &format!("{base}{path}"),
        &headers,
        Some(body.as_bytes()),
        Some("POST"),
        8,
    );
    if !(200..300).contains(&code) {
        return None;
    }
    parse_probe(&resp)
}

/// 桥视角探一个 `host:port`（桥 `POST /api/v1/probe`，admin 白名单内）。
/// 桥不可达/非对象 ⇒ `None`。
pub fn probe_bridge(admin_host: &str, admin_port: u16, addr: &str) -> Option<ProbeResult> {
    let body = serde_json::json!({ "addr": addr }).to_string();
    let (code, body_text) =
        crate::core::http::raw_req_method("POST", &format!("http://{admin_host}:{admin_port}/api/v1/probe"), Some(body.as_bytes()), Some("application/json"), 8).ok()?;
    if !(200..300).contains(&code) {
        return None;
    }
    parse_probe(&serde_json::from_str(&body_text).ok()?)
}

/// `AIMAIL_BRIDGE_HOST` / `AIMAIL_BRIDGE_ADMIN` / `AIMAIL_AGENT_HOST` env → 解析。
pub fn env_bridge_host() -> Option<String> {
    let raw = crate::core::config::env_val("AIMAIL_BRIDGE_HOST", "");
    if raw.trim().is_empty() {
        return None;
    }
    crate::core::netenv::parse_bridge_host(&raw).map(|_| raw.trim().to_string())
}

/// 外部桥 admin 端点（`AIMAIL_BRIDGE_ADMIN` 优先，缺省回退 BRIDGE_HOST 的 host:port）。
/// 外部桥 admin 与数据端口可能不同，故允许显式 env 覆盖。
pub fn env_bridge_admin() -> Option<(String, u16)> {
    let raw = crate::core::config::env_val("AIMAIL_BRIDGE_ADMIN", "");
    if !raw.trim().is_empty() {
        if let Some(hp) = crate::core::netenv::parse_bridge_host(raw.trim()) {
            return Some(hp);
        }
    }
    env_bridge_host().and_then(|h| crate::core::netenv::parse_bridge_host(&h))
}

pub fn env_agent_host() -> Option<String> {
    let raw = crate::core::config::env_val("AIMAIL_AGENT_HOST", "");
    if raw.trim().is_empty() {
        return None;
    }
    if !crate::core::netenv::agent_host_valid(raw.trim()) {
        // owner：不是公网也不是本机 IP ⇒ 视为不存在
        return None;
    }
    Some(raw.trim().to_string())
}

/// install 的入站模式判定：采集 env + 本机事实 + 探测，跑 `plan`。
/// `gw_reachable` 由 GW admin 端点应答判定（带 system key 的 GET /admin/systems）。
pub fn plan_inbound(gw_url: &str, admin_key: &str) -> WebhookPlan {
    let gw_reachable = gw_admin_reachable(gw_url, admin_key);
    let bridge_host = env_bridge_host();
    let agent_host = env_agent_host();

    // probe1：GW 视角探桥服务（桥活 = connected）。
    let probe_gw_to_bridge = bridge_host.as_ref().and_then(|h| {
        crate::core::netenv::parse_bridge_host(h)
            .map(|(host, port)| probe_gw(gw_url, admin_key, &format!("{host}:{port}")))
            .flatten()
    });
    // probe2：桥视角探 agent 入站（第二跳）。仅当 bridge+agent 都在场才跑。
    // 探测端点 = 该桥自己的 admin（S1 外部桥 / S2 本机桥），不是 GW。
    let probe_bridge_to_agent = if let (Some(ah), Some((badmin, bport))) =
        (&agent_host, env_bridge_admin())
    {
        crate::core::netenv::parse_hostport(ah)
            .map(|(ahost, aport)| probe_bridge(&badmin, bport, &format!("{ahost}:{aport}")))
            .flatten()
    } else {
        None
    };
    // R：GW 视角探 agent 入站（路径级）。
    let probe_gw_to_agent = agent_host.as_ref().and_then(|ah| {
        crate::core::netenv::parse_hostport(ah)
            .map(|(host, port)| probe_gw(gw_url, admin_key, &format!("{host}:{port}")))
            .flatten()
    });
    let gw_is_local = crate::core::gateway::is_local_gateway(gw_url);
    let bridge_deployable = crate::core::bridge_deploy::bridge_deployable();

    plan(&PlanInputs {
        gw_reachable,
        bridge_host,
        probe_gw_to_bridge,
        probe_bridge_to_agent,
        gw_is_local,
        probe_gw_to_agent,
        bridge_deployable,
    })
}

/// GW admin 端点可达性（带 system key 的 GET；2xx = 可达）。
pub fn gw_admin_reachable(gw_url: &str, admin_key: &str) -> bool {
    if admin_key.is_empty() {
        // 无 key（如复用路径未取到）⇒ 退化为 TCP 可达性。
        let Ok(u) = crate::core::bridge_wire::parse_abs_url(gw_url) else {
            return false;
        };
        let port = u.port.unwrap_or(if u.scheme == "https" { 443 } else { 80 });
        return crate::core::bridge_wire::tcp_open(
            &u.hostname,
            port,
            std::time::Duration::from_millis(1500),
        );
    }
    let base = gw_url.trim_end_matches('/');
    let path = "/api/v1/admin/systems";
    let headers = crate::core::sig::signed_headers(admin_key, "GET", path, None, "");
    let (code, _) =
        crate::core::http::json_req(&format!("{base}{path}"), &headers, None, Some("GET"), 8);
    (200..300).contains(&code)
}

#[cfg(test)]
mod tests {
    use super::*;

    // 探针结果构造器（ProbeResult 已 Clone）
    fn conn() -> ProbeResult {
        ProbeResult { reachable: true, error: "connected".into() }
    }
    fn refused() -> ProbeResult {
        ProbeResult { reachable: false, error: "connection_refused".into() }
    }
    fn timeout() -> ProbeResult {
        ProbeResult { reachable: false, error: "timeout".into() }
    }

    fn base() -> PlanInputs {
        PlanInputs {
            gw_reachable: true,
            bridge_host: None,
            probe_gw_to_bridge: None,
            probe_bridge_to_agent: None,
            gw_is_local: false,
            probe_gw_to_agent: None,
            bridge_deployable: false,
        }
    }

    #[test]
    fn gw_unreachable_fails_all_states() {
        let mut i = base();
        i.gw_reachable = false;
        assert!(plan(&i).is_fail());
    }

    #[test]
    fn b_valid_gives_bridge_push_with_host() {
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.probe_gw_to_bridge = Some(conn()); // 桥活
        i.probe_bridge_to_agent = Some(refused()); // 路径通（agent 未起）
        match plan(&i) {
            WebhookPlan::BridgePush { bridge_host } => {
                assert_eq!(bridge_host, "http://203.0.113.7:38080");
            }
            other => panic!("expected BridgePush, got {other:?}"),
        }
        assert_eq!(
            plan(&i).webhook_host().as_deref(),
            Some("http://203.0.113.7:38080")
        );
    }

    #[test]
    fn bridge_down_degrades_not_bridge_push() {
        // probe1 refused = 桥没起 ⇒ B 无效 ⇒ 降级
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.probe_gw_to_bridge = Some(refused());
        i.probe_bridge_to_agent = Some(conn());
        i.gw_is_local = true; // 降级后走 S3
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn second_hop_timeout_degrades() {
        // probe2 timeout = 第二跳不通 ⇒ B 无效
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.probe_gw_to_bridge = Some(conn());
        i.probe_bridge_to_agent = Some(timeout());
        i.gw_is_local = true;
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn no_bridge_probe_is_not_bridge_push() {
        // bridge_host 在场但没跑探测（None）⇒ B 无效
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.gw_is_local = true;
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn local_gw_gives_direct_even_without_r() {
        let mut i = base();
        i.gw_is_local = true; // L ⇒ 直连（agent 在本机，loopback 可达）
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn remote_gw_reachable_agent_gives_direct() {
        let mut i = base();
        i.gw_is_local = false;
        i.probe_gw_to_agent = Some(refused()); // R 路径通（agent 未起）
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn remote_gw_unreachable_agent_bridge_deployable_gives_pull() {
        let mut i = base();
        i.gw_is_local = false;
        i.probe_gw_to_agent = Some(timeout()); // R 不通
        i.bridge_deployable = true; // BR
        assert!(matches!(plan(&i), WebhookPlan::BridgePull));
        assert_eq!(plan(&i).webhook_host().as_deref(), Some(""));
    }

    #[test]
    fn remote_gw_unreachable_agent_no_bridge_fails() {
        let mut i = base();
        i.gw_is_local = false;
        i.probe_gw_to_agent = Some(timeout());
        i.bridge_deployable = false;
        assert!(plan(&i).is_fail());
    }

    #[test]
    fn no_agent_probe_and_not_local_needs_bridge() {
        // 无 AGENT_HOST（R=None）且 GW 非本机 ⇒ 需桥
        let mut i = base();
        i.gw_is_local = false;
        i.bridge_deployable = true;
        assert!(matches!(plan(&i), WebhookPlan::BridgePull));
    }

    #[test]
    fn webhook_host_three_values() {
        // S1 URL
        let mut i = base();
        i.bridge_host = Some("http://b:1".into());
        i.probe_gw_to_bridge = Some(conn());
        i.probe_bridge_to_agent = Some(conn());
        assert_eq!(plan(&i).webhook_host().as_deref(), Some("http://b:1"));
        // S2 空串
        let mut j = base();
        j.bridge_deployable = true;
        assert_eq!(plan(&j).webhook_host().as_deref(), Some(""));
        // S3 键缺席
        let mut k = base();
        k.gw_is_local = true;
        assert_eq!(plan(&k).webhook_host(), None);
    }

    #[test]
    fn bridge_push_wins_over_direct_when_b_valid() {
        // B 有效时优先 S1（即使 L 也成立）
        let mut i = base();
        i.bridge_host = Some("http://b:1".into());
        i.probe_gw_to_bridge = Some(conn());
        i.probe_bridge_to_agent = Some(conn());
        i.gw_is_local = true;
        assert!(matches!(plan(&i), WebhookPlan::BridgePush { .. }));
    }

    #[test]
    fn parse_probe_reads_reachable_and_error() {
        assert_eq!(
            parse_probe(&serde_json::json!({"reachable": true, "error": "connected"})),
            Some(ProbeResult { reachable: true, error: "connected".into() })
        );
        assert_eq!(
            parse_probe(&serde_json::json!({"reachable": false, "error": "connection_refused", "detail": "x"})),
            Some(ProbeResult { reachable: false, error: "connection_refused".into() })
        );
        // 缺 error 字段 ⇒ 归 "unknown"（仍返回 Some）；非对象 / 缺 reachable ⇒ None
        assert_eq!(
            parse_probe(&serde_json::json!({"reachable": true})),
            Some(ProbeResult { reachable: true, error: "unknown".into() })
        );
        assert_eq!(
            parse_probe(&serde_json::json!({"error": "connected"})),
            None,
            "missing reachable ⇒ not a valid probe answer"
        );
        assert_eq!(parse_probe(&serde_json::json!("oops")), None);
        assert_eq!(parse_probe(&serde_json::json!(null)), None);
    }
}
