//! 最简 JSON 请求助手 —— `cli/check_status.py:830-845`（`_json_req`）的逐语义复刻。
//!
//! 返回 `(status, parsed)`，三种分档必须一致：
//! - 2xx ⇒ `(status, body 的 JSON)`；
//! - HTTP 错误（4xx/5xx）⇒ `(code, body 的 JSON 或 {})`；
//! - 其它异常（含"2xx 但不是合法 JSON"）⇒ `(0, {"error": ...})`。
//!
//! 最后一条容易漏：Python 的 `json.loads(r.read())` 在同一个 try 里，解析失败会落到
//! `except Exception` 分支 ⇒ 状态码变 0。照抄，不要"顺手"保留 200。

use std::time::Duration;

use serde_json::{json, Value};

/// `data` 为 `None` 且 `method` 为 `None` 时按 urllib 语义取 GET，否则 POST。
pub fn json_req(
    url: &str,
    headers: &[(&str, String)],
    data: Option<&[u8]>,
    method: Option<&str>,
    timeout_secs: u64,
) -> (u16, Value) {
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(timeout_secs.max(1)))
        .build();
    let verb = method.unwrap_or(if data.is_some() { "POST" } else { "GET" });
    let mut req = agent.request(verb, url);
    for (k, v) in headers {
        req = req.set(k, v);
    }
    let result = match data {
        Some(body) => req.send_bytes(body),
        None => req.call(),
    };
    match result {
        Ok(resp) => {
            let status = resp.status();
            let text = resp.into_string().unwrap_or_default();
            match serde_json::from_str::<Value>(&text) {
                Ok(v) => (status, v),
                Err(e) => (0, json!({ "error": e.to_string() })),
            }
        }
        Err(ureq::Error::Status(code, resp)) => {
            let text = resp.into_string().unwrap_or_default();
            match serde_json::from_str::<Value>(&text) {
                Ok(v) => (code, v),
                Err(_) => (code, json!({})),
            }
        }
        Err(e) => (0, json!({ "error": e.to_string() })),
    }
}

/// 原始请求（不解析 JSON）—— 供 L4 hook 探测这类"只看状态码/原文"的调用。
///
/// 对应 Python 的 `urllib.request.urlopen(...)`：**HTTP 错误也算 Ok**（状态码可达，
/// 由调用方按 code 分档），只有传输层失败才 Err（Python 的 `except Exception`）。
pub fn raw_req(
    url: &str,
    data: Option<&[u8]>,
    content_type: Option<&str>,
    timeout_secs: u64,
) -> Result<(u16, String), String> {
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(timeout_secs.max(1)))
        .build();
    let method = if data.is_some() { "POST" } else { "GET" };
    let mut req = agent.request(method, url);
    if let Some(ct) = content_type {
        req = req.set("Content-Type", ct);
    }
    let result = match data {
        Some(body) => req.send_bytes(body),
        None => req.call(),
    };
    match result {
        Ok(resp) => {
            let status = resp.status();
            let text = resp.into_string().unwrap_or_default();
            Ok((status, text))
        }
        Err(ureq::Error::Status(code, resp)) => {
            let text = resp.into_string().unwrap_or_default();
            Ok((code, text))
        }
        Err(e) => Err(e.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    fn serve(code: u16, body: &'static str) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut s) = stream else { continue };
                let mut buf = [0u8; 2048];
                let _ = s.read(&mut buf);
                let head = format!(
                    "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = s.write_all(head.as_bytes());
                let _ = s.write_all(body.as_bytes());
                let _ = s.flush();
            }
        });
        port
    }

    #[test]
    fn ok_json_is_returned_with_status() {
        let port = serve(200, r#"{"uptime_secs":42}"#);
        let (code, body) = json_req(
            &format!("http://127.0.0.1:{port}/health"),
            &[],
            None,
            None,
            5,
        );
        assert_eq!(code, 200);
        assert_eq!(body.get("uptime_secs").and_then(Value::as_u64), Some(42));
    }

    #[test]
    fn http_error_keeps_code_and_parses_body() {
        let port = serve(404, r#"{"error":"nope"}"#);
        let (code, body) = json_req(&format!("http://127.0.0.1:{port}/x"), &[], None, None, 5);
        assert_eq!(code, 404);
        assert_eq!(body.get("error").and_then(Value::as_str), Some("nope"));
    }

    #[test]
    fn non_json_2xx_falls_back_to_status_zero_like_python() {
        // Python 在同一 try 里 json.loads ⇒ 解析失败落到 except ⇒ (0, {"error": ...})
        let port = serve(200, "not json at all");
        let (code, body) = json_req(&format!("http://127.0.0.1:{port}/x"), &[], None, None, 5);
        assert_eq!(code, 0);
        assert!(body.get("error").and_then(Value::as_str).is_some());
    }

    #[test]
    fn transport_error_is_status_zero_with_error_key() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let (code, body) = json_req(&format!("http://127.0.0.1:{port}/x"), &[], None, None, 2);
        assert_eq!(code, 0);
        assert!(body.get("error").and_then(Value::as_str).is_some());
    }
}
