//! Rust port of `cli/bridge_wire.py` -- **CLI-owned bridge routing wiring** (zero SDK dependency).
//!
//! Why this layer (copied from the original rationale of the Python module, do not replace with SDK bridge helpers): the bridge wiring on the SDK side
//! (e.g., `aimail_base.ensure_bridge_route`) is scheduled for removal; CLI calls to those symbols will break immediately after SDK removal.
//! So the CLI maintains its own bridge client here. **This module does not import anything from pysdk/agent-aimail.**
//!
//! Contract (read from bridge repo: `~/aimail-bridge/src/admin.rs` + `router.rs`) —— admin API service is on the bridge's
//! configured address, independent of mode:
//! ```text
//!   GET    /health                -> {"status","uptime_secs","version"}
//!   GET    /api/v1/routes         -> [{"email","host","port"}, ...]
//!   POST   /api/v1/routes         <- {"email","host","port"}   (upsert; idempotent)
//!   DELETE /api/v1/routes/:email  -> "ok"                      (withdraw)
//! ```
//! `host` field **must be a complete absolute URL**: bridge uses `ProfileRoute::from_url` for hosts containing `://` (URL fidelity,
//! path preserved); passing bare `host:port` will make the bridge apply default path and treat host as literal, producing `http://host:port:80/...`
//! such undeliverable targets (2026-09-21 measured: reflush wrote `127.0.0.1:9101` as `127.0.0.1:9101:80` => delivery silently broken).
//! Therefore this module **only sends validated absolute URLs, and never constructs one itself**.
//!
//! Read-only discipline: do not write any files. The bridge's own config file is the "there is a bridge here" signal; system config provides the admin port, and binding provides the target.
//!
//! Four-state vocabulary (no new terms): `ok` / `skipped` / `no_bridge` / `failed` (failed = declared but unavailable ⇒
//! fail closed, change nothing, state the reason clearly). `up`/`down` are **asymmetric** (2026-09-28 ruling): routes are keyed by address,
//! withdrawal only requires email + a reachable bridge; bindings with an empty bind endpoint (pull) **must still withdraw** the routes created during push
//! —— the old "empty ⇒ skip" short-circuit would leave this line until the bridge's own health cleanup (up to 180s), during which emails keep being pushed to the host that has stopped receiving.

use std::collections::BTreeMap;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::Duration;

use serde_json::{json, Map, Value};

/// admin API host: the bridge binds to a local address, and the admin API is restricted to localhost by default ⇒ the CLI always uses loopback.
pub const ADMIN_HOST: &str = "127.0.0.1";
/// Fallback admin port when neither system config nor bridge config specifies one.
pub const DEFAULT_ADMIN_PORT: u16 = 38081;

pub const STATE_OK: &str = "ok";
pub const STATE_SKIPPED: &str = "skipped";
pub const STATE_NO_BRIDGE: &str = "no_bridge";
pub const STATE_FAILED: &str = "failed";

pub const ACTION_LIVE: &str = "live";
pub const ACTION_DOWN: &str = "down";

// ── helpers ────────────────────────────────────────────────────────────────────

/// `_valid_port`: invalid/out-of-range ⇒ 0 (called "invalid").
pub fn valid_port(value: &Value) -> u16 {
    let n = match value {
        Value::Number(n) => n.as_i64().unwrap_or(0),
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    };
    if n > 0 && n < 65536 {
        n as u16
    } else {
        0
    }
}

/// `_port_from_bind`: the port segment in `"0.0.0.0:38080"` / `"[::1]:38081"` / `"38081"`.
pub fn port_from_bind(bind: &str) -> u16 {
    let s = bind.trim();
    if s.is_empty() {
        return 0;
    }
    if let Some(rest) = s.strip_prefix('[') {
        return match rest.split_once(']') {
            Some((_, after)) => valid_port(&Value::String(
                after.strip_prefix(':').unwrap_or("").to_string(),
            )),
            None => 0,
        };
    }
    if let Some((_, p)) = s.rsplit_once(':') {
        return valid_port(&Value::String(p.to_string()));
    }
    valid_port(&Value::String(s.to_string()))
}

/// Bridge declaration (return value of `load_declaration`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Declaration {
    pub declared: bool,
    pub usable: bool,
    pub reason: String,
    pub mode: String,
    pub bind: String,
    pub admin_host: String,
    pub admin_port: u16,
    pub source: String,
    pub gateway_port: u16,
}

