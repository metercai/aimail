//! inbound webhook decision — the **single decision function** of system-level install (owner 2026-10-13 finalized).
//!
//! Inputs (env + local facts + classification results of two probe endpoints, all collected by caller and injected):
//! - `gw_reachable`  Precondition: GW is reachable from local machine (No ⇒ all states depend on GW, direct Fail);
//! - `bridge_host`   `AIMAIL_BRIDGE_HOST` (`http(s)://host:port`, no path; missing/invalid ⇒ None);
//! - `probe_gw_to_bridge`     probe1: probe BRIDGE_HOST from the GW view (the bridge server endpoint);
//! - `probe_bridge_to_agent`  probe2: Probe agent inbound target from bridge perspective (second hop);
//! - `gw_is_local`  L: GW resolved IP ∈ local machine list;
//! - `probe_gw_to_agent` R: probe AGENT_HOST from GW perspective (direct inbound endpoint);
//! - `bridge_deployable` BR: Local bridge is deployable.
//!
//! Determination (owner ruling verbatim):
//! ```text
//! B = probe1(bridge service level: connected) ∧ probe2(path level: connected ∨ connection_refused)
//!     —— Any missing or invalid ⇒ B=false (BRIDGE_HOST degrades to non-existent)
//! B                       ⇒ S1 push via bridge   webhook_host = BRIDGE_HOST
//! ¬B ∧ (L ∨ R)            ⇒ S3 direct push   webhook_host key absent
//! ¬B ∧ ¬L ∧ ¬R ∧ BR       ⇒ S2 bridge pull     webhook_host = "" (empty string)
//! ¬B ∧ ¬L ∧ ¬R ∧ ¬BR      ⇒ Fail bridge required but not deployable (fail loudly, no silent degradation, not S4)
//! ```
//!
//! **Pure function**: Probes are executed by the caller (`POST /api/v1/admin/probe-webhook`, bridge
//! `POST /api/v1/probe`), this module only interprets classification results and sets state. Two interpretations have different semantics, cannot be mixed:
//! - Bridge **service** endpoint (probe1): Only `connected` = alive (refused = bridge not started = dead);
//! - agent **inbound** endpoint (probe2 / R): `connection_refused` = path reachable (agent process
//!   starts after install, port not listening is expected) = available.

use crate::core::netenv::ProbeResult;

/// Determination result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WebhookPlan {
    /// S1 push via bridge: `webhook_host = BRIDGE_HOST` (`http(s)://host:port`, no path;
    /// GW fills to bridge, bridge appends agent real inbound path when forwarding).
    BridgePush { bridge_host: String },
    /// S2 bridge pull: `webhook_host = ""` (empty string; GW side pending, bridge long-polls and pushes to agent).
    BridgePull,
    /// S3 direct push: `webhook_host` key absent (GW directly pushes to agent inbound endpoint, with path).
    Direct,
    /// install fails loudly (bridge required but not deployable / GW unreachable).
    Fail { reason: String },
}

impl WebhookPlan {
    /// Three values for webhook_host (owner ruling: S1 URL / S2 empty string / S3 key absent).
    /// Returns `None` on `Fail` (should not be persisted).
    pub fn webhook_host(&self) -> Option<String> {
        match self {
            WebhookPlan::BridgePush { bridge_host } => Some(bridge_host.clone()),
            WebhookPlan::BridgePull => Some(String::new()),
            WebhookPlan::Direct => None, // key absent
            WebhookPlan::Fail { .. } => None,
        }
    }

    pub fn is_fail(&self) -> bool {
        matches!(self, WebhookPlan::Fail { .. })
    }
}

