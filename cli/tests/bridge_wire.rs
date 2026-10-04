//! `core::bridge_wire`（= `cli/bridge_wire.py` 的 rust 复刻）**离线验收**。
//!
//! 判据：四态决策（ok/skipped/no_bridge/failed）与三步顺序 · `validate_target` 的拒绝理由逐字 ·
//! 声明读取的四种形态（无/系统配置带端口/桥配置在/TOML 坏 ⇒ fail closed）· 请求形状
//! （POST `/api/v1/routes` 体 `{email,host(完整URL),port:80}`、DELETE `/api/v1/routes/<email>`）·
//! `down` 不对称（绑定端点为空也**必须**撤回）· 回执行文案逐字。全程 stub admin API，不触网。

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::mpsc::{channel, Receiver};
use std::thread;

use aimail::core::bridge_wire::*;

const BINDING: &str = "agentmail.json";
use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq, Eq)]
struct Req {
    method: String,
    path: String,
    body: String,
}

/// 起一个 stub admin API：按 `replies`（path → (status, reason/body)）应答，并记录收到的请求。
/// `/health` 默认答 200 + `{"status":"ok","uptime_secs":1,"version":"9.9.9"}`。
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

    // 1) 无桥配置、无端口 ⇒ 未声明 + 原因逐字
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

    // 2) 系统配置带 bridge_admin_port ⇒ 已声明、端口取自系统配置
    let d = load_declaration(&json!({"bridge_admin_port": 39999}), &missing);
    assert!(d.declared && d.usable);
    assert_eq!(d.admin_port, 39999);
    assert_eq!(d.source, "system config bridge_admin_port");
    assert_eq!(d.gateway_port, 39999);

    // 3) 桥配置在位：mode/bind 读出；端口取 bind（系统配置没给端口时）
    let cfg = write_bridge_cfg(tmp.path(), "mode = \"pull\"\nbind = \"0.0.0.0:38080\"\n");
    let d = load_declaration(&json!({}), &cfg);
    assert!(d.declared && d.usable);
    assert_eq!(d.mode, "pull");
    assert_eq!(d.bind, "0.0.0.0:38080");
    assert_eq!(d.admin_port, 38080);
    assert_eq!(d.source, cfg.display().to_string());

    // 4) 坏 TOML ⇒ 声明了但不可用（fail closed），且**不是**"没有桥"
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

    // 裸 host:port 必须被拒（补前缀会产出不可投递形态）
    let (t, why) = validate_target(&json!("127.0.0.1:9101"));
    assert!(t.is_empty());
    assert_eq!(
        why,
        "not an absolute http(s) URL: '127.0.0.1:9101' has no http(s):// scheme \
         (a bare host:port cannot be delivered to)"
    );

    // 绝对 URL 原样透传（含路径）
    let (t, why) = validate_target(&json!("http://127.0.0.1:9101/aimail/inbound"));
    assert_eq!(t, "http://127.0.0.1:9101/aimail/inbound");
    assert!(why.is_empty());

    // 越界端口：Python 的 `u.port` 直接抛 ValueError ⇒ 走 "not a usable URL (...)" 分支
    let (_, why) = validate_target(&json!("http://host:99999/x"));
    assert_eq!(
        why,
        "'http://host:99999/x' is not a usable URL (Port out of range 0-65535)"
    );
    // 端口 0：`u.port` 不抛，但 `_valid_port` 拒 ⇒ "carries an invalid port"
    let (_, why) = validate_target(&json!("http://host:0/x"));
    assert_eq!(why, "'http://host:0/x' carries an invalid port");
}