/// Read-only determination: whether a bridge is declared on this machine, and where its admin API is.
///
/// `declared` is true when "the bridge's own config file is in place" or "the system config has `bridge_admin_port`";
/// `usable=false` indicates declared but broken (unreadable/invalid TOML) -- this must **fail closed**,
/// and must never be treated as "no bridge here". **Never raises an error.**
pub fn load_declaration(gateway_cfg: &Value, bridge_cfg_path: &Path) -> Declaration {
    let mut out = Declaration {
        declared: false,
        usable: true,
        reason: String::new(),
        mode: String::new(),
        bind: String::new(),
        admin_host: ADMIN_HOST.to_string(),
        admin_port: DEFAULT_ADMIN_PORT,
        source: String::new(),
        gateway_port: 0,
    };
    let gw_port = valid_port(gateway_cfg.get("bridge_admin_port").unwrap_or(&Value::Null));
    out.gateway_port = gw_port;
    if gw_port != 0 {
        out.declared = true;
        out.admin_port = gw_port;
        out.source = "system config bridge_admin_port".to_string();
    }

    if !bridge_cfg_path.is_file() {
        if !out.declared {
            out.reason = format!(
                "no bridge config at {} and no bridge_admin_port declared",
                bridge_cfg_path.display()
            );
        }
        return out;
    }

    let parsed: Result<toml::Value, String> = std::fs::read(bridge_cfg_path)
        .map_err(|e| e.to_string())
        .and_then(|bytes| {
            std::str::from_utf8(&bytes)
                .map_err(|e| e.to_string())
                .and_then(|t| t.parse::<toml::Value>().map_err(|e| e.to_string()))
        });
    let toml_cfg = match parsed {
        Ok(v) => v,
        Err(e) => {
            // Bad declaration = fail closed state (Python reports `{ExcName}: {e}`; here we provide equivalent text)
            out.declared = true;
            out.usable = false;
            out.source = bridge_cfg_path.display().to_string();
            out.reason = format!(
                "bridge config {} is unreadable/invalid TOML ({}) — nothing changed",
                bridge_cfg_path.display(),
                e
            );
            return out;
        }
    };

    out.declared = true;
    out.source = bridge_cfg_path.display().to_string();
    out.mode = toml_cfg
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    out.bind = toml_cfg
        .get("bind")
        .or_else(|| toml_cfg.get("addr"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if gw_port == 0 {
        let bind_port = port_from_bind(&out.bind);
        if bind_port != 0 {
            out.admin_port = bind_port;
        }
    }
    out
}

/// Real TCP probe: is anyone listening on `host:port`.
pub fn tcp_open(host: &str, port: u16, timeout: Duration) -> bool {
    let addrs = match (host, port).to_socket_addrs() {
        Ok(a) => a,
        Err(_) => return false,
    };
    for a in addrs {
        if TcpStream::connect_timeout(&a, timeout).is_ok() {
            return true;
        }
    }
    false
}

/// JSON object when `GET /health` -> admin API responds, otherwise None (informational only; reachability criterion is TCP probe).
pub fn admin_health(host: &str, port: u16, timeout_secs: u64) -> Option<Value> {
    let url = format!("http://{}:{}/health", host, port);
    let (code, body) = crate::core::http::raw_req(&url, None, None, timeout_secs).ok()?;
    if !(200..300).contains(&code) {
        return None;
    }
    let v: Value = serde_json::from_str(if body.is_empty() { "{}" } else { &body }).ok()?;
    if v.is_object() {
        Some(v)
    } else {
        None
    }
}

/// `(reachable, detail)` -- reachable = admin port accepts connection.
pub fn admin_reachable(host: &str, port: u16) -> (bool, String) {
    if tcp_open(host, port, Duration::from_millis(500)) {
        match admin_health(host, port, 1) {
            Some(h) if !h.get("status").unwrap_or(&Value::Null).is_null() => {
                let status = h.get("status").and_then(|v| v.as_str()).unwrap_or("");
                let version = h
                    .get("version")
                    .map(|v| match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    })
                    .unwrap_or_else(|| "?".to_string());
                (
                    true,
                    format!(
                        "admin {}:{} is up (health status={}, version={})",
                        host, port, status, version
                    ),
                )
            }
            _ => (
                true,
                format!("admin {}:{} is up (no /health contract answer)", host, port),
            ),
        }
    } else {
        (false, format!("admin {}:{} is not reachable", host, port))
    }
}

/// The parsed absolute URL (a semantic subset of `urlparse` + `u.port`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedUrl {
    pub scheme: String,
    pub netloc: String,
    pub hostname: String,
    pub port: Option<u16>,
    pub path: String,
    pub query: String,
    pub fragment: String,
}

