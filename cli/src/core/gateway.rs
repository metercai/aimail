//! gateway HTTP client — semantic line-by-line re-implementation of the Python side `_GatewayClient._request`
//! (`pysdk/aimail_tools.py:100-147`, CLI side calls same-named implementation via `load_core()`).
//!
//! Returns **isomorphic dict** (not Result): this is the core semantics of the replicated object -- transport layer failure
//! also returns `{"status": 0, "error": ...}` instead of throwing exception, caller judges by `status` tier
//! (e.g. `stats` cloud status tiering: 200=ok / 403·404=unlinked / 0=keep original judgment).
//!
//! | Scenario | Return |
//! |---|---|
//! | 2xx + JSON object | `{"status": <code>, **body}` (body's own `status` is dropped) |
//! | 2xx + JSON array | `{"status": <code>, "data": [...]}` |
//! | 2xx + non-JSON | `{"status": <code>, "body": "<original text>"}` |
//! | 4xx/5xx | `{"status": <code>, "error": "...", **err_body}` |
//! | Transport failure | `{"status": 0, "error": "..."}` |
//!
//! Two known **intentional approximations** (both in error messages, do not affect tier judgment):
//! 1. `error` message in HTTP errors: Python uses urllib's `str(e)` ("HTTP Error 403:
//!    Forbidden"), Rust side gives `"HTTP Error <code>"`; when JSON body is present, server's own
//!    `error` field overrides it (same order as Python's `**err_body`);
//! 2. body serialization: Python `json.dumps(body)` has spaces (`{"a": 1}`), Rust
//!    `serde_json::to_vec` no spaces (`{"a":1}`) -- signature is calculated based on **actual sent bytes**,
//!    server verifies signature based on received bytes, so both sides are self-consistent; golden vector tests use fixed bytes.

use crate::core::sig;
use serde_json::{json, Map, Value};
use std::time::Duration;

/// Gateway client (reusable for stats / domain / renew / welcome / check cloud probes).
pub struct GatewayClient {
    base: String,
    api_key: String,
    identity: String,
    agent: ureq::Agent,
}

impl GatewayClient {
    /// Trailing slash in `gateway_url` is removed (same as Python); `timeout_secs` aligns with Python default 30.
    pub fn new(gateway_url: &str, api_key: &str, identity: &str, timeout_secs: u64) -> Self {
        let agent = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(timeout_secs))
            .build();
        Self {
            base: gateway_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
            identity: identity.to_string(),
            agent,
        }
    }

    /// Default timeout (Python `_GatewayClient(..., timeout=30)`).
    pub fn with_defaults(gateway_url: &str, api_key: &str) -> Self {
        Self::new(gateway_url, api_key, "", 30)
    }

    /// GET (no body).
    pub fn get(&self, path: &str) -> Value {
        self.request("GET", path, None)
    }

    /// POST（JSON body）。
    pub fn post(&self, path: &str, body: &Value) -> Value {
        self.request("POST", path, Some(body))
    }

    /// Generic request; returns isomorphic dict (see tier table in module header).
    pub fn request(&self, method: &str, path: &str, body: Option<&Value>) -> Value {
        let url = format!("{}{}", self.base, path);
        let data: Option<Vec<u8>> =
            body.map(|b| serde_json::to_vec(b).unwrap_or_else(|_| b.to_string().into_bytes()));

        let mut req = self.agent.request(method, &url);
        req = req.set("Accept", "application/json");
        if data.is_some() {
            req = req.set("Content-Type", "application/json");
        }
        if !self.identity.is_empty() {
            req = req.set("X-Api-Identity", &self.identity);
        }
        if let Some(s) = sig::compute(
            &self.api_key,
            method,
            path,
            data.as_deref().unwrap_or(b""),
            None,
        ) {
            req = req
                .set("X-Api-Timestamp", &s.timestamp)
                .set("X-Api-Signature", &s.signature);
        }

        let sent = match &data {
            Some(d) => req.send_bytes(d),
            None => req.call(),
        };
        match sent {
            Ok(resp) => {
                let status = resp.status() as u64;
                let text = resp.into_string().unwrap_or_default();
                success_body(status, &text)
            }
            Err(ureq::Error::Status(code, resp)) => {
                let text = resp.into_string().unwrap_or_default();
                error_body(code as u64, &text)
            }
            Err(e) => json_err(0, &e.to_string()),
        }
    }
}

