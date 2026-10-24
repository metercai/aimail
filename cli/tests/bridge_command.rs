//! Offline acceptance of the `aimail bridge` command surface (`cmd::bridge`).
//!
//! Criteria: no args => status view (rc=0, including the honest reports "config does not exist / route table empty") · `-s` => per-system re-refresh
//! (target taken from the binding webhook_url, `host` passed as the **full URL**, the request lands on `/api/v1/routes`, the summary line verbatim) ·
//! `--restart`/`--upgrade` (deployment surface, P2 slice 3) => **loudly unported** (rc=1), not a silent success.
//!
//! Note: the re-refresh, per the Python stance, **hits the fixed admin address `127.0.0.1:38081` directly** => the stub binds exactly this port
//! (which is precisely the "fixed address" behavior under verification); if that port is occupied this test will fail — as expected (the real machine behaves the same).

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::Command;
use std::sync::mpsc::{channel, Receiver};
use std::thread;

fn rust_bin() -> String {
    format!("{}/target/debug/aimail", env!("CARGO_MANIFEST_DIR"))
}

fn run(home: &std::path::Path, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(rust_bin())
        .args(args)
        .env("AIMAIL_HOME", home)
        .env("AIMAIL_PROG_DIR", home.join("prog"))
        .env("HOME", home.join("uhome"))
        .output()
        .expect("run aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn status_refresh_and_deploy_flags_are_honest() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("systems/s1/agent1")).unwrap();
    std::fs::create_dir_all(home.join("uhome")).unwrap();

    // 1) no args => status view: process not running + config does not exist + route table empty, all honest reports, rc=0
    let (rc, out, _) = run(&home, &["bridge"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("  bridge status:"), "{out}");
    assert!(out.contains("process: not running"), "{out}");
    assert!(out.contains("config: aimail_bridge.toml does not exist"), "{out}");
    assert!(out.contains("route table is empty (aimail_routes.toml)"), "{out}");

    // 2) both deployment-surface flags are wired: this fixture has no bridge config / no bridge binary => both must **fail loudly** (rc=1),
    //    no silent 0 (`--upgrade` missing config = "bridge config does not exist"; `--restart` startup failure)
    let (rc, out, _) = run(&home, &["bridge", "--upgrade"]);
    assert_eq!(rc, 1, "--upgrade should be rc=1: {out}");
    assert!(out.contains("bridge configuration does not exist"), "--upgrade output: {out}");
    let (rc, out, _) = run(&home, &["bridge", "--restart"]);
    assert!(
        rc == 1,
        "--restart without a bridge binary must be rc=1 (no silent success): {out}"
    );
    assert!(
        out.contains("Bridge startup failed") || out.contains("Old bridge"),
        "--restart output: {out}"
    );

    // 3) re-refresh: the stub admin binds the fixed address 127.0.0.1:38081 (the same behavior as Python)
    let listener = TcpListener::bind("127.0.0.1:38081").unwrap();
    let (tx, rx): (_, Receiver<(String, String, String)>) = channel();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut sock: TcpStream = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            sock.set_read_timeout(Some(std::time::Duration::from_millis(500)))
                .unwrap();
            // read until the Content-Length is satisfied (ureq may write header and body in two pieces — an early exit would read an empty body)
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
                        let _ = i;
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
            let (head, body) = match text.split_once("\r\n\r\n") {
                Some((h, b)) => (h.to_string(), b.to_string()),
                None => (text.clone(), String::new()),
            };
            let first = head.lines().next().unwrap_or("").to_string();
            let mut p = first.split_whitespace();
            let method = p.next().unwrap_or("").to_string();
            let path = p.next().unwrap_or("").to_string();
            let _ = tx.send((method, path, body));
            let payload = b"ok";
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                payload.len()
            );
            let _ = sock.write_all(resp.as_bytes());
            let _ = sock.write_all(payload);
            let _ = sock.flush();
        }
    });

    // fixture: system config (for the existence check) + one registered binding (webhook_url is the only source of trust)
    // the platform is derived by "reverse lookup from the config's system_home" (same order as Python) => the fixture gives a home with the hermes marker
    // platform detect recognizes the marker features (C-3: hermes = hermes-agent; profiles is a runtime product and does not enter the features)
    std::fs::create_dir_all(home.join("phome/hermes-agent")).unwrap();
    std::fs::write(
        home.join("systems/s1/aimail_gateway.json"),
        format!(
            r#"{{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1","system_name":"","save_raw_snapshots":false,"domain":"example.test","system_home":"{}"}}"#,
            home.join("phome").display()
        ),
    )
    .unwrap();
    std::fs::write(
        home.join("systems/s1/agent1")
            .join(aimail::core::contract::binding_file()),
        format!(
            r#"{{"email":"a@example.test","webhook_url":"http://127.0.0.1:9101{}"}}"#,
            aimail::core::contract::inbound_path()
        ),
    )
    .unwrap();

    let (rc, out, _) = run(&home, &["bridge", "-s", "s1"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("bridge not running"), "{out}");
    assert!(
        out.contains(&format!(
            "↻ a@example.test: (none) → http://127.0.0.1:9101{}",
            aimail::core::contract::inbound_path()
        )),
        "{out}"
    );
    assert!(out.contains("Refresh complete: 1 routes registered/confirmed"), "{out}");

    let (method, path, body) = rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("the stub should receive one route request");
    assert_eq!(method, "POST");
    assert_eq!(path, "/api/v1/routes");
    assert!(
        body.contains(&format!(
            "\"host\":\"http://127.0.0.1:9101{}\"",
            aimail::core::contract::inbound_path()
        )),
        "host must be the full URL: {body}"
    );
    // one binding should produce exactly one request (what the stub records is the evidence)
    assert!(rx
        .recv_timeout(std::time::Duration::from_millis(300))
        .is_err());
    let _: HashMap<String, String> = HashMap::new();
}