/// Hand-written parsing, mirroring the key behavior of `urlparse`: `scheme://netloc[/path]`,
/// non-numeric or out-of-range port ⇒ Err (corresponds to Python `u.port` raising `ValueError`).
pub fn parse_abs_url(raw: &str) -> Result<ParsedUrl, String> {
    let (scheme, rest) = match raw.split_once("://") {
        Some((s, r)) => (s.to_lowercase(), r),
        None => (String::new(), raw),
    };
    let netloc = rest.split(['/', '?', '#']).next().unwrap_or("").to_string();
    // userinfo is not part of this scenario; take the part after @
    let hostpart = netloc.rsplit('@').next().unwrap_or("").to_string();
    let (hostname, port_str) = if let Some(rest2) = hostpart.strip_prefix('[') {
        match rest2.split_once(']') {
            Some((h, after)) => (
                h.to_string(),
                after.strip_prefix(':').map(|s| s.to_string()),
            ),
            None => (String::new(), None),
        }
    } else if let Some((h, p)) = hostpart.rsplit_once(':') {
        (h.to_string(), Some(p.to_string()))
    } else {
        (hostpart.clone(), None)
    };
    let port = match port_str {
        None => None,
        Some(p) => {
            let n: i64 = p
                .trim()
                .parse()
                .map_err(|_| format!("Port could not be cast to integer value as '{}'", p))?;
            if !(0..65536).contains(&n) {
                return Err("Port out of range 0-65535".to_string());
            }
            Some(n as u16)
        }
    };
    // path/query/fragment (`urlparse` semantics: the part after scheme://netloc)
    let after_scheme = match raw.split_once("://") {
        Some((_, r)) => r,
        None => raw,
    };
    let tail = match after_scheme.find('/') {
        Some(k) => after_scheme[k..].to_string(),
        None => String::new(),
    };
    let (path, query, fragment) = match tail.split_once('#') {
        Some((before_hash, frag)) => {
            let (p, q) = match before_hash.split_once('?') {
                Some((p, q)) => (p.to_string(), q.to_string()),
                None => (before_hash.to_string(), String::new()),
            };
            (p, q, frag.to_string())
        }
        None => match tail.split_once('?') {
            Some((p, q)) => (p.to_string(), q.to_string(), String::new()),
            None => (tail, String::new(), String::new()),
        },
    };
    Ok(ParsedUrl {
        scheme,
        netloc,
        hostname,
        port,
        path,
        query,
        fragment,
    })
}

/// `(absolute_url, reason)` -- target must already be a deliverable absolute URL. Empty `reason` indicates accepted.
///
/// Bare `host:port` (or `host:port/path`) is **rejected** rather than prefixed: prefixing exactly produces the 2026-09-27 measured
/// undeliverable form `http://host:port:80/...` observed in the 2026-09-27 test. Accepted URLs are **passed through as-is, never reconstructed**.
pub fn validate_target(value: &Value) -> (String, String) {
    let raw = match value {
        Value::Null => String::new(),
        Value::String(s) => s.trim().to_string(),
        other => other.to_string().trim().to_string(),
    };
    if raw.is_empty() {
        return (
            String::new(),
            "the binding carries no webhook_url (pull mode: the gateway fetches the mail, \
             there is no local endpoint to route to)"
                .to_string(),
        );
    }
    let u = match parse_abs_url(&raw) {
        Ok(u) => u,
        Err(e) => {
            return (
                String::new(),
                format!("'{}' is not a usable URL ({})", raw, e),
            )
        }
    };
    if u.scheme != "http" && u.scheme != "https" {
        return (
            String::new(),
            format!(
                "not an absolute http(s) URL: '{}' has no http(s):// scheme \
                 (a bare host:port cannot be delivered to)",
                raw
            ),
        );
    }
    if u.netloc.is_empty() || u.hostname.is_empty() {
        return (String::new(), format!("'{}' carries no host", raw));
    }
    if u.port == Some(0) {
        return (String::new(), format!("'{}' carries an invalid port", raw));
    }
    (raw, String::new())
}