fn obj_with_status(status: u64) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("status".into(), Value::from(status));
    m
}

fn json_err(status: u64, msg: &str) -> Value {
    let mut m = obj_with_status(status);
    m.insert("error".into(), Value::from(msg));
    Value::Object(m)
}

fn success_body(status: u64, text: &str) -> Value {
    match serde_json::from_str::<Value>(text) {
        Ok(Value::Array(a)) => {
            let mut m = obj_with_status(status);
            m.insert("data".into(), Value::Array(a));
            Value::Object(m)
        }
        Ok(Value::Object(mut body)) => {
            body.remove("status"); // Response body must not override HTTP status (same as Python)
            let mut m = obj_with_status(status);
            for (k, v) in body {
                m.insert(k, v);
            }
            Value::Object(m)
        }
        Ok(_) => json_err(0, "unexpected JSON body type (not object/array)"),
        Err(_) => {
            let mut m = obj_with_status(status);
            m.insert("body".into(), Value::from(text));
            Value::Object(m)
        }
    }
}

fn error_body(status: u64, text: &str) -> Value {
    let mut m = obj_with_status(status);
    let mut err = format!("HTTP Error {status}");
    if let Ok(Value::Object(mut body)) = serde_json::from_str::<Value>(text) {
        body.remove("status");
        if let Some(Value::String(s)) = body.get("error") {
            // Server gave specific reason ⇒ it is the main message (Python's **err_body override same order)
            err = s.clone();
        }
        m.insert("error".into(), Value::from(err));
        for (k, v) in body {
            m.insert(k, v);
        }
    } else {
        m.insert("error".into(), Value::from(err));
    }
    Value::Object(m)
}

// ════════════════════════════════════════════════════════════════════════════
// Standalone API functions (no client instance needed) -- Rust replication of `pysdk/gateway_api.py:118-161`
//   2026-10-04 owner ruling: these two calls are implemented in rust **natively** (they are just "signature + HTTP endpoint",
//   signature algorithm is already used on rust side and has L0/L2 verification), no new SDK public surface, no release for this.
// ════════════════════════════════════════════════════════════════════════════

/// `whoami(gw, ak, identity="")`（`gateway_api.py:118-130`）：`GET /api/v1/whoami`。
///
/// **Any failure returns empty object** (Python swallows all exceptions into `{}`: 4xx/5xx HTTPError,
/// transport failure, 2xx but non-JSON all behave this way) ⇒ caller can only "not get metadata", cannot judge failure based on this.
pub fn whoami(gateway_url: &str, api_key: &str, identity: &str) -> Value {
    let path = "/api/v1/whoami";
    // Note: signed_headers **always** include Content-Type per Python criteria (same for GET)
    let headers = sig::signed_headers(api_key, "GET", path, None, identity);
    let url = format!("{}{}", gateway_url.trim_end_matches('/'), path);
    let (status, v) = crate::core::http::json_req(&url, &headers, None, Some("GET"), 10);
    if (200..300).contains(&status) {
        v
    } else {
        Value::Object(Map::new())
    }
}

/// `create_api_key(gw, ak, system_id, email, scopes, category)`（`:133-161`）：
/// `POST /api/v1/admin/api-keys`, body = `{system_id, email_address, scopes, category}`
/// (**key order copied exactly**, signature calculated based on actual sent bytes), identity = `system_id`.
///
/// Return shape copied from Python: 2xx ⇒ raw body; HTTP error ⇒ `{raw_key:"", error, detail, status}`;
/// Other exceptions ⇒ `{raw_key:"", error: str(e)}`.
pub fn create_api_key(
    gateway_url: &str,
    api_key: &str,
    system_id: &str,
    email: &str,
    scopes: &[String],
    category: &str,
) -> Value {
    let path = "/api/v1/admin/api-keys";
    let mut body = Map::new();
    body.insert("system_id".into(), Value::String(system_id.to_string()));
    body.insert("email_address".into(), Value::String(email.to_string()));
    body.insert(
        "scopes".into(),
        Value::Array(scopes.iter().map(|s| Value::String(s.clone())).collect()),
    );
    body.insert("category".into(), Value::String(category.to_string()));
    let data = serde_json::to_vec(&Value::Object(body)).unwrap_or_default();
    // Content-Type is provided by signed_headers (same as Python's compute_api_signature)
    let headers = sig::signed_headers(api_key, "POST", path, Some(&data), system_id);
    let url = format!("{}{}", gateway_url.trim_end_matches('/'), path);
    let (status, v) = crate::core::http::json_req(&url, &headers, Some(&data), Some("POST"), 10);
    if (200..300).contains(&status) {
        return v;
    }
    if status == 0 {
        let err = v
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        return json!({ "raw_key": "", "error": err });
    }
    let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    json!({
        "raw_key": "",
        "error": s("error"),
        "detail": s("detail"),
        "status": status,
    })
}

