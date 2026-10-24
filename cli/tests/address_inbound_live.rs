//! P4 slice 5 acceptance: `address --inbound-live/--inbound-down` (hidden surface).
//!
//! Criteria: ① the two flags are mutually exclusive, verbatim; ② missing locator, verbatim; ③ `-e` miss; ④ `-a` miss and list the local agents;
//! ⑤ (omitted, see the in-test comments); ⑥ live + pull binding => `skipped` synthesis + **rc=0** (idempotent);
//! ⑦ down with no bridge declared => `no_bridge` **silent rc=0**; ⑧ down with a bridge declared + a row for this system in the table =>
//!   row withdrawal + `; backlog N pending` (stub gateway) + anchor `route withdrawn` + rc=0.
//!
//! Note: the fixture must carry a **binding file** (otherwise `list_agents` cannot know this leaf's email => `-e` cannot find it,
//! which is exactly the true cause of the previous round's ⑦ red verdict: the fixture lacked the binding file, hitting the "no binding" guard, not a write failure).

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;

use aimail::core::{bridge_wire as bw, contract};

fn bin() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("target/debug/aimail");
    p
}

fn run(home: &Path, argv: &[&str]) -> (i32, String, String) {
    let out = Command::new(bin())
        .args(argv)
        .env("AIMAIL_HOME", home)
        .env("HOME", home)
        .env_remove("AIMAIL_PYTHON")
        .stdin(Stdio::null())
        .output()
        .expect("spawn");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

struct Stub {
    port: u16,
    rx: mpsc::Receiver<(String, String, String)>,
}

fn spawn_stub() -> Stub {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for s in l.incoming() {
            let mut s = match s {
                Ok(s) => s,
                Err(_) => continue,
            };
            let mut buf = Vec::new();
            let mut tmp = [0u8; 4096];
            let mut clen = 0usize;
            loop {
                let n = match s.read(&mut tmp) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => n,
                };
                buf.extend_from_slice(&tmp[..n]);
                if clen == 0 {
                    if let Some(h) = String::from_utf8_lossy(&buf).find("\r\n\r\n") {
                        let head = String::from_utf8_lossy(&buf[..h]).to_lowercase();
                        clen = head
                            .split("content-length:")
                            .nth(1)
                            .and_then(|v| v.split("\r\n").next())
                            .and_then(|v| v.trim().parse().ok())
                            .unwrap_or(0);
                        if buf.len() >= h + 4 + clen {
                            break;
                        }
                    }
                } else {
                    let h = String::from_utf8_lossy(&buf).find("\r\n\r\n").unwrap_or(0);
                    if buf.len() >= h + 4 + clen {
                        break;
                    }
                }
            }
            let text = String::from_utf8_lossy(&buf).to_string();
            let mut parts = text.splitn(2, ' ');
            let method = parts.next().unwrap_or("").to_string();
            let path = parts
                .next()
                .unwrap_or("")
                .split(' ')
                .next()
                .unwrap_or("")
                .to_string();
            let body = text.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
            let _ = tx.send((method, path.clone(), body));
            let payload = if path.contains("/pending") {
                r#"{"status":200,"batches":[{"deliveries":[1,2]}]}"#
            } else {
                r#"{"status":200}"#
            };
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                payload.len(),
                payload
            );
            let _ = s.write_all(resp.as_bytes());
        }
    });
    Stub { port, rx }
}

