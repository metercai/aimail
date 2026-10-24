//! **Offline acceptance** of `core::bridge_wire` (= the rust re-implementation of `cli/bridge_wire.py`).
//!
//! Criteria: the four-state decision (ok/skipped/no_bridge/failed) and the three-step order · the rejection reasons of `validate_target` verbatim ·
//! the four forms of declaration reading (none / system config with port / bridge config present / broken TOML => fail closed) · the request shape
//! (POST `/api/v1/routes` body `{email,host(full URL),port:80}`, DELETE `/api/v1/routes/<email>`) ·
//! the `down` asymmetry (an empty binding endpoint **must** be withdrawn too) · the rollback wording verbatim. The whole run uses the stub admin API, no network.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::mpsc::{channel, Receiver};
use std::thread;

use aimail::core::bridge_wire::*;

use aimail::core::contract;
use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq, Eq)]
struct Req {
    method: String,
    path: String,
    body: String,
}

/// Start a stub admin API: reply per `replies` (path -> (status, reason/body)), and record the requests received.
/// `/health` answers 200 by default + `{"status":"ok","uptime_secs":1,"version":"9.9.9"}`.
fn stub_admin(replies: HashMap<String, (u16, String)>) -> (u16, Receiver<Req>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = channel();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut sock = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            let req = read_request(&mut sock);
            let (method, path, body) = match req {
                Some(r) => r,
                None => continue,
            };
            if !path.starts_with("/health") {
                let _ = tx.send(Req {
                    method: method.clone(),
                    path: path.clone(),
                    body: body.clone(),
                });
            }
            let key = format!("{} {}", method, path.split('?').next().unwrap_or(""));
            let (status, payload) = if path.starts_with("/health") {
                (
                    200,
                    json!({"status":"ok","uptime_secs":1,"version":"9.9.9"}).to_string(),
                )
            } else {
                replies
                    .get(&key)
                    .cloned()
                    .unwrap_or((200, "ok".to_string()))
            };
            let reason = if (200..300).contains(&status) {
                "OK"
            } else {
                "Server Error"
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

fn read_request(sock: &mut TcpStream) -> Option<(String, String, String)> {
    sock.set_read_timeout(Some(std::time::Duration::from_millis(500)))
        .ok()?;
    let mut buf = Vec::new();
    let mut tmp = [0u8; 8192];
    let mut header_end = None;
    let mut need = usize::MAX;
    loop {
        let n = sock.read(&mut tmp).ok()?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
        if header_end.is_none() {
            if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                header_end = Some(i + 4);
                let head = String::from_utf8_lossy(&buf[..i]).to_string();
                for line in head.lines() {
                    if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                        need = v.trim().parse().unwrap_or(0);
                    }
                }
            }
        }
        if let Some(he) = header_end {
            if buf.len() >= he + if need == usize::MAX { 0 } else { need } {
                break;
            }
        }
    }
    let text = String::from_utf8_lossy(&buf).to_string();
    let (head, body) = match text.split_once("\r\n\r\n") {
        Some((h, b)) => (h.to_string(), b.to_string()),
        None => (text.clone(), String::new()),
    };
    let first = head.lines().next()?.to_string();
    let mut parts = first.split_whitespace();
    let method = parts.next()?.to_string();
    let path = parts.next()?.to_string();
    Some((method, path, body))
}

fn write_bridge_cfg(dir: &std::path::Path, content: &str) -> PathBuf {
    let p = dir.join("aimail_bridge.toml");
    std::fs::write(&p, content).unwrap();
    p
}

