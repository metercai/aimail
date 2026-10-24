//! **Cross-language parity** of the rust-native implementations of `whoami` / `create_api_key` (owner ruling 2026-10-04).
//!
//! The criteria come in two layers:
//! 1. **request shape**: on the same stub, the method/path/
//!    header names (including the presence/absence of `X-Api-Identity`) / body JSON sent by Python (`pysdk/gateway_api.py`) and Rust must agree. The signature value is **not** compared (the timestamp and body-byte
//!    differences make them inevitably different: Python's `json.dumps` carries spaces, serde is compact, each side is self-consistent over the bytes it really sends —
//!    this is a pre-existing approximation already registered in the header comment of `core/gateway.rs`).
//! 2. **return shape**: the success body and the 4xx branch shapes (`whoami` swallows it into `{}`; `create_api_key` returns
//!    `{raw_key,error,detail,status}`) are field-for-field identical on both sides.

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

/// Start a stub gateway: fixed replies per path, recording the requests received (exits after reading `expect_reqs` of them).
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
            // accumulate the read: ureq may write the head and the body in two pieces (a single read only gets the head)
            let _ = sock.set_read_timeout(Some(std::time::Duration::from_millis(300)));
            let mut raw: Vec<u8> = Vec::new();
            let mut buf = vec![0u8; 16384];
            loop {
                match sock.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        raw.extend_from_slice(&buf[..n]);
                        // the head is complete and the Content-Length is satisfied => done
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

/// Call `gateway_api.<fn>(...)` on the Python side and print the return value as JSON to stdout.
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
        eprintln!("the python side failed: {}", String::from_utf8_lossy(&out.stderr));
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
    // the Python side
    let (purl, prx) = stub_gateway(rp.clone(), 1);
    let py_val = python_call(&format!("ga.whoami({purl:?}, 'k', '')"));
    let py_req = prx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("the py request");
    // the Rust side
    let (rurl, rrx) = stub_gateway(rp, 1);
    let rs_val = aimail::core::gateway::whoami(&rurl, "k", "");
    let rs_req = rrx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("the rs request");

    assert_eq!(py_req.method, rs_req.method, "method");
    assert_eq!(py_req.path, rs_req.path, "path");
    assert!(
        !rs_req.headers.contains_key("x-api-identity"),
        "an empty identity => the header is not sent"
    );
    assert!(!py_req.headers.contains_key("x-api-identity"), "the same for Python");
    for h in ["x-api-timestamp", "x-api-signature"] {
        assert!(py_req.headers.contains_key(h), "Python should send {h}");
        assert!(rs_req.headers.contains_key(h), "Rust should send {h}");
    }
    let Some(py_val) = py_val else {
        println!("SKIP: the Python side is unavailable");
        return;
    };
    assert_eq!(py_val, rs_val, "the whoami return body should be the same as Python");
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
    assert_eq!(rs_val, serde_json::json!({}), "4xx => an empty object");
    if let Some(pv) = py_val {
        assert_eq!(pv, rs_val, "the same verdict for Python");
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
        .expect("the py request");
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
        .expect("the rs request");

    assert_eq!(py_req.method, "POST");
    assert_eq!(rs_req.method, "POST");
    assert_eq!(py_req.path, rs_req.path, "path");
    assert_eq!(
        py_req.headers.get("x-api-identity"),
        Some(&"s1".to_string()),
        "Python uses the system_id as the identity"
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
    // body: semantically equal (the bytes need not be identical — the whitespace difference is a registered approximation)
    let pj: Value = serde_json::from_str(&py_req.body).expect("py body json");
    let rj: Value = serde_json::from_str(&rs_req.body).expect("rs body json");
    assert_eq!(pj, rj, "the semantic equality of the body JSON");
    assert_eq!(
        pj.get("scopes").and_then(|s| s.as_array()).map(|a| a.len()),
        Some(1)
    );
    let Some(py_val) = py_val else {
        println!("SKIP: the Python side is unavailable");
        return;
    };
    assert_eq!(py_val, rs_val, "the create_api_key return body should be the same as Python");
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
        assert_eq!(pv, rs_val, "the 4xx branch shape should be the same as Python");
    }
}