/// `cli/_common.is_local_gateway`: whether the gateway is local/on this machine (direct push ⇒ no bridge needed).
///
/// Criteria copied verbatim: hostname ∈ {127.0.0.1, localhost, ::1}, **or** host matches a local address
/// (Python side uses `socket.getaddrinfo(socket.gethostname())`; here we use an equivalent implementation with std's parser).
/// Unresolvable urls are **not** considered local (callers must handle "cannot determine" themselves; defaulting to direct connection is not allowed).
pub fn is_local_gateway(url: &str) -> bool {
    let host = crate::core::repair::url_host_pub(url);
    if host.is_empty() {
        return false;
    }
    if matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        return true;
    }
    #[cfg(windows)]
    let name_opt: Option<String> = crate::core::procwin::hostname();
    #[cfg(not(windows))]
    let name_opt: Option<String> = std::fs::read_to_string("/proc/sys/kernel/hostname")
        .ok()
        .map(|s| s.trim().to_string());
    let Some(name) = name_opt else {
        return false;
    };
    if name.is_empty() {
        return false;
    }
    use std::net::ToSocketAddrs;
    match (name.as_str(), 0u16).to_socket_addrs() {
        Ok(addrs) => addrs.into_iter().any(|a| a.ip().to_string() == host),
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connection_refused_is_status_zero_not_a_panic() {
        // Local port with no listener ⇒ transmission failure ⇒ {"status": 0, "error": ...}
        let c = GatewayClient::new("http://127.0.0.1:1", "k", "", 5);
        let r = c.get("/api/v1/quotas");
        assert_eq!(r.get("status").and_then(Value::as_u64), Some(0), "{r}");
        assert!(r.get("error").and_then(Value::as_str).is_some(), "{r}");
    }

    #[test]
    fn trailing_slash_is_stripped_like_python() {
        let c = GatewayClient::new("http://127.0.0.1:1/", "k", "", 1);
        assert_eq!(c.base, "http://127.0.0.1:1");
    }

    #[test]
    fn response_status_key_cannot_be_overwritten_by_body() {
        let v = success_body(200, r#"{"status": 999, "expires_at": "2030-01-01"}"#);
        assert_eq!(v.get("status").and_then(Value::as_u64), Some(200));
        assert_eq!(
            v.get("expires_at").and_then(Value::as_str),
            Some("2030-01-01")
        );
    }

    #[test]
    fn json_array_is_wrapped_into_data_like_python() {
        let v = success_body(200, "[{\"domain\": \"x.test\"}]");
        assert_eq!(v.get("status").and_then(Value::as_u64), Some(200));
        assert!(v.get("data").and_then(Value::as_array).is_some(), "{v}");
    }

    #[test]
    fn plain_text_body_is_reported_verbatim() {
        let v = success_body(200, "not json");
        assert_eq!(v.get("body").and_then(Value::as_str), Some("not json"));
    }

    #[test]
    fn http_error_keeps_server_error_text() {
        let v = error_body(404, r#"{"error": "route not registered", "detail": "x"}"#);
        assert_eq!(v.get("status").and_then(Value::as_u64), Some(404));
        assert_eq!(
            v.get("error").and_then(Value::as_str),
            Some("route not registered")
        );
        assert_eq!(v.get("detail").and_then(Value::as_str), Some("x"));
    }
}
