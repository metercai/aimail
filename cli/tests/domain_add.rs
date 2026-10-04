//! P4 切片1 验收：`aimail domain --add`（域名创建面）—— stub 网关 + 请求形状断言。
//!
//! 判据（逐条对齐 `cli/aimail:cmd_domain` 的创建分支）：
//! ①非法域名（空 / 含 `@` / 不含 `.`）⇒ **不发请求**，文案打**原文**；
//! ②合法 ⇒ `POST /api/v1/admin/systems/{sid}/domains`，body = `{id, domain(小写归一)}`，
//!   `-w` 有值才带 `webhook_url`；`id` 省略 ⇒ `d{epoch}`；
//! ③响应带 `error` ⇒ `创建失败: {error} {detail}`（`.strip()`）且 rc=1；
//! ④成功 ⇒ `domain created: {domain} (system {sid})` rc=0；
//! ⑤校验顺序：先存在性/凭据，再域名合法性（与 Python 同）。

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

/// stub 网关：固定回一份 JSON（由调用方给），并把 (METHOD, PATH, BODY) 发回通道。
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
    // ①+⑤ 非法域名：不发请求（先校验）
    let (port, rx) = stub(r#"{"ok":true}"#);
    let gw = format!("http://127.0.0.1:{}", port);
    let tmp = fixture(&gw);
    let home = tmp.path().join("ahome");
    for bad in ["", "no-dot", "has@at.test"] {
        let (rc, out) = run(&home, &["domain", "-s", "s1", "-a", bad]);
        assert_eq!(rc, 1, "bad={bad} out={out}");
        assert!(
            out.contains(&format!("非法域名: '{}'", bad)),
            "bad={bad} out={out}"
        );
    }
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(300))
            .is_err(),
        "非法域名不得发出任何请求"
    );

    // ② 合法创建：POST 形状 + 小写归一 + 省略 id ⇒ d{epoch}
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
    assert!(b.contains(r#""id":"d"#), "省略 id 应生成 d{{epoch}}: {b}");
    assert!(!b.contains("webhook_url"), "未给 -w 不得带该字段: {b}");

    // ③ -w 有值才带；显式 --id 生效
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

    // ④ 响应带 error ⇒ 失败文案（含 detail）+ rc=1
    let (port4, _rx4) = stub(r#"{"error":"domain exists","detail":"already registered"}"#);
    let tmp4 = fixture(&format!("http://127.0.0.1:{}", port4));
    let (rc4, out4) = run(
        &tmp4.path().join("ahome"),
        &["domain", "-s", "s1", "-a", "dup.test"],
    );
    assert_eq!(rc4, 1, "{out4}");
    assert!(
        out4.contains("创建失败: domain exists already registered"),
        "{out4}"
    );

    // ⑤ 无系统配置 ⇒ 先报存在性（先于域名校验）
    let (port5, _rx5) = stub(r#"{}"#);
    let tmp5 = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(tmp5.path().join("ahome/uhome")).unwrap();
    let (rc5, out5) = run(
        &tmp5.path().join("ahome"),
        &["domain", "-s", "nope", "-a", "zz"],
    );
    assert_eq!(rc5, 1, "{out5}");
    assert!(out5.contains("无系统配置:"), "{out5}");
    let _ = port5;
}