// ── bridge client ──────────────────────────────────────────────────────────────

/// `(ok, detail)` -- one admin call. **Never throws**, reason returned as text.
pub fn admin_request(
    method: &str,
    host: &str,
    port: u16,
    path: &str,
    body: Option<&Value>,
    timeout_secs: u64,
) -> (bool, String) {
    let url = format!("http://{}:{}{}", host, port, path);
    let data = body.map(|b| b.to_string().into_bytes());
    match crate::core::http::raw_req_method(
        method,
        &url,
        data.as_deref(),
        Some("application/json"),
        timeout_secs,
    ) {
        Ok((code, body_text)) => {
            if (200..300).contains(&code) {
                (true, body_text.trim().to_string())
            } else {
                // Mirrors Python's `HTTPError` categorization: status code + reason phrase
                (
                    false,
                    format!("bridge answered HTTP {} ({})", code, body_text),
                )
            }
        }
        Err(e) => (false, e),
    }
}

/// `POST /api/v1/routes {email, host, port}` —— idempotent upsert (bridge-side table).
///
/// `port` is a placeholder (ignored by the bridge when host is a full URL); it can only be 80, because `admin.rs` rejects 0.
pub fn upsert_route(email: &str, target_url: &str, host: &str, port: u16) -> (bool, String) {
    let body = json!({"email": email, "host": target_url, "port": 80});
    admin_request("POST", host, port, "/api/v1/routes", Some(&body), 5)
}

/// `DELETE /api/v1/routes/:email` —— revoke the route for this address.
pub fn withdraw_route(email: &str, host: &str, port: u16) -> (bool, String) {
    let path = format!("/api/v1/routes/{}", percent_encode(email));
    admin_request("DELETE", host, port, &path, None, 5)
}

/// Minimal implementation of `urllib.parse.quote(s, safe='')` (all non-unreserved characters are %XX).
fn percent_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.as_bytes() {
        let c = *b as char;
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~') {
            out.push(c);
        } else {
            out.push_str(&format!("%{:02X}", b));
        }
    }
    out
}

// ── the three-step decision ────────────────────────────────────────────────────

