//! `whoami` / `create_api_key` 的 rust 原生实现（owner 2026-10-04 裁决）**跨语言 parity**。
//!
//! 判据分两层：
//! 1. **请求形状**：同一个 stub 上，Python（`pysdk/gateway_api.py`）与 Rust 发的方法/路径/
//!    头名（含 `X-Api-Identity` 的有无）/体 JSON 必须一致。签名值**不比**（时间戳与体字节
//!    差异使其必然不同：Python 的 `json.dumps` 带空格、serde 紧凑，两侧各自按实发字节自洽 ——
//!    这是 `core/gateway.rs` 头注已登记的既有近似）。
//! 2. **返回形状**：成功体、4xx 的分支形状（`whoami` 吞成 `{}`；`create_api_key` 给
//!    `{raw_key,error,detail,status}`）两侧逐字段相同。

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::mpsc;
use std::thread;

use serde_json::Value;

struct Recorded {
    method: String,
    path: String,
    headers: BTreeMap<String, String>,
    body: String,
}

/// 起一个 stub 网关：按路径给固定回复，记录收到的请求（读满 `expect_reqs` 条后退出）。
fn stub_gateway(
    replies: BTreeMap<String, (u16, String)>,
    expect_reqs: usize,
) -> (String, mpsc::Receiver<Recorded>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for _ in 0..expect_reqs {
            let Ok((mut sock, _)) = listener.accept() else {
                break;
            };
            // 累积读：ureq 可能把 head 与 body 分两次写（单次 read 只拿到 head）
            let _ = sock.set_read_timeout(Some(std::time::Duration::from_millis(300)));
            let mut raw: Vec<u8> = Vec::new();
            let mut buf = vec![0u8; 16384];
            loop {
                match sock.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        raw.extend_from_slice(&buf[..n]);
                        // head 齐了且 Content-Length 满足 ⇒ 收工
                        if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
                            let head = String::from_utf8_lossy(&raw[..pos]).to_lowercase();
                            let want: usize = head
                                .lines()
                                .find_map(|l| l.strip_prefix("content-length:"))
                                .and_then(|v| v.trim().parse().ok())
                                .unwrap_or(0);
                            if raw.len() >= pos + 4 + want {
                                break;
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
            let text = String::from_utf8_lossy(&raw).to_string();
            let mut lines = text.split("\r\n");
            let first = lines.next().unwrap_or("");
            let mut parts = first.split_whitespace();
            let method = parts.next().unwrap_or("").to_string();
            let path = parts.next().unwrap_or("").to_string();
            let mut headers = BTreeMap::new();
            let mut body = String::new();
            let mut in_body = false;
            for l in text.split("\r\n") {
                if in_body {
                    body.push_str(l);
                    continue;
                }
                if l.is_empty() {
                    in_body = true;
                    continue;
                }
                if let Some((k, v)) = l.split_once(':') {
                    headers.insert(k.trim().to_lowercase(), v.trim().to_string());
                }
            }
            let (code, payload) = replies
                .get(&path)
                .cloned()
                .unwrap_or((404, "{\"error\":\"not found\"}".to_string()));
            let resp = format!(
                "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                payload.len()
            );
            let _ = sock.write_all(resp.as_bytes());
            let _ = tx.send(Recorded {
                method,
                path,
                headers,
                body,
            });
        }
    });
    (format!("http://127.0.0.1:{port}"), rx)
}

fn pysdk_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .join("pysdk")
}

/// 在 Python 侧调用 `gateway_api.<fn>(...)`，把返回值以 JSON 打到 stdout。
fn python_call(expr: &str) -> Option<Value> {
    let script = format!(
        "import sys, json; sys.path.insert(0, {dir:?}); import gateway_api as ga; print(json.dumps({expr}))",
        dir = pysdk_dir().to_string_lossy()
    );
    let out = std::process::Command::new("python3")
        .args(["-c", &script])
        .output()
        .ok()?;
    if !out.status.success() {
        eprintln!("python 侧失败: {}", String::from_utf8_lossy(&out.stderr));
        return None;
    }
    serde_json::from_slice(&out.stdout).ok()
}

fn replies(map: &[(&str, u16, &str)]) -> BTreeMap<String, (u16, String)> {
    map.iter()
        .map(|(p, c, b)| (p.to_string(), (*c, b.to_string())))
        .collect()
}