/// Determination inputs (all collected by caller and injected).
#[derive(Debug, Clone)]
pub struct PlanInputs {
    /// Precondition: GW is reachable from local machine.
    pub gw_reachable: bool,
    /// `AIMAIL_BRIDGE_HOST` (parsed successfully; missing/invalid ⇒ None).
    pub bridge_host: Option<String>,
    /// probe1: probe BRIDGE_HOST from GW perspective (no bridge_host ⇒ None).
    pub probe_gw_to_bridge: Option<ProbeResult>,
    /// probe2: Probe agent inbound target from bridge perspective (no bridge_host ⇒ None).
    pub probe_bridge_to_agent: Option<ProbeResult>,
    /// L: GW resolves IP ∈ local list (GW is on the local machine).
    pub gw_is_local: bool,
    /// R: probe AGENT_HOST from GW perspective (no valid AGENT_HOST ⇒ None).
    pub probe_gw_to_agent: Option<ProbeResult>,
    /// BR: local bridge is deployable.
    pub bridge_deployable: bool,
}

/// Bridge **service** endpoint interpretation: only `connected` = alive.
fn bridge_service_alive(p: &ProbeResult) -> bool {
    p.reachable
}

/// Interpretation of agent **inbound** endpoint (path level): `connected` ∨ `connection_refused` = path reachable.
fn inbound_path_open(p: &ProbeResult) -> bool {
    p.reachable || p.error == "connection_refused"
}

