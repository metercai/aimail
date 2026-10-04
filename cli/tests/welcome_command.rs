//! P3c 收官验收：`aimail welcome` **API 模式**端到端（stub 网关 + 真轮询日志）。
//!
//! 判据：4 行播报 · `api_send` 成功行（email_id/message_id 回显）· 轮询到 `dir=outbound` 的回复
//! ⇒ "Bidirectional … verified" · 第 3 段因**快照无三标签草案**而明确失败 **rc=2**（不假装成功）·
//! `-w` 短路 rc=0 · 缺 admin_key ⇒ rc=1。SMTP 路径（端口 25）留 CLI L2 门禁。

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
        .env_remove("AGENT_HOME")
        .env_remove("AIMAIL_MANAGER_ADDRESS")
        .env_remove("MANAGER")
        .output()
        .expect("run aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
    )
}

/// stub 网关：`POST /api/v1/system/welcome` → `{"email_id":"E1","message_id":"M1"}`。
fn stub() -> (u16, std::sync::mpsc::Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut sock = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            sock.set_read_timeout(Some(std::time::Duration::from_millis(500)))
                .unwrap();
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
            let _ = tx.send(format!(
                "{} {}",
                it.next().unwrap_or(""),
                it.next().unwrap_or("")
            ));
            let payload = r#"{"email_id":"E1","message_id":"M1"}"#;
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                payload.len(),
                payload
            );
            let _ = sock.write_all(resp.as_bytes());
            let _ = sock.flush();
        }
    });
    (port, rx)
}

fn fixture(gw: &str, admin_key: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    let leaf = home.join("systems/s1/default_example.test");
    std::fs::create_dir_all(&leaf).unwrap();
    std::fs::create_dir_all(home.join("uhome")).unwrap();
    std::fs::write(
        home.join("systems/s1/aimail_gateway.json"),
        format!(
            r#"{{"gateway_url":"{}","admin_key":"{}","system_id":"s1","system_name":"","domain":"example.test","manager_address":"m@example.test","default_agent_name":"default","webhook_host":""}}"#,
            gw, admin_key
        ),
    )
    .unwrap();
    // 绑定文件（三层收口靠它归属 sid —— 缺了就会落到 _unassigned，轮询到错的日志）
    std::fs::write(
        leaf.join(aimail::core::contract::binding_file()),
        r#"{"email":"default@example.test","api_key":"k"}"#,
    )
    .unwrap();
    // 日志先有一行（基线），outbound 由另一线程在轮询期间追加
    std::fs::write(leaf.join("agentmail.log"), "{\"dir\":\"noop\"}\n").unwrap();
    tmp
}

#[test]
fn welcome_api_mode_end_to_end() {
    let (port, rx) = stub();
    let gw = format!("http://127.0.0.1:{}", port);
    let tmp = fixture(&gw, "ADMINKEY");
    let home = tmp.path().join("ahome");
    let log = home.join("systems/s1/default_example.test/agentmail.log");

    // 轮询期间追加一条 outbound（poll 每 5s 扫一次 ⇒ 1s 后追加即可被看到）
    let log2 = log.clone();
    thread::spawn(move || {
        thread::sleep(std::time::Duration::from_secs(1));
        let mut f = std::fs::OpenOptions::new()
            .append(true)
            .open(&log2)
            .unwrap();
        let _ =
            f.write_all(b"{\"dir\":\"outbound\",\"email_id\":\"R9\",\"to\":\"m@example.test\"}\n");
        let _ = f.flush();
    });

    let (rc, out) = run(&home, &["welcome", "-s", "s1"]);
    assert_eq!(
        rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap(),
        "POST /api/v1/system/welcome"
    );
    assert!(out.contains("  Gateway:     http://127.0.0.1:"), "{out}");
    assert!(
        out.contains("  Mode:        API (system welcome, from the gateway system sender)"),
        "{out}"
    );
    assert!(out.contains("  To:          default@example.test"), "{out}");
    assert!(
        out.contains("  Cc:          (server-resolved from agent manager_address)"),
        "{out}"
    );
    assert!(
        out.contains("  ✓ Welcome email sent via API (email_id=E1, message_id=M1)"),
        "{out}"
    );
    assert!(out.contains("Polling reply from log: "), "{out}");
    assert!(
        out.contains("  ✓ Agent replied (outbound logged, email_id=R9)"),
        "{out}"
    );
    assert!(
        out.contains("  ✓ Bidirectional send/receive verified (reply email_id=R9)"),
        "{out}"
    );
    // 第 3 段：快照无三标签草案 ⇒ 明确失败 rc=2（不假装成功）
    assert!(out.contains("  ✗ 未取得 persona/signature"), "{out}");
    assert_eq!(rc, 2, "{out}");

    // `-w` 短路：只发不等 ⇒ rc=0
    let tmp2 = fixture(&gw, "ADMINKEY");
    let (rc2, out2) = run(&tmp2.path().join("ahome"), &["welcome", "-s", "s1", "-w"]);
    assert_eq!(rc2, 0, "{out2}");
    assert!(out2.contains("  ✓ Welcome email sent via API "), "{out2}");
    assert!(!out2.contains("Polling reply"), "不应轮询: {out2}");

    // 缺 admin_key ⇒ rc=1（API 模式需系统 admin key）
    let tmp3 = fixture(&gw, "");
    let (rc3, out3) = run(&tmp3.path().join("ahome"), &["welcome", "-s", "s1"]);
    assert_eq!(rc3, 1, "{out3}");
    assert!(
        out3.contains("✗ aimail_gateway.json 无 admin_key——API 模式需系统 admin key"),
        "{out3}"
    );
}
