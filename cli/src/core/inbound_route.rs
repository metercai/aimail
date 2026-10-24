//! Engine components for the hidden inbound surface (`address --inbound-live/--inbound-down`):
//! `_withdraw_inbound_routes` (withdraw all route lines for this system, idempotent), `_gateway_backlog_count`
//! (Gateway read-only backlog query, best-effort), `live_confirmed` (inbound up **confirmation URL feedback**,
//! contract §4.3, owner 2026-10-13).
//!
//! Same domain as `bridge_wire` (bridge routing), but belongs to the "action orchestration" layer: [`bridge_wire`] manages vocabulary/single-line sync/reconciliation,
//! this manages actions required for the hidden surface. Only touches the CLI side, zero SDK dependencies.

use serde_json::{json, Map, Value};
use std::path::Path;

use crate::core::bridge_wire as bw;

/// **Registered addresses** under this sid (`email` in `<aimail_home>/systems/<sid>/*/<binding_file>`).
///
/// Empty set = no binding for this sid locally (unregistered/cleared) ⇒ caller degrades to domain scope,
/// ensuring "withdraw all route lines for this system" still cleans up completely when bindings are missing (no residue left).
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

/// Whether a route line belongs to this sid: domain scope hit, and (when ownership can be determined) address is under this sid.
fn route_in_scope(email: &str, domain_hit: bool, owned: &[String]) -> bool {
    domain_hit && (owned.is_empty() || owned.iter().any(|e| e == email))
}

/// `_withdraw_inbound_routes(sid, cfg)`: withdraw **all** route lines for **this system** (idempotent).
///
/// Returns `(withdrawn, failed, no_bridge, anchor_out)`:
/// · `no_bridge` ⇒ bridge not declared (caller should stay silent, not change rc);
/// · `anchor_out` ⇒ result of the anchor line (for caller rendering; `None` if anchor is not in the table).
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

/// `_gateway_backlog_count(cfg, emails)`: pending delivery count on gateway side (**read-only**, best-effort).
///
/// `POST /api/v1/admin/pending`, body `{"filter": [], "emails": [...]}`, accumulate `deliveries` per bucket.
/// Missing gw/ak/emails, non-2xx response, structural anomaly ⇒ `None` (caller prints `; backlog unknown`), **never blocks**.
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

/// `_finish_anchor(out)`: four-state exit for anchor result (**copied verbatim** from Python's fail-closed semantics).
pub fn finish_anchor(out: Option<&Value>) -> i32 {
    use crate::cmd::report::{fail, ok, warn};
    let Some(o) = out else {
        return 0; // Anchor not in table ⇒ no event to broadcast
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

/// Synthetic event when the anchor is pull (empty webhook): `skipped` -- idempotent no-op.
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

/// Synthetic event when the anchor line is not in the routing table: `ok` / `route already absent` -- idempotent.
pub fn already_absent_outcome(email: &str) -> Value {
    let mut m = Map::new();
    m.insert("state".into(), json!("ok"));
    m.insert("action".into(), json!(bw::ACTION_DOWN));
    m.insert("email".into(), json!(email));
    m.insert("reason".into(), json!("route already absent"));
    Value::Object(m)
}

// ── Inbound up confirmation URL feedback (Contract §4.3, owner 2026-10-13)─────────────────────
//
// install only persists the "best known" inbound state; the inbound server's actual listen port is determined only after it starts listening, and the SDK calls back per address
// `address --inbound-live --inbound-url <confirmed URL>`, writing the **confirmed** inbound endpoint back to the routing layer.
// Dispatch by `webhook_host` tri-state (single source of truth = system config):
//   S1 key present=URL  => external bridge push: upsert route directly to that bridge admin (target=confirmed URL);
//   S2 key present=empty string  => local bridge pull: upsert route to local bridge admin (same API, 127.0.0.1);
//   S3 key absent     ⇒ direct push: narrow refresh gateway per-address webhook (only modifies the webhook field).
// Returns an outcome with the same shape as `sync_route` (for `finish_anchor` to render four states).

/// S1 bridge admin endpoint resolution: `webhook_host` = `http(s)://host:port` (no path) ⇒ `(host, port)`.
/// Unresolvable ⇒ `None` (caller reports failed).
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

/// S3 direct push confirmation write-back: narrow refresh gateway per-address webhook (**only touches webhook field**).
///
/// Carrier = `GET /api/v1/admin/systems/{sid}/domains` (system scope) to get `domain==email`
/// record id where `domain==email`, then `PUT /api/v1/admin/system-domains/{id}` `{webhook_url, webhook_secret}`.
/// Chose this over "re-send registration": re-sending registration goes through `create_domain`'s same-system upsert, which **unconditionally**
/// flushes `domain_addr_meta`'s `agent_signature`/`agent_persona` to empty (`storage.rs:951-952`),
/// Clear SDK-persisted signature/persona; narrow PUT only UPDATEs `webhook_url`/`webhook_secret`,
/// `is_active = COALESCE(NULL, is_active)` remains unchanged (`storage.rs:677`), agent meta untouched.
/// Same carrier as TS SDK `syncAddressWebhook` (`auto-bind.ts:305`).
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
    // 1) Get per-address record id
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
    // 2) narrow PUT: only refresh webhook fields (secret shares source with binding; if missing, retain gateway current value)
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

/// outcome skeleton (same shape as `sync_route`, for `finish_anchor`/`format_line` rendering).
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

/// confirmation-URL feedback for inbound up (contract §4.3). **never panics**.
///
/// `confirmed_url` is empty ⇒ `skipped` (callback did not include a confirmation endpoint, do not touch routing, state the reason clearly).
/// Non-empty but not an absolute http(s) URL ⇒ `failed` (do not rebuild, do not guess port, same discipline as `validate_target`).
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
    // Target must already be a deliverable absolute URL (reuse validate_target: reject bare host:port, do not add prefix)
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
        // S3 direct push: narrow refresh gateway per-address webhook
        let secret = binding_webhook_secret(sid, email);
        return s3_refresh_webhook(cfg, sid, email, &target, &secret);
    }

    // S1 (URL) / S2 (empty string) => bridge route upsert. Admin endpoint resolved by state:
    let (admin_host, admin_port, src) = if !webhook_host.is_empty() {
        // S1 external bridge: webhook_host = http(s)://host:port
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
        // S2 local bridge: go via declaration (127.0.0.1 + declared port)
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

/// `webhook_secret` in the binding file (read-only; S3 refresh and gateway current value share the same source, missing ⇒ empty string preserves gateway value).
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
            "Addresses of other sids must not be revoked"
        );
        assert!(
            !route_in_scope("a@d.test", false, &owned),
            "Must not revoke if domain does not match"
        );
        assert!(
            route_in_scope("x@d.test", true, &[]),
            "Degrade to domain scope when ownership is unknown"
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
    /// Confirm inbound URL (path taken from contract single source of truth, no literals -- literal ratchet rule (b)).
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
        // No port ⇒ http→80 / https→443
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
        // Bare host:port / no scheme ⇒ cannot be resolved as an admin endpoint
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
        // Bare host:port cannot be delivered ⇒ failed (do not add prefix, do not guess port)
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
        // S3 (key absent) but system config has no gateway_url/admin_key ⇒ failed
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
        // S1 (webhook_host=url) external bridge unreachable => fail closed (do not touch route)
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
        // S2 (webhook_host=empty string) but no declared bridge => failed (pull must go through bridge)
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