/// Fixture: `with_binding` decides whether to write the binding file; `webhook` decides pull (empty) or push.
fn fixture(
    gw_port: u16,
    with_binding: bool,
    webhook: &str,
    declared_bridge: Option<u16>,
) -> (tempfile::TempDir, PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    let sys = home.join("systems/s1");
    std::fs::create_dir_all(&sys).unwrap();
    let mut cfg = format!(
        r#"{{"gateway_url":"http://127.0.0.1:{gw_port}","admin_key":"AK","system_id":"s1","domain":"example.test","manager_address":"m@example.test""#
    );
    if let Some(p) = declared_bridge {
        cfg.push_str(&format!(r#","bridge_admin_port":{p}"#));
    }
    cfg.push('}');
    std::fs::write(sys.join("aimail_gateway.json"), cfg).unwrap();
    let leaf = sys.join(bw::addr_clean("agent@example.test"));
    std::fs::create_dir_all(&leaf).unwrap();
    if with_binding {
        std::fs::write(
            leaf.join(contract::binding_file()),
            format!(r#"{{"email":"agent@example.test","api_key":"K","webhook_url":"{webhook}"}}"#),
        )
        .unwrap();
    }
    (tmp, home)
}

#[test]
fn inbound_faces_validate_and_finish_by_state() {
    let stub = spawn_stub();
    // ①② mutual exclusion / missing locator: the fixture carries a binding (avoid accidentally hitting the guard)
    let (_t0, h0) = fixture(stub.port, true, "", Some(0));
    let (rc, out, _) = run(
        &h0,
        &["address", "-s", "s1", "--inbound-live", "--inbound-down"],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("mutually exclusive"), "{out}");
    let (rc, out, _) = run(&h0, &["address", "-s", "s1", "--inbound-live"]);
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("needs -a <agent> (or -e <email>) to locate the binding"),
        "{out}"
    );

    // ③④ locator miss
    let (rc, out, _) = run(
        &h0,
        &["address", "-s", "s1", "--inbound-live", "-e", "nope@x.test"],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("no local address for nope@x.test"), "{out}");
    let (rc, out, _) = run(
        &h0,
        &["address", "-s", "s1", "--inbound-live", "-a", "nope"],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("no local address for -a nope"), "{out}");

    // ⑤ (omitted) "located but no binding file => guard" can only trigger when the **platform is enumerated but there is no local binding**:
    // under a purely local fixture list_agents is empty => it lands on "no local address" just like Python (not the guard).
    // that path needs a platform home fixture (hermes profiles, etc.); leave it to the CLI L2 gate.

    // ⑥ live + pull binding + no bridge declared => skipped synthesis, rc=0
    let (_t2, h2) = fixture(stub.port, true, "", None);
    let (rc, out, err) = run(
        &h2,
        &["address", "-s", "s1", "--inbound-live", "-a", "agent"],
    );
    assert_eq!(rc, 0, "⑥ idempotent should be rc=0\nout={out}\nerr={err}");
    assert!(
        out.contains("nothing to do"),
        "⑥ should be the pull skipped synthesis: {out}"
    );

    // ⑦ down with no bridge declared => no_bridge silent rc=0
    let (rc, out, err) = run(
        &h2,
        &["address", "-s", "s1", "--inbound-down", "-a", "agent"],
    );
    assert_eq!(rc, 0, "⑦ no_bridge should be silent rc=0\nout={out}\nerr={err}");

    // ⑧ down with a bridge declared + a row for this system in the table => row withdrawal + backlog + anchor already absent
    let (_t3, h3) = fixture(stub.port, true, "", Some(stub.port));
    let bdir = h3.join("bridge");
    std::fs::create_dir_all(&bdir).unwrap();
    std::fs::write(
        h3.join("bridge/aimail_routes.toml"),
        format!(
            "agent@example.test = \"http://127.0.0.1:9101{}\"\n",
            contract::inbound_path()
        ),
    )
    .unwrap();
    let (rc, out, err) = run(
        &h3,
        &["address", "-s", "s1", "--inbound-down", "-a", "agent"],
    );
    assert_eq!(rc, 0, "⑧ should be rc=0\nout={out}\nerr={err}");
    assert!(
        out.contains("route(s) withdrawn for system s1"),
        "⑧ should announce the withdrawn row count: {out}"
    );
    assert!(
        out.contains("backlog 2 pending"),
        "⑧ the backlog should come from the stub: {out}"
    );
    // the anchor row **is** in the table => the real result is `route withdrawn` (already absent is only for "the anchor is not in the table")
    assert!(
        out.contains("route withdrawn: agent@example.test"),
        "⑧ anchor result: {out}"
    );
    let mut saw_delete = false;
    while let Ok((m, p, _)) = stub.rx.try_recv() {
        if m == "DELETE" && p.contains("agent%40example.test") {
            saw_delete = true;
        }
    }
    assert!(saw_delete, "⑧ should really have sent the DELETE row withdrawal");
}