#[test]
fn sync_route_four_states_and_request_shapes() {
    let tmp = tempfile::tempdir().unwrap();
    let no_cfg = tmp.path().join("aimail_bridge.toml");
    let gw = json!({});
    let url = json!("http://127.0.0.1:9101/aimail/inbound");

    // 1) 没有声明桥 ⇒ no_bridge（刻意的 no-op，不报错）
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

    // 2) 声明了但不可达 ⇒ failed + "nothing changed"（fail closed）
    let cfg = write_bridge_cfg(tmp.path(), "mode = \"pull\"\nbind = \"127.0.0.1:38999\"\n");
    let out = sync_route(ACTION_LIVE, "a@example.test", &url, &gw, &cfg, ADMIN_HOST);
    assert_eq!(out["state"], STATE_FAILED);
    let line = format_line(&out);
    assert!(line.contains("route FAILED for a@example.test:"), "{line}");
    assert!(
        line.contains("is not reachable — nothing changed"),
        "{line}"
    );

    // 3) 可达 + live + 合法 URL ⇒ ok，且请求形状 = POST /api/v1/routes {email,host(完整URL),port:80}
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
    assert_eq!(body["host"], "http://127.0.0.1:9101/aimail/inbound"); // 完整 URL、原样
    assert_eq!(body["port"], 80); // 占位（admin.rs 拒绝 0）
    assert_eq!(
        format_line(&out),
        "route: a@example.test -> http://127.0.0.1:9101/aimail/inbound"
    );

    // 4) live + 空端点 ⇒ skipped（合法 pull 绑定），**不发请求**
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

    // 5) down + 空端点 ⇒ **仍须撤回**（不对称），DELETE 路径带转义
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

    // 6) 桥答 5xx ⇒ failed，原因带状态码
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
        "# comment\nb@example.test = \"http://127.0.0.1:9101/aimail/inbound\"\n\na@example.test = \"http://127.0.0.1:9099/inbound\"\n",
    )
    .unwrap();
    let routes = read_routes(&p);
    assert_eq!(routes.len(), 2);
    assert_eq!(
        routes.get("a@example.test").unwrap(),
        "http://127.0.0.1:9099/inbound"
    );

    // `_target_to_route_fields`：完整 URL ⇒ (url, 80)；裸 host:port ⇒ 拆两字段
    assert_eq!(
        target_to_route_fields("http://127.0.0.1:9101/x"),
        ("http://127.0.0.1:9101/x".to_string(), 80)
    );
    assert_eq!(
        target_to_route_fields("127.0.0.1:9101"),
        ("127.0.0.1".to_string(), 9101)
    );

    // `_system_route_scope`：shared 后缀 / 非 shared 后缀 / 缺 domain ⇒ None
    let sc = system_route_scope(&json!({"domain": "d.test", "system_name": "sys1"})).unwrap();
    assert!(sc("billing.sys1@d.test"));
    assert!(!sc("billing.sys2@d.test"));
    assert!(!sc("billing@d.test"));
    let sc2 = system_route_scope(&json!({"domain": "d.test"})).unwrap();
    assert!(sc2("billing@d.test"));
    // 非 shared（无 system_name）：`@{domain}` 后缀 ⇒ 该域内**全部**地址都属本系统
    // （`system_domains.domain_addr UNIQUE` ⇒ 域唯一属主），shared 形态也落在此后缀内
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
    // 期望态：agent1 = push（有 webhook_url）⇒ upsert；agent2 = pull（空）⇒ 不在期望态，但属本系统
    std::fs::write(
        home.join("systems/s1/agent1").join(BINDING),
        r#"{"email":"a@example.test","webhook_url":"http://127.0.0.1:9101/aimail/inbound"}"#,
    )
    .unwrap();
    std::fs::write(
        home.join("systems/s1/agent2").join(BINDING),
        r#"{"email":"p@example.test","webhook_url":""}"#,
    )
    .unwrap();
    // 实际态：p@ 是历史 push 留下的陈旧行（须撤回）；other@ 不属本系统（不许动）
    std::fs::write(
        home.join("bridge/aimail_routes.toml"),
        "p@example.test = \"http://127.0.0.1:9101/aimail/inbound\"\n\
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
        "陈旧行必须撤回: {seen:?}"
    );
    assert!(
        !seen.iter().any(|(_, p)| p.contains("elsewhere")),
        "不属本系统的行不许动: {seen:?}"
    );
}