/// Determine what to do for this single address, and do it. **Never throws errors**.
///
/// 1. Only `action="live"` (up) checks the bound local endpoint: empty value = valid pull binding ⇒ `skipped` + reason;
///    non-empty but undeliverable = defect ⇒ `failed` (reject and provide reason, never silently pass). `down` **does not look at the URL at all**:
///    Routes are keyed by email; when the bound endpoint becomes empty, we **must** revoke it (2026-09-28);
/// 2. Declared bridges must be reachable, otherwise `failed` + reason and **do nothing** (fail closed: half-configured bridges are not allowed to write);
/// 3. No bridge declared ⇒ `no_bridge`: an intentional no-op, not an error.
pub fn sync_route(
    action: &str,
    email: &str,
    target_url: &Value,
    gateway_cfg: &Value,
    bridge_cfg_path: &Path,
    admin_host: &str,
) -> Value {
    let act = if action.to_lowercase() == ACTION_DOWN {
        ACTION_DOWN
    } else {
        ACTION_LIVE
    };
    let mut out = Map::new();
    out.insert("state".into(), json!(STATE_FAILED));
    out.insert("action".into(), json!(act));
    out.insert("email".into(), json!(email));
    out.insert("target".into(), json!(""));
    out.insert("reason".into(), json!(""));
    out.insert("admin_port".into(), json!(DEFAULT_ADMIN_PORT));
    out.insert("mode".into(), json!(""));
    out.insert("bind".into(), json!(""));
    out.insert("source".into(), json!(""));

    if act != ACTION_DOWN {
        let raw = match target_url {
            Value::Null => String::new(),
            Value::String(s) => s.trim().to_string(),
            other => other.to_string().trim().to_string(),
        };
        let (target, why) = validate_target(target_url);
        if target.is_empty() {
            out.insert(
                "state".into(),
                json!(if raw.is_empty() {
                    STATE_SKIPPED
                } else {
                    STATE_FAILED
                }),
            );
            out.insert("reason".into(), json!(why));
            return Value::Object(out);
        }
        out.insert("target".into(), json!(target));
    }

    let decl = load_declaration(gateway_cfg, bridge_cfg_path);
    out.insert("admin_port".into(), json!(decl.admin_port));
    out.insert("mode".into(), json!(decl.mode));
    out.insert("bind".into(), json!(decl.bind));
    out.insert("source".into(), json!(decl.source));
    if !decl.declared {
        out.insert("state".into(), json!(STATE_NO_BRIDGE));
        out.insert("reason".into(), json!(decl.reason));
        return Value::Object(out);
    }
    if !decl.usable {
        out.insert("state".into(), json!(STATE_FAILED));
        out.insert("reason".into(), json!(decl.reason));
        return Value::Object(out);
    }

    let (reachable, detail) = admin_reachable(admin_host, decl.admin_port);
    if !reachable {
        out.insert("state".into(), json!(STATE_FAILED));
        out.insert(
            "reason".into(),
            json!(format!(
                "bridge declared ({}) but {} — nothing changed",
                decl.source, detail
            )),
        );
        return Value::Object(out);
    }

    if act == ACTION_DOWN {
        let (ok, detail) = withdraw_route(email, admin_host, decl.admin_port);
        if ok {
            out.insert("state".into(), json!(STATE_OK));
            out.insert(
                "reason".into(),
                json!(if detail.is_empty() {
                    "route withdrawn".to_string()
                } else {
                    detail
                }),
            );
        } else {
            out.insert("state".into(), json!(STATE_FAILED));
            out.insert(
                "reason".into(),
                json!(format!("could not withdraw the route: {}", detail)),
            );
        }
        return Value::Object(out);
    }

    let target = out
        .get("target")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let (ok, detail) = upsert_route(email, &target, admin_host, decl.admin_port);
    if ok {
        out.insert("state".into(), json!(STATE_OK));
        out.insert(
            "reason".into(),
            json!(if detail.is_empty() {
                "route upserted".to_string()
            } else {
                detail
            }),
        );
    } else {
        out.insert("state".into(), json!(STATE_FAILED));
        out.insert(
            "reason".into(),
            json!(format!("could not upsert the route: {}", detail)),
        );
    }
    Value::Object(out)
}

/// Return execution (same shape as SDK route lines).
pub fn format_line(outcome: &Value) -> String {
    let state = outcome.get("state").and_then(|v| v.as_str()).unwrap_or("");
    let email = outcome.get("email").and_then(|v| v.as_str()).unwrap_or("");
    if state == STATE_OK && outcome.get("action").and_then(|v| v.as_str()) == Some(ACTION_DOWN) {
        return format!("route withdrawn: {}", email);
    }
    if state == STATE_OK {
        return format!(
            "route: {} -> {}",
            email,
            outcome.get("target").and_then(|v| v.as_str()).unwrap_or("")
        );
    }
    if state == STATE_SKIPPED {
        let r = outcome.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return format!(
            "route skipped for {}: {}",
            email,
            if r.is_empty() { "no local endpoint" } else { r }
        );
    }
    if state == STATE_NO_BRIDGE {
        let r = outcome.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return format!(
            "route skipped for {}: {}",
            email,
            if r.is_empty() { "no local bridge" } else { r }
        );
    }
    if state == STATE_FAILED {
        let r = outcome.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return format!(
            "route FAILED for {}: {} -- run 'aimail repair'",
            email,
            if r.is_empty() { "unknown" } else { r }
        );
    }
    format!(
        "route: {} state={}",
        email,
        if state.is_empty() { "unknown" } else { state }
    )
}

// ── Local routing table / ownership criteria (shared by `cmd_bridge` and reconciliation)────────────────────────────

/// `_read_routes`: read `aimail_routes.toml` (email -> target URL).
/// Note: **line-by-line parsing** (not real TOML -- values contain URLs, and historical files are in `key = "value"` format).
pub fn read_routes(path: &Path) -> BTreeMap<String, String> {
    let mut routes = BTreeMap::new();
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return routes,
    };
    for line in text.lines() {
        let s = line.trim();
        if s.is_empty() || s.starts_with('#') || !s.contains('=') {
            continue;
        }
        let (k, v) = s.split_once('=').unwrap();
        routes.insert(
            k.trim().trim_matches('"').to_string(),
            v.trim().trim_matches('"').to_string(),
        );
    }
    routes
}