#[test]
fn declaration_forms() {
    let tmp = tempfile::tempdir().unwrap();
    let missing = tmp.path().join("aimail_bridge.toml");

    // 1) no bridge config, no port => not declared + the reason verbatim
    let d = load_declaration(&json!({}), &missing);
    assert!(!d.declared && d.usable);
    assert_eq!(
        d.reason,
        format!(
            "no bridge config at {} and no bridge_admin_port declared",
            missing.display()
        )
    );
    assert_eq!(d.admin_port, DEFAULT_ADMIN_PORT);

    // 2) the system config carries bridge_admin_port => declared, the port taken from the system config
    let d = load_declaration(&json!({"bridge_admin_port": 39999}), &missing);
    assert!(d.declared && d.usable);
    assert_eq!(d.admin_port, 39999);
    assert_eq!(d.source, "system config bridge_admin_port");
    assert_eq!(d.gateway_port, 39999);

    // 3) the bridge config is in place: mode/bind read out; the port taken from bind (when the system config gives no port)
    let cfg = write_bridge_cfg(tmp.path(), "mode = \"pull\"\nbind = \"0.0.0.0:38080\"\n");
    let d = load_declaration(&json!({}), &cfg);
    assert!(d.declared && d.usable);
    assert_eq!(d.mode, "pull");
    assert_eq!(d.bind, "0.0.0.0:38080");
    assert_eq!(d.admin_port, 38080);
    assert_eq!(d.source, cfg.display().to_string());

    // 4) a broken TOML => declared but unavailable (fail closed), and **not** "no bridge"
    let bad = write_bridge_cfg(tmp.path(), "mode = = broken\n");
    let d = load_declaration(&json!({}), &bad);
    assert!(d.declared && !d.usable);
    assert!(d.reason.contains("unreadable/invalid TOML"), "{}", d.reason);
    assert!(d.reason.contains("nothing changed"), "{}", d.reason);
}

#[test]
fn validate_target_messages_are_verbatim() {
    let (t, why) = validate_target(&json!(""));
    assert!(t.is_empty());
    assert_eq!(
        why,
        "the binding carries no webhook_url (pull mode: the gateway fetches the mail, \
         there is no local endpoint to route to)"
    );

    // a bare host:port must be rejected (adding a prefix would produce a non-deliverable form)
    let (t, why) = validate_target(&json!("127.0.0.1:9101"));
    assert!(t.is_empty());
    assert_eq!(
        why,
        "not an absolute http(s) URL: '127.0.0.1:9101' has no http(s):// scheme \
         (a bare host:port cannot be delivered to)"
    );

    // an absolute URL passes through as-is (path included)
    let (t, why) = validate_target(&json!(format!(
        "http://127.0.0.1:9101{}",
        aimail::core::contract::inbound_path()
    )));
    assert_eq!(
        t,
        format!(
            "http://127.0.0.1:9101{}",
            aimail::core::contract::inbound_path()
        )
    );
    assert!(why.is_empty());

    // an out-of-range port: Python's `u.port` raises ValueError directly => it goes through the "not a usable URL (...)" branch
    let (_, why) = validate_target(&json!("http://host:99999/x"));
    assert_eq!(
        why,
        "'http://host:99999/x' is not a usable URL (Port out of range 0-65535)"
    );
    // port 0: `u.port` does not raise, but `_valid_port` rejects => "carries an invalid port"
    let (_, why) = validate_target(&json!("http://host:0/x"));
    assert_eq!(why, "'http://host:0/x' carries an invalid port");
}

