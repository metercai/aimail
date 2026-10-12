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
}