/// Single decision function.
pub fn plan(i: &PlanInputs) -> WebhookPlan {
    // Precondition: all states depend on GW.
    if !i.gw_reachable {
        return WebhookPlan::Fail {
            reason: "gateway is not reachable from this machine".into(),
        };
    }

    // B = probe1(bridge alive) ∧ probe2(path reachable); any missing/invalid ⇒ false (degrade).
    let b = match (
        i.probe_gw_to_bridge.as_ref(),
        i.probe_bridge_to_agent.as_ref(),
    ) {
        (Some(p1), Some(p2)) => bridge_service_alive(p1) && inbound_path_open(p2),
        _ => false,
    };
    if b {
        // bridge_host must be present (otherwise probe would not be Some).
        let host = i
            .bridge_host
            .clone()
            .unwrap_or_else(|| unreachable!("B true requires bridge_host"));
        return WebhookPlan::BridgePush { bridge_host: host };
    }

    // R: direct inbound path reachable (path level).
    let r = i.probe_gw_to_agent.as_ref().is_some_and(inbound_path_open);
    if i.gw_is_local || r {
        return WebhookPlan::Direct;
    }

    // Bridge required but direct connection unavailable.
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

/// **Plan drift detection** (offline single source of truth for check/repair; same source as [`plan`], pure function).
///
/// `webhook_host` is owned by the **decision function** (written by install, verified by check, not written by repair). Here we only judge
/// Drift that can be falsified by **offline stable facts** — network facts (B bridge alive / R direct reachable) are **not checked**: check is
/// reused ~20 times in the repair write path, and network state fluctuates with process start/stop; checking it would cause false reds.
///
/// Inputs:
/// - `stored`: persisted `webhook_host` tri-state (key absent = None; key present = Some(string, including empty string));
/// - `gw_is_local`: L (pure resolution, offline);
/// - `bridge_deployable`: BR (filesystem, offline).
///
/// Returns `Some(why)` = drift (decision function **cannot** write this state); `None` = no contradiction (including "offline undecidable").
pub fn plan_drift(
    stored: Option<&str>,
    gw_is_local: bool,
    bridge_deployable: bool,
) -> Option<String> {
    match stored {
        // S3 key absent: Decision function S3 only needs ¬B ∧ (L ∨ R); R is a network fact ⇒ not falsifiable offline.
        None => None,
        // S1 non-empty: Decision function S1 value = parseable BRIDGE_HOST (parse failure ⇒ env considered non-existent ⇒ no S1).
        Some(url) if !url.is_empty() => {
            if crate::core::netenv::parse_bridge_host(url).is_none() {
                Some(format!(
                    "persisted webhook_host '{url}' is not a bridge host (expected http(s)://host:port or host:port, no path) — the install plan only writes a parseable BRIDGE_HOST"
                ))
            } else {
                // B (bridge alive) is a network fact ⇒ not determinable offline.
                None
            }
        }
        // S2 empty string: predicate function S2 requires ¬B ∧ ¬L ∧ ¬R ∧ BR, where L / BR are decidable offline.
        Some(_) => {
            if gw_is_local {
                Some(
                    "persisted inbound state is bridge-pull (empty webhook_host), but the gateway \
                     resolves to this machine — the plan would classify this as direct-push \
                     (key absent), never pull"
                        .to_string(),
                )
            } else if !bridge_deployable {
                Some(
                    "persisted inbound state is bridge-pull (empty webhook_host), but no local \
                     bridge is deployable — the plan would fail (a bridge is required but cannot \
                     be deployed), never pull"
                        .to_string(),
                )
            } else {
                // ¬L ∧ BR: S2 may still hold (depends on network facts B / R) ⇒ no contradiction.
                None
            }
        }
    }
}

// ── Collection layer (install call; probes use existing endpoints, classification result interpretation is in the pure function above) ──────────────

/// Parse a probe endpoint's JSON response → `ProbeResult`; missing fields/non-object ⇒ `None` (treated as not probed).
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

/// GW perspective probes a `host:port` (`POST /api/v1/admin/probe-webhook`, system-scope key).
/// GW unreachable/unauthenticated/non-object ⇒ `None` (caller degrades to "not probed").
pub fn probe_gw(gw_url: &str, admin_key: &str, addr: &str) -> Option<ProbeResult> {
    if admin_key.is_empty() {
        return None;
    }
    let base = gw_url.trim_end_matches('/');
    let path = "/api/v1/admin/probe-webhook";
    let body = serde_json::json!({ "addr": addr }).to_string();
    let headers =
        crate::core::sig::signed_headers(admin_key, "POST", path, Some(body.as_bytes()), "");
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

/// Bridge perspective probes a `host:port` (bridge `POST /api/v1/probe`, within admin whitelist).
/// Bridge unreachable/non-object ⇒ `None`.
pub fn probe_bridge(admin_host: &str, admin_port: u16, addr: &str) -> Option<ProbeResult> {
    let body = serde_json::json!({ "addr": addr }).to_string();
    let (code, body_text) = crate::core::http::raw_req_method(
        "POST",
        &format!("http://{admin_host}:{admin_port}/api/v1/probe"),
        Some(body.as_bytes()),
        Some("application/json"),
        8,
    )
    .ok()?;
    if !(200..300).contains(&code) {
        return None;
    }
    parse_probe(&serde_json::from_str(&body_text).ok()?)
}

/// `AIMAIL_BRIDGE_HOST` / `AIMAIL_BRIDGE_ADMIN` / `AIMAIL_AGENT_HOST` env → parse.
pub fn env_bridge_host() -> Option<String> {
    let raw = crate::core::config::env_val("AIMAIL_BRIDGE_HOST", "");
    if raw.trim().is_empty() {
        return None;
    }
    crate::core::netenv::parse_bridge_host(&raw).map(|_| raw.trim().to_string())
}

/// External bridge admin endpoint (`AIMAIL_BRIDGE_ADMIN` preferred, fallback to BRIDGE_HOST host:port if missing).
/// External bridge admin and data ports may differ, so explicit env override is allowed.
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
        // owner: not public network nor local machine IP ⇒ treated as non-existent
        return None;
    }
    Some(raw.trim().to_string())
}