#[test]
fn sync_route_four_states_and_request_shapes() {
    let tmp = tempfile::tempdir().unwrap();
    let no_cfg = tmp.path().join("aimail_bridge.toml");
    let gw = json!({});
    let url = json!(format!(
        "http://127.0.0.1:9101{}",
        aimail::core::contract::inbound_path()
    ));

    // 1) no bridge declared => no_bridge (a deliberate no-op, no error)
    let out = sync_route(
        ACTION_LIVE,
        "a@example.test",
        &url,
        &gw,
        &no_cfg,
        ADMIN_HOST,
    );
    assert_eq!(out["state"], STATE_NO_BRIDGE);
    assert!(format_line(&out).starts_with("route skipped for a@example.test: no bridge config at"));

    // 2) declared but unreachable => failed + "nothing changed" (fail closed)
    let cfg = write_bridge_cfg(tmp.path(), "mode = \"pull\"\nbind = \"127.0.0.1:38999\"\n");
    let out = sync_route(ACTION_LIVE, "a@example.test", &url, &gw, &cfg, ADMIN_HOST);
    assert_eq!(out["state"], STATE_FAILED);
    let line = format_line(&out);
    assert!(line.contains("route FAILED for a@example.test:"), "{line}");
    assert!(
        line.contains("is not reachable — nothing changed"),
        "{line}"
    );

    // 3) reachable + live + a legal URL => ok, and the request shape = POST /api/v1/routes {email,host(full URL),port:80}
    let (port, rx) = stub_admin(HashMap::new());
    let cfg = write_bridge_cfg(
        tmp.path(),
        &format!("mode = \"pull\"\nbind = \"127.0.0.1:{}\"\n", port),
    );
    let out = sync_route(ACTION_LIVE, "a@example.test", &url, &gw, &cfg, ADMIN_HOST);
    assert_eq!(out["state"], STATE_OK, "{}", out["reason"]);
    let req = rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap();
    assert_eq!(req.method, "POST");
    assert_eq!(req.path, "/api/v1/routes");
    let body: Value = serde_json::from_str(&req.body).unwrap();
    assert_eq!(body["email"], "a@example.test");
    assert_eq!(
        body["host"],
        format!(
            "http://127.0.0.1:9101{}",
            aimail::core::contract::inbound_path()
        )
    ); // the full URL, as-is
    assert_eq!(body["port"], 80); // a placeholder (admin.rs rejects 0)
    assert_eq!(
        format_line(&out),
        format!(
            "route: a@example.test -> http://127.0.0.1:9101{}",
            aimail::core::contract::inbound_path()
        )
    );

    // 4) live + an empty endpoint => skipped (a legal pull binding), **no request sent**
    let out = sync_route(
        ACTION_LIVE,
        "p@example.test",
        &json!(""),
        &gw,
        &cfg,
        ADMIN_HOST,
    );
    assert_eq!(out["state"], STATE_SKIPPED);
    assert!(format_line(&out)
        .starts_with("route skipped for p@example.test: the binding carries no webhook_url"));

    // 5) down + an empty endpoint => **still must be withdrawn** (asymmetric), the DELETE path is escaped
    let out = sync_route(
        ACTION_DOWN,
        "p@example.test",
        &json!(""),
        &gw,
        &cfg,
        ADMIN_HOST,
    );
    assert_eq!(out["state"], STATE_OK, "{}", out["reason"]);
    let req = rx.recv_timeout(std::time::Duration::from_secs(3)).unwrap();
    assert_eq!(req.method, "DELETE");
    assert_eq!(req.path, "/api/v1/routes/p%40example.test");
    assert_eq!(format_line(&out), "route withdrawn: p@example.test");

    // 6) the bridge answers 5xx => failed, the reason carries the status code
    let mut replies = HashMap::new();
    replies.insert(
        "POST /api/v1/routes".to_string(),
        (500u16, "boom".to_string()),
    );
    let (port2, _rx2) = stub_admin(replies);
    let cfg2 = write_bridge_cfg(
        tmp.path(),
        &format!("mode = \"pull\"\nbind = \"127.0.0.1:{}\"\n", port2),
    );
    let out = sync_route(ACTION_LIVE, "b@example.test", &url, &gw, &cfg2, ADMIN_HOST);
    assert_eq!(out["state"], STATE_FAILED);
    assert!(
        out["reason"]
            .as_str()
            .unwrap()
            .starts_with("could not upsert the route: bridge answered HTTP 500"),
        "{}",
        out["reason"]
    );
}