/// `_target_to_route_fields`: receiver URL -> bridge admin API `{host, port}` fields.
/// host must be a full URL (containing `://`) => return `(url, 80)`; bare `host:port` fallback splits into two fields.
pub fn target_to_route_fields(target: &str) -> (String, u16) {
    let t = target.trim();
    if t.contains("://") {
        return (t.to_string(), 80);
    }
    if let Some((h, p)) = t.rsplit_once(':') {
        if !h.is_empty() && p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty() {
            return (h.to_string(), p.parse().unwrap_or(80));
        }
    }
    (t.to_string(), 80)
}

/// `_system_route_scope`: criteria for this system's route lines. If ownership cannot be determined (missing domain) => None.
///
/// shared (config has `system_name`): suffix `.{system_name}@{domain}`; non-shared: suffix `@{domain}`.
pub type RouteScope = Box<dyn Fn(&str) -> bool>;

pub fn system_route_scope(cfg: &Value) -> Option<RouteScope> {
    let domain = cfg
        .get("domain")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if domain.is_empty() {
        return None;
    }
    let sname = cfg
        .get("system_name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if sname.is_empty() {
        let suffix = format!("@{}", domain);
        Some(Box::new(move |em: &str| em.ends_with(&suffix)))
    } else {
        let suffix = format!(".{}@{}", sname, domain);
        Some(Box::new(move |em: &str| em.ends_with(&suffix)))
    }
}

// ── reconciliation orchestration (`_reconcile_inbound_routes` / `_ensure_inbound_routes`) ──────────────

/// `(email, webhook_url)` of the **already-registered** bindings on this machine — the expected-state source for reconciliation.
///
/// Only take items where "binding file is readable and has email": **unregistered** expected items in platform enumeration are meaningless at the route layer
/// (routes are keyed by address, unregistered addresses have no deliverable cloud identity).
pub fn registered_bindings(sid: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    if sid.is_empty() {
        return out;
    }
    let base = crate::core::config::systems_root_in(&crate::core::home::aimail_home()).join(sid);
    let entries = match std::fs::read_dir(&base) {
        Ok(e) => e,
        Err(_) => return out,
    };
    let mut dirs: Vec<_> = entries.flatten().map(|e| e.path()).collect();
    dirs.sort();
    for d in dirs {
        if !d.is_dir() {
            continue;
        }
        let jf = d.join(crate::core::contract::binding_file());
        let text = match std::fs::read_to_string(&jf) {
            Ok(t) => t,
            Err(_) => continue, // Permission/IO exceptions treated as unregistered, enumeration does not interrupt
        };
        let v: Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let em = v.get("email").and_then(|x| x.as_str()).unwrap_or("");
        if em.is_empty() {
            continue;
        }
        out.push((
            em.to_string(),
            v.get("webhook_url")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string(),
        ));
    }
    out
}

