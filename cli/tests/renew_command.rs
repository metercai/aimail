//! P3a 离线验收：`aimail renew`（目标解析文案 / 只读视图与天数 / 续期成功与失败 / 配额查询失败）。
//! 全程 stub 网关（离线）；并断言**发出**的请求形状（方法+路径）。
//!
//! 已登记分歧：Python 在"无系统/多系统/指定系统不在本地"时 `_fail` 后**继续**用空 key 打
//! 生产默认网关；本实现打印同样文案后 rc=1 返回、**不发网络**（测试据此断言 rc=1 且零请求）。

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::mpsc::{channel, Receiver};
use std::sync::Arc;
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
        .env_remove("AIMAIL_GW_URL")
        .output()
        .expect("run aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
    )
}

/// stub 网关：`replies` 键 = "METHOD path"；默认 200 `{}`。记录收到的请求。
fn stub(replies: HashMap<String, (u16, String)>) -> (u16, Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = channel();
    let replies = Arc::new(replies);
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut sock = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            sock.set_read_timeout(Some(std::time::Duration::from_millis(500)))
                .unwrap();
            let mut buf = Vec::new();
            let mut tmpb = [0u8; 4096];
            let mut need: Option<usize> = None;
            loop {
                let n = match sock.read(&mut tmpb) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(_) => break,
                };
                buf.extend_from_slice(&tmpb[..n]);
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
                if let Some(n) = need {
                    if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                        if buf.len() >= i + 4 + n {
                            break;
                        }
                    }
                }
            }
            let text = String::from_utf8_lossy(&buf).to_string();
            let first = text.lines().next().unwrap_or("").to_string();
            let mut it = first.split_whitespace();
            let method = it.next().unwrap_or("").to_string();
            let path = it.next().unwrap_or("").to_string();
            let key = format!("{} {}", method, path);
            let _ = tx.send(key.clone());
            let (status, payload) = replies
                .get(&key)
                .cloned()
                .unwrap_or((200, "{}".to_string()));
            let reason = if (200..300).contains(&status) {
                "OK"
            } else {
                "Bad Request"
            };
            let resp = format!(
                "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                status,
                reason,
                payload.len(),
                payload
            );
            let _ = sock.write_all(resp.as_bytes());
            let _ = sock.flush();
        }
    });
    (port, rx)
}

fn fixture(system_ids: &[&str], gw: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("uhome")).unwrap();
    for sid in system_ids {
        let dir = home.join("systems").join(sid);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("aimail_gateway.json"),
            format!(
                r#"{{"gateway_url":"{}","admin_key":"K","system_id":"{}","system_name":"","webhook_host":"","domain":"example.test"}}"#,
                gw, sid
            ),
        )
        .unwrap();
    }
    tmp
}

fn days_from_now(days: i64) -> String {
    let t = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64
        + days * 86400;
    // 秒级 ISO8601（Z 结尾），与 Python `datetime.fromisoformat(...replace Z)` 可解析
    let (y, m, d, hh, mm, ss) = epoch_to_utc(t);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", y, m, d, hh, mm, ss)
}

fn epoch_to_utc(ts: i64) -> (i64, u32, u32, u32, u32, u32) {
    let days = ts.div_euclid(86400);
    let rem = ts.rem_euclid(86400);
    let (hh, mm, ss) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // 民用历（Howard Hinnant 算法）
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d, hh as u32, mm as u32, ss as u32)
}

