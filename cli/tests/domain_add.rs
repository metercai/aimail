//! P4 slice 1 acceptance: `aimail domain --add` (the domain-create surface) — stub gateway + request-shape assertions.
//!
//! Criteria (aligned item by item with the create branch of `cli/aimail:cmd_domain`):
//! ① an illegal domain (empty / contains `@` / contains no `.`) => **no request sent**, the wording printed **as the original**;
//! ② legal => `POST /api/v1/admin/systems/{sid}/domains`, body = `{id, domain(lowercase-normalized)}`,
//!   `webhook_url` is only carried when `-w` has a value; `id` omitted => `d{epoch}`;
//! ③ the response carries `error` => `create failed: {error} {detail}` (`.strip()`) and rc=1;
//! ④ success => `domain created: {domain} (system {sid})` rc=0;
//! ⑤ the validation order: existence/credentials first, then domain legality (same as Python).

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::thread;

fn rust_bin() -> String {
    format!("{}/target/debug/aimail", env!("CARGO_MANIFEST_DIR"))
}

fn run(home: &Path, args: &[&str]) -> (i32, String) {
    let out = std::process::Command::new(rust_bin())
        .args(args)
        .env("AIMAIL_HOME", home)
        .env("AIMAIL_PROG_DIR", home.join("prog"))
        .env("HOME", home.join("uhome"))
        .output()
        .expect("run aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
    )
}

/// The stub gateway: answers a fixed JSON (given by the caller) and sends (METHOD, PATH, BODY) back over the channel.
fn stub(reply: &'static str) -> (u16, std::sync::mpsc::Receiver<(String, String, String)>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut sock = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            let mut buf = Vec::new();
            let mut tmp = [0u8; 4096];
            let mut need = None;
            loop {
                let n = match sock.read(&mut tmp) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(_) => break,
                };
                buf.extend_from_slice(&tmp[..n]);
                if need.is_none() {
                    if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                        let head = String::from_utf8_lossy(&buf[..i]).to_lowercase();
                        need = Some(
                            head.lines()
                                .find_map(|l| l.strip_prefix("content-length:"))
                                .and_then(|v| v.trim().parse::<usize>().ok())
                                .unwrap_or(0),
                        );
                    }
                }
                if let (Some(n), Some(i)) = (need, buf.windows(4).position(|w| w == b"\r\n\r\n")) {
                    if buf.len() >= i + 4 + n {
                        break;
                    }
                }
            }
            let text = String::from_utf8_lossy(&buf).to_string();
            let first = text.lines().next().unwrap_or("").to_string();
            let mut it = first.split_whitespace();
            let method = it.next().unwrap_or("").to_string();
            let path = it.next().unwrap_or("").to_string();
            let body = text
                .split_once("\r\n\r\n")
                .map(|(_, b)| b.to_string())
                .unwrap_or_default();
            let _ = tx.send((method, path, body));
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                reply.len(),
                reply
            );
            let _ = sock.write_all(resp.as_bytes());
            let _ = sock.flush();
        }
    });
    (port, rx)
}

fn fixture(gw: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("systems/s1")).unwrap();
    std::fs::create_dir_all(home.join("uhome")).unwrap();
    std::fs::write(
        home.join("systems/s1/aimail_gateway.json"),
        format!(
            r#"{{"gateway_url":"{}","admin_key":"AK","system_id":"s1","domain":"example.test","webhook_host":""}}"#,
            gw
        ),
    )
    .unwrap();
    tmp
}

#[test]
fn domain_add_creates_and_rejects() {
    // ①+⑤ an illegal domain: no request sent (validation first)
    let (port, rx) = stub(r#"{"ok":true}"#);
    let gw = format!("http://127.0.0.1:{}", port);
    let tmp = fixture(&gw);
    let home = tmp.path().join("ahome");
    for bad in ["", "no-dot", "has@at.test"] {
        let (rc, out) = run(&home, &["domain", "-s", "s1", "-a", bad]);
        assert_eq!(rc, 1, "bad={bad} out={out}");
        assert!(
            out.contains(&format!("Invalid domain: '{}'", bad)),
            "bad={bad} out={out}"
        );
    }
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(300))
            .is_err(),
        "an illegal domain must not send any request"
    );

    // ② a legal creation: the POST shape + lowercase normalization + id omitted => d{epoch}
    let (port2, rx2) = stub(r#"{"status":200,"domain":"new.test"}"#);
    let tmp2 = fixture(&format!("http://127.0.0.1:{}", port2));
    let (rc2, out2) = run(
        &tmp2.path().join("ahome"),
        &["domain", "-s", "s1", "-a", "  New.TEST  "],
    );
    assert_eq!(rc2, 0, "{out2}");
    assert!(
        out2.contains("domain created: new.test (system s1)"),
        "{out2}"
    );
    let (m, p, b) = rx2.recv_timeout(std::time::Duration::from_secs(3)).unwrap();
    assert_eq!(m, "POST");
    assert_eq!(p, "/api/v1/admin/systems/s1/domains");
    assert!(b.contains(r#""domain":"new.test""#), "{b}");
    assert!(b.contains(r#""id":"d"#), "an omitted id should generate d{{epoch}}: {b}");
    assert!(!b.contains("webhook_url"), "no -w given, so the field must not be present: {b}");

    // ③ carried only when -w has a value; an explicit --id takes effect
    let (port3, rx3) = stub(r#"{"status":200}"#);
    let tmp3 = fixture(&format!("http://127.0.0.1:{}", port3));
    let (rc3, _) = run(
        &tmp3.path().join("ahome"),
        &[
            "domain",
            "-s",
            "s1",
            "-a",
            "w.test",
            "--id",
            "dX",
            "-w",
            &format!(
                "http://127.0.0.1:9101{}",
                aimail::core::contract::inbound_path()
            ),
        ],
    );
    assert_eq!(rc3, 0);
    let (_, _, b3) = rx3.recv_timeout(std::time::Duration::from_secs(3)).unwrap();
    assert!(b3.contains(r#""id":"dX""#), "{b3}");
    assert!(b3.contains(r#""webhook_url":"#), "{b3}");

    // ④ the response carries error => the failure wording (with the detail) + rc=1
    let (port4, _rx4) = stub(r#"{"error":"domain exists","detail":"already registered"}"#);
    let tmp4 = fixture(&format!("http://127.0.0.1:{}", port4));
    let (rc4, out4) = run(
        &tmp4.path().join("ahome"),
        &["domain", "-s", "s1", "-a", "dup.test"],
    );
    assert_eq!(rc4, 1, "{out4}");
    assert!(
        out4.contains("create failed: domain exists already registered"),
        "{out4}"
    );

    // ⑤ no system config => existence is reported first (before the domain validation)
    let (port5, _rx5) = stub(r#"{}"#);
    let tmp5 = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(tmp5.path().join("ahome/uhome")).unwrap();
    let (rc5, out5) = run(
        &tmp5.path().join("ahome"),
        &["domain", "-s", "nope", "-a", "zz"],
    );
    assert_eq!(rc5, 1, "{out5}");
    assert!(out5.contains("no system config:"), "{out5}");
    let _ = port5;
}