/// `_reconcile_inbound_routes`: live reconciliation -- expected state = all registered bindings of this system, actual state = bridge route table.
///
/// Missing/changed => upsert; **this system's** lines where config is no longer needed (binding disappeared/changed to pull) => revoke; lines with undetermined ownership only warn, no action.
/// Returns `(reported, anchor_out)`; `anchor_out=None` indicates anchor has nothing to report.
pub fn reconcile_inbound_routes(
    sid: &str,
    cfg: &Value,
    anchor_email: &str,
    bridge_cfg: &Path,
) -> (usize, Option<Value>) {
    use crate::cmd::report::{ok, warn};

    let mut upsert: BTreeMap<String, String> = BTreeMap::new();
    for (em, url) in registered_bindings(sid) {
        if !url.trim().is_empty() {
            upsert.insert(em, url);
        }
    }
    let routes = read_routes(&crate::core::bridge_wire::routes_file());
    let pred = system_route_scope(cfg);
    let mut withdraw: Vec<String> = Vec::new();
    match pred {
        None => {
            if !routes.is_empty() {
                warn(
                    "route reconcile: cannot attribute existing routes to this system \
                     (config domain missing) — stale rows left untouched",
                );
            }
        }
        Some(p) => {
            withdraw = routes
                .keys()
                .filter(|em| !upsert.contains_key(*em) && p(em))
                .cloned()
                .collect();
        }
    }

    let mut reported = 0usize;
    let mut anchor_out: Option<Value> = None;
    let mut no_bridge = false;
    for (em, url) in &upsert {
        let out = sync_route(ACTION_LIVE, em, &json!(url), cfg, bridge_cfg, ADMIN_HOST);
        reported += 1;
        if em == anchor_email {
            anchor_out = Some(out.clone());
        } else {
            let line = format_line(&out);
            if out.get("state").and_then(|v| v.as_str()) == Some(STATE_OK) {
                ok(&line);
            } else {
                warn(&line);
            }
        }
        if out.get("state").and_then(|v| v.as_str()) == Some(STATE_NO_BRIDGE) {
            no_bridge = true;
            break;
        }
    }
    if !no_bridge {
        for em in &withdraw {
            let out = sync_route(ACTION_DOWN, em, &Value::Null, cfg, bridge_cfg, ADMIN_HOST);
            reported += 1;
            if em == anchor_email {
                anchor_out = Some(out.clone());
            } else {
                let line = format_line(&out);
                if out.get("state").and_then(|v| v.as_str()) == Some(STATE_OK) {
                    ok(&line);
                } else {
                    warn(&line);
                }
            }
        }
    }
    (reported, anchor_out)
}

/// Route table path under bridge runtime directory (`ROUTES_FILE`).
pub fn routes_file() -> std::path::PathBuf {
    crate::core::home::aimail_home()
        .join("bridge")
        .join("aimail_routes.toml")
}

/// Bridge config file path (`BRIDGE_CFG`).
pub fn bridge_cfg_file() -> std::path::PathBuf {
    crate::core::home::aimail_home()
        .join("bridge")
        .join("aimail_bridge.toml")
}

/// `_ensure_inbound_routes`: Perform one live reconciliation based on system config. **best-effort**: never changes caller rc.
pub fn ensure_inbound_routes(sid: &str) -> i32 {
    use crate::cmd::report::warn;
    if sid.is_empty() {
        return 0;
    }
    // Python: when system config cannot be read, cfg={} => still proceed with reconciliation, but ownership criteria missing domain => only warn, no action
    let cfg: Value = match crate::core::config::load_gateway_config(sid) {
        Some(c) => Value::Object(c.to_json()),
        None => json!({}),
    };
    let _ = reconcile_inbound_routes(sid, &cfg, "", &bridge_cfg_file());
    let _ = warn; // Keep same shape as Python (failure only warns)
    0
}

// ── bridge maintenance (used by `cmd_bridge`: process detection / path / expected routes) ────────────────────────────

/// Bridge runtime directory (`BRIDGE_DIR`).
pub fn bridge_dir() -> std::path::PathBuf {
    crate::core::home::aimail_home().join("bridge")
}

/// Bridge pid file (`BRIDGE_PID`).
pub fn bridge_pid_file() -> std::path::PathBuf {
    bridge_dir().join("bridge.pid")
}

/// Bridge log (`BRIDGE_LOG`).
pub fn bridge_log_file() -> std::path::PathBuf {
    bridge_dir().join("aimail-bridge.log")
}

/// admin address used by bridge maintenance commands (`BRIDGE_ADDR` = default 38081; different from "declarative" `sync_route`:
/// `cmd_bridge` refresh **directly hits this fixed address**, copied from Python).
pub const BRIDGE_ADDR: &str = "127.0.0.1:38081";