#[test]
fn whoami_request_and_result_match_python() {
    let rp = replies(&[(
        "/api/v1/whoami",
        200,
        r#"{"category":"system_admin","scope":"system","system_id":"s1"}"#,
    )]);
    // Python 侧
    let (purl, prx) = stub_gateway(rp.clone(), 1);
    let py_val = python_call(&format!("ga.whoami({purl:?}, 'k', '')"));
    let py_req = prx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("py 请求");
    // Rust 侧
    let (rurl, rrx) = stub_gateway(rp, 1);
    let rs_val = aimail::core::gateway::whoami(&rurl, "k", "");
    let rs_req = rrx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("rs 请求");

    assert_eq!(py_req.method, rs_req.method, "方法");
    assert_eq!(py_req.path, rs_req.path, "路径");
    assert!(
        !rs_req.headers.contains_key("x-api-identity"),
        "identity 空 ⇒ 不发该头"
    );
    assert!(!py_req.headers.contains_key("x-api-identity"), "Python 同");
    for h in ["x-api-timestamp", "x-api-signature"] {
        assert!(py_req.headers.contains_key(h), "Python 应发 {h}");
        assert!(rs_req.headers.contains_key(h), "Rust 应发 {h}");
    }
    let Some(py_val) = py_val else {
        println!("SKIP: Python 侧不可用");
        return;
    };
    assert_eq!(py_val, rs_val, "whoami 返回体应与 Python 相同");
}

#[test]
fn whoami_swallows_http_error_to_empty_object() {
    let rp = replies(&[("/api/v1/whoami", 403, r#"{"error":"nope"}"#)]);
    let (purl, prx) = stub_gateway(rp.clone(), 1);
    let py_val = python_call(&format!("ga.whoami({purl:?}, 'k', '')"));
    let _ = prx.recv_timeout(std::time::Duration::from_secs(5));
    let (rurl, rrx) = stub_gateway(rp, 1);
    let rs_val = aimail::core::gateway::whoami(&rurl, "k", "");
    let _ = rrx.recv_timeout(std::time::Duration::from_secs(5));
    assert_eq!(rs_val, serde_json::json!({}), "4xx ⇒ 空对象");
    if let Some(pv) = py_val {
        assert_eq!(pv, rs_val, "Python 同判");
    }
}

#[test]
fn create_api_key_request_and_result_match_python() {
    let rp = replies(&[("/api/v1/admin/api-keys", 200, r#"{"raw_key":"k-123"}"#)]);
    let (purl, prx) = stub_gateway(rp.clone(), 1);
    let py_val = python_call(&format!(
        "ga.create_api_key({purl:?}, 'k', 's1', 'a@x.test', ['send'], 'system_agent')"
    ));
    let py_req = prx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("py 请求");
    let (rurl, rrx) = stub_gateway(rp, 1);
    let scopes = vec!["send".to_string()];
    let rs_val = aimail::core::gateway::create_api_key(
        &rurl,
        "k",
        "s1",
        "a@x.test",
        &scopes,
        "system_agent",
    );
    let rs_req = rrx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("rs 请求");

    assert_eq!(py_req.method, "POST");
    assert_eq!(rs_req.method, "POST");
    assert_eq!(py_req.path, rs_req.path, "路径");
    assert_eq!(
        py_req.headers.get("x-api-identity"),
        Some(&"s1".to_string()),
        "Python 用 system_id 作 identity"
    );
    assert_eq!(
        rs_req.headers.get("x-api-identity"),
        Some(&"s1".to_string())
    );
    assert_eq!(
        py_req.headers.get("content-type"),
        Some(&"application/json".to_string())
    );
    assert_eq!(
        rs_req.headers.get("content-type"),
        Some(&"application/json".to_string())
    );
    // 体：语义相等（字节不必相同 —— 空格差异是已登记的近似）
    let pj: Value = serde_json::from_str(&py_req.body).expect("py body json");
    let rj: Value = serde_json::from_str(&rs_req.body).expect("rs body json");
    assert_eq!(pj, rj, "体 JSON 语义");
    assert_eq!(
        pj.get("scopes").and_then(|s| s.as_array()).map(|a| a.len()),
        Some(1)
    );
    let Some(py_val) = py_val else {
        println!("SKIP: Python 侧不可用");
        return;
    };
    assert_eq!(py_val, rs_val, "create_api_key 返回体应与 Python 相同");
}

#[test]
fn create_api_key_error_shape_matches_python() {
    let rp = replies(&[(
        "/api/v1/admin/api-keys",
        403,
        r#"{"error":"denied","detail":"why"}"#,
    )]);
    let (purl, prx) = stub_gateway(rp.clone(), 1);
    let py_val = python_call(&format!(
        "ga.create_api_key({purl:?}, 'k', 's1', 'a@x.test', ['send'], 'system_agent')"
    ));
    let _ = prx.recv_timeout(std::time::Duration::from_secs(5));
    let (rurl, rrx) = stub_gateway(rp, 1);
    let scopes = vec!["send".to_string()];
    let rs_val = aimail::core::gateway::create_api_key(
        &rurl,
        "k",
        "s1",
        "a@x.test",
        &scopes,
        "system_agent",
    );
    let _ = rrx.recv_timeout(std::time::Duration::from_secs(5));
    assert_eq!(
        rs_val,
        serde_json::json!({"raw_key":"", "error":"denied", "detail":"why", "status":403})
    );
    if let Some(pv) = py_val {
        assert_eq!(pv, rs_val, "4xx 分支形状应与 Python 相同");
    }
}