#[test]
fn routes_table_and_scope_helpers() {
    let tmp = tempfile::tempdir().unwrap();
    let p = tmp.path().join("aimail_routes.toml");
    std::fs::write(
        &p,
        format!(
            "# comment\nb@example.test = \"http://127.0.0.1:9101{}\"\n\na@example.test = \"http://127.0.0.1:9099{}\"\n",
            aimail::core::contract::inbound_path(),
            aimail::core::contract::inbound_path()
        ),
    )
    .unwrap();
    let routes = read_routes(&p);
    assert_eq!(routes.len(), 2);
    assert_eq!(
        routes.get("a@example.test").unwrap(),
        &format!(
            "http://127.0.0.1:9099{}",
            aimail::core::contract::inbound_path()
        )
    );

    // `_target_to_route_fields`: a full URL => (url, 80); a bare host:port => split into two fields
    assert_eq!(
        target_to_route_fields("http://127.0.0.1:9101/x"),
        ("http://127.0.0.1:9101/x".to_string(), 80)
    );
    assert_eq!(
        target_to_route_fields("127.0.0.1:9101"),
        ("127.0.0.1".to_string(), 9101)
    );

    // `_system_route_scope`: the shared suffix / the non-shared suffix / a missing domain => None
    let sc = system_route_scope(&json!({"domain": "d.test", "system_name": "sys1"})).unwrap();
    assert!(sc("billing.sys1@d.test"));
    assert!(!sc("billing.sys2@d.test"));
    assert!(!sc("billing@d.test"));
    let sc2 = system_route_scope(&json!({"domain": "d.test"})).unwrap();
    assert!(sc2("billing@d.test"));
    // non-shared (no system_name): the `@{domain}` suffix => **all** addresses in that domain belong to this system
    // (`system_domains.domain_addr UNIQUE` => the domain has a unique owner); the shared form also falls inside this suffix
    assert!(sc2("billing.sys1@d.test"));
    assert!(!sc2("billing@other.test"));
    assert!(system_route_scope(&json!({})).is_none());
}

#[test]
fn reconcile_upserts_withdraws_and_leaves_out_of_scope_rows() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("systems/s1/agent1")).unwrap();
    std::fs::create_dir_all(home.join("systems/s1/agent2")).unwrap();
    std::fs::create_dir_all(home.join("bridge")).unwrap();
    // expected state: agent1 = push (has webhook_url) => upsert; agent2 = pull (empty) => not in the expected state, but belongs to this system
    std::fs::write(
        home.join("systems/s1/agent1")
            .join(contract::binding_file()),
        format!(
            r#"{{"email":"a@example.test","webhook_url":"http://127.0.0.1:9101{}"}}"#,
            aimail::core::contract::inbound_path()
        ),
    )
    .unwrap();
    std::fs::write(
        home.join("systems/s1/agent2")
            .join(contract::binding_file()),
        r#"{"email":"p@example.test","webhook_url":""}"#,
    )
    .unwrap();
    // actual state: p@ is a stale row left by a historical push (must be withdrawn); other@ does not belong to this system (must not be touched)
    std::fs::write(
        home.join("bridge/aimail_routes.toml"),
        "p@example.test = \"http://127.0.0.1:9101{}\"\n\
         other@elsewhere.test = \"http://127.0.0.1:9999/x\"\n",
    )
    .unwrap();

    let (port, rx) = stub_admin(HashMap::new());
    let bridge_cfg = home.join("bridge/aimail_bridge.toml");
    std::fs::write(
        &bridge_cfg,
        format!("mode = \"pull\"\nbind = \"127.0.0.1:{}\"\n", port),
    )
    .unwrap();

    std::env::set_var("AIMAIL_HOME", &home);
    let cfg = json!({"domain": "example.test", "system_name": ""});
    let (reported, anchor) = reconcile_inbound_routes("s1", &cfg, "", &bridge_cfg);
    assert_eq!(reported, 2, "1 upsert + 1 withdraw");
    assert!(anchor.is_none());

    let mut seen: Vec<(String, String)> = Vec::new();
    while let Ok(r) = rx.recv_timeout(std::time::Duration::from_millis(300)) {
        seen.push((r.method, r.path));
    }
    assert!(
        seen.contains(&("POST".to_string(), "/api/v1/routes".to_string())),
        "{seen:?}"
    );
    assert!(
        seen.contains(&(
            "DELETE".to_string(),
            "/api/v1/routes/p%40example.test".to_string()
        )),
        "a stale row must be withdrawn: {seen:?}"
    );
    assert!(
        !seen.iter().any(|(_, p)| p.contains("elsewhere")),
        "a row not belonging to this system must not be touched: {seen:?}"
    );
}