/// `_bridge_pids`: exact match anchored on **executable file path** (no loose matching -- criteria from the two accidental kill incidents on 2026-08-16/09-21).
///
/// First try `pgrep -f '^[^ ]*/aimail-bridge( |$)'`; if host doesn't have procps installed (deerflow image, F12), fallback to scanning
/// **first token** of `/proc/<pid>/cmdline`, same criteria (ends with `aimail-bridge`) => precision unchanged, no crash.
pub fn bridge_pids() -> Vec<u32> {
    #[cfg(windows)]
    {
        // Windows: enumerate by dedicated process name (no equivalent to path first token criteria; single source of truth = procwin)
        return crate::core::procwin::bridge_pids_by_name();
    }
    #[cfg(unix)]
    {
        let is_bridge_token = |tok: &str| tok == "aimail-bridge" || tok.ends_with("/aimail-bridge");
        if let Ok(out) = std::process::Command::new("pgrep")
            .args(["-f", r"^[^ ]*/aimail-bridge( |$)"])
            .output()
        {
            if out.status.success() {
                let mut pids: Vec<u32> = String::from_utf8_lossy(&out.stdout)
                    .lines()
                    .filter_map(|l| l.trim().parse::<u32>().ok())
                    .collect();
                pids.sort_unstable();
                pids.dedup();
                return pids;
            }
            return Vec::new(); // pgrep exists, but no match found
        }
        let mut pids = Vec::new();
        if let Ok(entries) = std::fs::read_dir("/proc") {
            for e in entries.flatten() {
                let pid: u32 = match e.file_name().to_string_lossy().parse() {
                    Ok(p) => p,
                    Err(_) => continue,
                };
                let raw = match std::fs::read(e.path().join("cmdline")) {
                    Ok(b) => b,
                    Err(_) => continue,
                };
                let first = String::from_utf8_lossy(&raw)
                    .split('\u{0}')
                    .next()
                    .unwrap_or("")
                    .to_string();
                if is_bridge_token(&first) {
                    pids.push(pid);
                }
            }
        }
        pids.sort_unstable();
        pids.dedup();
        pids
    }
}

/// `_addr_clean`: address → directory key (`re.sub(r"[^\w.\-]", "_", email, flags=ASCII)`).
pub fn addr_clean(email: &str) -> String {
    email
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// `_default_recv_url`: platform default receiver (hermes => read webhook port from host config.yaml, default 8646,
/// path taken from the contract's hermes inbound path; otherwise openclaw's `127.0.0.1:8799/hook`).
pub fn default_recv_url(sid: &str, email: &str) -> String {
    let aj = crate::core::config::systems_root_in(&crate::core::home::aimail_home())
        .join(sid)
        .join(addr_clean(email))
        .join(crate::core::contract::binding_file());
    if let Ok(text) = std::fs::read_to_string(&aj) {
        if let Ok(v) = serde_json::from_str::<Value>(&text) {
            let wu = v.get("webhook_url").and_then(|x| x.as_str()).unwrap_or("");
            if !wu.is_empty() {
                return wu.to_string();
            }
        }
    }
    let cfg = crate::core::config::load_gateway_config(sid);
    if let Some(c) = cfg {
        let sh = c.system_home.clone();
        if !sh.is_empty() && std::path::Path::new(&sh).join("hermes-agent").exists() {
            let base = std::path::Path::new(&sh);
            let tail = email
                .split('@')
                .next()
                .unwrap_or("")
                .rsplit('.')
                .next()
                .unwrap_or("");
            let mut p = base.join("profiles").join(tail).join("config.yaml");
            if !p.exists() {
                p = base.join("config.yaml");
            }
            let port = std::fs::read_to_string(&p)
                .ok()
                .and_then(|t| yaml_rust2::YamlLoader::load_from_str(&t).ok())
                .and_then(|docs| docs.into_iter().next())
                .and_then(|doc| {
                    let wh = &doc["platforms"]["webhook"];
                    wh["port"].as_i64().or_else(|| wh["extra"]["port"].as_i64())
                })
                .unwrap_or(8646);
            // hermes's inbound path is a **contract value** (hermes is the only path exception) ⇒ use constant, do not write literal
            return format!(
                "http://127.0.0.1:{}{}",
                port,
                crate::core::contract::hermes_inbound_path()
            );
        }
    }
    "http://127.0.0.1:8799/hook".to_string()
}

/// `_system_agents`: `(email, receiver URL)` for all agents of this system.
/// Receiver **prioritizes the bound webhook_url** (single source of truth, includes platform real path);
/// falls back to historical routing table, then falls back to platform default (mirrors Python's three-level order).
pub fn system_agents(sid: &str) -> Vec<(String, String)> {
    let routes = read_routes(&routes_file());
    let mut out = Vec::new();
    for (em, wu) in registered_bindings(sid) {
        let target = if !wu.is_empty() {
            wu
        } else if let Some(r) = routes.get(&em) {
            r.clone()
        } else {
            default_recv_url(sid, &em)
        };
        out.push((em, target));
    }
    out
}
