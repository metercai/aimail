//! 网关 HTTP 客户端 —— 逐语义复刻 Python 侧 `_GatewayClient._request`
//! （`pysdk/aimail_tools.py:100-147`，CLI 侧经 `load_core()` 调同名实现）。
//!
//! 返回**同构 dict**（不是 Result）：这是被复刻对象的核心语义 —— 传输层失败
//! 也返回 `{"status": 0, "error": ...}` 而不是抛异常，调用方按 `status` 分档判读
//! （例如 `stats` 的云状态分档：200=ok / 403·404=unlinked / 0=保持原判）。
//!
//! | 场景 | 返回 |
//! |---|---|
//! | 2xx + JSON 对象 | `{"status": <code>, **body}`（body 自带的 `status` 被丢掉） |
//! | 2xx + JSON 数组 | `{"status": <code>, "data": [...]}` |
//! | 2xx + 非 JSON | `{"status": <code>, "body": "<原文>"}` |
//! | 4xx/5xx | `{"status": <code>, "error": "...", **err_body}` |
//! | 传输失败 | `{"status": 0, "error": "..."}` |
//!
//! 已知的两处**有意近似**（都在错误文案上，不影响分档判读）：
//! 1. HTTP 错误里的 `error` 文案：Python 是 urllib 的 `str(e)`（"HTTP Error 403:
//!    Forbidden"），Rust 侧给 `"HTTP Error <code>"`；带 JSON body 时服务端自己的
//!    `error` 字段会覆盖它（与 Python 的 `**err_body` 同序）；
//! 2. body 序列化：Python `json.dumps(body)` 带空格（`{"a": 1}`），Rust
//!    `serde_json::to_vec` 无空格（`{"a":1}`）—— 签名是按**实际发送的字节**算的，
//!    服务端按收到的字节验签，故两侧各自自洽；黄金向量测试用的是固定字节。

use crate::core::sig;
use serde_json::{json, Map, Value};
use std::time::Duration;

/// 网关客户端（可复用于 stats / domain / renew / welcome / check 的云探针）。
pub struct GatewayClient {
    base: String,
    api_key: String,
    identity: String,
    agent: ureq::Agent,
}

impl GatewayClient {
    /// `gateway_url` 末尾斜杠会被去掉（Python 同）；`timeout_secs` 对齐 Python 默认 30。
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

    /// 默认超时（Python `_GatewayClient(..., timeout=30)`）。
    pub fn with_defaults(gateway_url: &str, api_key: &str) -> Self {
        Self::new(gateway_url, api_key, "", 30)
    }

    /// GET（无 body）。
    pub fn get(&self, path: &str) -> Value {
        self.request("GET", path, None)
    }

    /// POST（JSON body）。
    pub fn post(&self, path: &str, body: &Value) -> Value {
        self.request("POST", path, Some(body))
    }

    /// 通用请求；返回同构 dict（见模块头部的分档表）。
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
            body.remove("status"); // 响应体不许覆盖 HTTP status（Python 同）
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
            // 服务端给了具体原因 ⇒ 它就是主文案（Python 的 **err_body 覆盖同序）
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
// 独立 API 函数（不需要 client 实例）—— `pysdk/gateway_api.py:118-161` 的 Rust 复刻
//   2026-10-04 owner 裁决：这两个调用 rust **原生**实现（它们只是"签名 + HTTP 端点"，
//   签名算法 rust 侧已在用且有 L0/L2 验证），不新增 SDK 公共面、不因此发版。
// ════════════════════════════════════════════════════════════════════════════

/// `whoami(gw, ak, identity="")`（`gateway_api.py:118-130`）：`GET /api/v1/whoami`。
///
/// **任何失败都返回空对象**（Python 把所有异常都吞成 `{}`：4xx/5xx 的 HTTPError、
/// 传输失败、2xx 但非 JSON 一律如此）⇒ 调用方只能"拿不到元数据"，不能据此判失败。
pub fn whoami(gateway_url: &str, api_key: &str, identity: &str) -> Value {
    let path = "/api/v1/whoami";
    // 注意：signed_headers 已按 Python 口径**总是**带 Content-Type（GET 也一样）
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
/// `POST /api/v1/admin/api-keys`，体 = `{system_id, email_address, scopes, category}`
/// （**键序照抄**，签名按实际发送字节算），identity = `system_id`。
///
/// 返回形状照抄 Python：2xx ⇒ 原始体；HTTP 错误 ⇒ `{raw_key:"", error, detail, status}`；
/// 其它异常 ⇒ `{raw_key:"", error: str(e)}`。
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
    // Content-Type 由 signed_headers 提供（与 Python 的 compute_api_signature 同）
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connection_refused_is_status_zero_not_a_panic() {
        // 无人监听的本地端口 ⇒ 传输失败 ⇒ {"status": 0, "error": ...}
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