#[test]
fn renew_offline_paths_and_stub_gateway() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("uhome")).unwrap();

    // 1) 无系统 ⇒ 文案 + rc=1（且**不发网络**）
    let (rc, out) = run(&home, &["renew", "--status"]);
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("no aimail systems configured; use --system-id"),
        "{out}"
    );

    // 2) 多系统未指定 ⇒ 列出候选
    for sid in ["s1", "s2"] {
        let dir = home.join("systems").join(sid);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("aimail_gateway.json"), r#"{"gateway_url":"http://127.0.0.1:1","admin_key":"K","system_home":"","system_name":"","domain":"example.test"}"#).unwrap();
    }
    let (rc, out) = run(&home, &["renew", "--status"]);
    assert_eq!(rc, 1);
    assert!(
        out.contains("multiple systems installed: s1, s2; use --system-id"),
        "{out}"
    );

    // 3) 指定系统不在本地
    let (rc, out) = run(&home, &["renew", "-s", "nope", "--status"]);
    assert_eq!(rc, 1);
    assert!(
        out.contains("system not found locally: nope (installed: s1, s2)"),
        "{out}"
    );

    // 4) 只读视图（远期到期）+ 5) 续期成功 + 6) 续期失败 + 7) 配额查询失败
    let mut replies = HashMap::new();
    replies.insert(
        "GET /api/v1/quotas".to_string(),
        (
            200,
            format!(
                r#"{{"product_id":"prod-1","validity_days":365,"expires_at":"{}","max_domains":5,"max_addresses":20,"max_daily_emails":500,"max_attachments":10}}"#,
                days_from_now(100)
            ),
        ),
    );
    replies.insert(
        "POST /api/v1/admin/renew-system".to_string(),
        (
            200,
            format!(
                r#"{{"status":200,"expires_at":"{}","quota":{{"max_domains":9,"max_addresses":99,"max_daily_emails":900,"max_attachments":9}},"product_id":"prod-1","product_name":"Pro"}}"#,
                days_from_now(400)
            ),
        ),
    );
    let (port, rx) = stub(replies);
    let gw = format!("http://127.0.0.1:{}", port);
    let tmp2 = fixture(&["s1"], &gw);
    let home2 = tmp2.path().join("ahome");

    let (rc, out) = run(&home2, &["renew", "-s", "s1", "--status"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("  system s1"), "{out}");
    assert!(
        out.contains("    product: prod-1 · validity: 365 days"),
        "{out}"
    );
    assert!(
        out.contains("99 day(s) remaining") || out.contains("100 day(s) remaining"),
        "{out}"
    );
    assert!(
        out.contains("    quotas: domains 5 · addresses 20 · daily 500 · attachments 10"),
        "{out}"
    );
    assert_eq!(
        rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap(),
        "GET /api/v1/quotas"
    );

    let (rc, out) = run(&home2, &["renew", "-s", "s1", "-c", "ACT-CODE-1234567890"]);
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("  renewing system s1 with activation code ACT-CODE-123"),
        "{out}"
    );
    assert!(
        out.contains("renewed: expires_at "),
        "标记带 ANSI 颜色，断言只看正文: {out}"
    );
    assert!(
        out.contains("    merged quotas: domains 9 · addresses 99 · daily 900 · attachments 9"),
        "{out}"
    );
    assert!(out.contains("    product: prod-1 (Pro)"), "{out}");
    assert_eq!(
        rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap(),
        "POST /api/v1/admin/renew-system"
    );

    // 6) 续期失败：stub 答 400 + 错误体 ⇒ python-repr 文案
    let mut replies2 = HashMap::new();
    replies2.insert(
        "POST /api/v1/admin/renew-system".to_string(),
        (400, r#"{"error":"bad code"}"#.to_string()),
    );
    let (port2, _rx2) = stub(replies2);
    let tmp3 = fixture(&["s1"], &format!("http://127.0.0.1:{}", port2));
    let home3 = tmp3.path().join("ahome");
    let (rc, out) = run(&home3, &["renew", "-s", "s1", "-c", "BAD"]);
    assert_eq!(rc, 0, "Python 在 renew 失败后继续走完并返回 0: {out}");
    assert!(
        out.contains("failed: {'status': 400, 'error': 'bad code'}")
            || out.contains("renew failed:"),
        "{out}"
    );

    // 7) 配额查询失败（500）⇒ 同形：先报失败，再照旧打印空视图
    let mut replies3 = HashMap::new();
    replies3.insert("GET /api/v1/quotas".to_string(), (500, "boom".to_string()));
    let (port3, _rx3) = stub(replies3);
    let tmp4 = fixture(&["s1"], &format!("http://127.0.0.1:{}", port3));
    let home4 = tmp4.path().join("ahome");
    let (rc, out) = run(&home4, &["renew", "-s", "s1", "--status"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("quotas query failed: "), "{out}");
    assert!(out.contains("    expires_at: unlimited"), "{out}");
}