/// install inbound mode decision: collect env + local facts + probes, run `plan`.
/// `gw_reachable` is determined by the response from the GW admin endpoint (GET /admin/systems with system key).
pub fn plan_inbound(gw_url: &str, admin_key: &str) -> WebhookPlan {
    let gw_reachable = gw_admin_reachable(gw_url, admin_key);
    let bridge_host = env_bridge_host();
    let agent_host = env_agent_host();

    // probe1: GW perspective probes bridge service (bridge alive = connected).
    let probe_gw_to_bridge = bridge_host.as_ref().and_then(|h| {
        crate::core::netenv::parse_bridge_host(h)
            .and_then(|(host, port)| probe_gw(gw_url, admin_key, &format!("{host}:{port}")))
    });
    // probe2: bridge perspective probes agent inbound (second hop). Only runs when both bridge+agent are present.
    // Probe endpoint = the bridge's own admin (S1 external bridge / S2 local bridge), not GW.
    let probe_bridge_to_agent = if let (Some(ah), Some((badmin, bport))) =
        (&agent_host, env_bridge_admin())
    {
        crate::core::netenv::parse_hostport(ah)
            .and_then(|(ahost, aport)| probe_bridge(&badmin, bport, &format!("{ahost}:{aport}")))
    } else {
        None
    };
    // R: GW-side probe of agent inbound (path-level).
    let probe_gw_to_agent = agent_host.as_ref().and_then(|ah| {
        crate::core::netenv::parse_hostport(ah)
            .and_then(|(host, port)| probe_gw(gw_url, admin_key, &format!("{host}:{port}")))
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

/// GW admin endpoint reachability (GET with key; 2xx = reachable).
///
/// Probe = `GET /api/v1/whoami` (existing CLI reachability criterion in `pysdk/gateway_api.py:118`):
/// Any authenticated key returns 200, no scope threshold ⇒ only expresses "gateway is alive and this key is valid", does not mix in
/// any admin permission assumption. **Do not** use `/api/v1/admin/systems` (this route does not exist ⇒ always 404).
pub fn gw_admin_reachable(gw_url: &str, admin_key: &str) -> bool {
    if admin_key.is_empty() {
        // No key (e.g., reuse path did not obtain one) ⇒ degrade to TCP reachability.
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
    let path = "/api/v1/whoami";
    let headers = crate::core::sig::signed_headers(admin_key, "GET", path, None, "");
    let (code, _) =
        crate::core::http::json_req(&format!("{base}{path}"), &headers, None, Some("GET"), 8);
    (200..300).contains(&code)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Probe result constructor (ProbeResult is already Clone)
    fn conn() -> ProbeResult {
        ProbeResult {
            reachable: true,
            error: "connected".into(),
        }
    }
    fn refused() -> ProbeResult {
        ProbeResult {
            reachable: false,
            error: "connection_refused".into(),
        }
    }
    fn timeout() -> ProbeResult {
        ProbeResult {
            reachable: false,
            error: "timeout".into(),
        }
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
        i.probe_gw_to_bridge = Some(conn()); // bridge alive
        i.probe_bridge_to_agent = Some(refused()); // path open (agent not started)
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
        // probe1 refused = bridge not up ⇒ B invalid ⇒ downgrade
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.probe_gw_to_bridge = Some(refused());
        i.probe_bridge_to_agent = Some(conn());
        i.gw_is_local = true; // after degradation, go to S3
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn second_hop_timeout_degrades() {
        // probe2 timeout = second hop unreachable ⇒ B invalid
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.probe_gw_to_bridge = Some(conn());
        i.probe_bridge_to_agent = Some(timeout());
        i.gw_is_local = true;
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn no_bridge_probe_is_not_bridge_push() {
        // bridge_host present but no probe run (None) ⇒ B invalid
        let mut i = base();
        i.bridge_host = Some("http://203.0.113.7:38080".into());
        i.gw_is_local = true;
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn local_gw_gives_direct_even_without_r() {
        let mut i = base();
        i.gw_is_local = true; // L ⇒ direct connection (agent is on local machine, loopback reachable)
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn remote_gw_reachable_agent_gives_direct() {
        let mut i = base();
        i.gw_is_local = false;
        i.probe_gw_to_agent = Some(refused()); // R path open (agent not started)
        assert!(matches!(plan(&i), WebhookPlan::Direct));
    }

    #[test]
    fn remote_gw_unreachable_agent_bridge_deployable_gives_pull() {
        let mut i = base();
        i.gw_is_local = false;
        i.probe_gw_to_agent = Some(timeout()); // R not open
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
        // No AGENT_HOST (R=None) and GW not local ⇒ bridge needed
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
        // S2 empty string
        let mut j = base();
        j.bridge_deployable = true;
        assert_eq!(plan(&j).webhook_host().as_deref(), Some(""));
        // S3 key absent
        let mut k = base();
        k.gw_is_local = true;
        assert_eq!(plan(&k).webhook_host(), None);
    }

    #[test]
    fn bridge_push_wins_over_direct_when_b_valid() {
        // When B is valid, prefer S1 (even if L also holds)
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
            Some(ProbeResult {
                reachable: true,
                error: "connected".into()
            })
        );
        assert_eq!(
            parse_probe(
                &serde_json::json!({"reachable": false, "error": "connection_refused", "detail": "x"})
            ),
            Some(ProbeResult {
                reachable: false,
                error: "connection_refused".into()
            })
        );
        // Missing error field ⇒ classify as "unknown" (still returns Some); non-object / missing reachable ⇒ None
        assert_eq!(
            parse_probe(&serde_json::json!({"reachable": true})),
            Some(ProbeResult {
                reachable: true,
                error: "unknown".into()
            })
        );
        assert_eq!(
            parse_probe(&serde_json::json!({"error": "connected"})),
            None,
            "missing reachable ⇒ not a valid probe answer"
        );
        assert_eq!(parse_probe(&serde_json::json!("oops")), None);
        assert_eq!(parse_probe(&serde_json::json!(null)), None);
    }

    // ── plan_drift: offline single source of truth (only judges offline stable facts, does not judge network facts)────────────────

    #[test]
    fn plan_drift_absent_key_is_never_offline_drift() {
        // S3 key absent: decision function S3 only needs ¬B ∧ (L ∨ R), R is a network fact ⇒ offline cannot be falsified.
        assert_eq!(plan_drift(None, false, false), None);
        assert_eq!(plan_drift(None, true, true), None);
        assert_eq!(plan_drift(None, true, false), None);
    }

    #[test]
    fn plan_drift_s1_only_flags_unparseable_hosts() {
        // Parseable BRIDGE_HOST (including bare host:port) ⇒ no contradiction (B is a network fact, not judged offline).
        assert_eq!(plan_drift(Some("http://10.1.2.3:9000"), true, false), None);
        assert_eq!(plan_drift(Some("10.1.2.3:9000"), false, false), None);
        // Local GW + external bridge is a legal S1 ⇒ do not judge drift.
        assert_eq!(plan_drift(Some("http://b.test:38080"), true, true), None);
        // Unresolvable ⇒ drift (the decision function only writes resolvable values).
        assert!(plan_drift(Some("not-a-bridge"), false, false).is_some());
        // With path ⇒ unresolvable (owner: BRIDGE_HOST has no path).
        assert!(plan_drift(Some("http://b.test:38080/with/path"), false, false).is_some());
    }

    #[test]
    fn plan_drift_s2_empty_string_is_offline_disprovable() {
        // Local GW ⇒ plan must decide S3 (L offline can decide) ⇒ S2 is drift.
        let local = plan_drift(Some(""), true, false);
        assert!(local.is_some(), "{local:?}");
        assert!(local.unwrap().contains("direct-push"));
        // Remote GW + no bridge deployable ⇒ plan must Fail (BR offline can decide) ⇒ S2 is drift.
        let nobridge = plan_drift(Some(""), false, false);
        assert!(nobridge.is_some(), "{nobridge:?}");
        assert!(nobridge.unwrap().contains("bridge is required"));
        // Remote GW + bridge deployable ⇒ S2 may still hold (depends on B/R) ⇒ do not decide.
        assert_eq!(plan_drift(Some(""), false, true), None);
    }
}
