//! C-2 网络级验收：gateway 对 whoami 探针回 401 ⇒ 三态 = (reachable, Some(401))，
//! plan 报 "identity rejected"，绝不报 "not reachable"；网关停掉 ⇒ (false, None)。
//! （纯函数层的对应断言在 core/webhookplan.rs::tests；这里钉住真实 HTTP 路径。）

use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::mpsc;
use std::thread;

fn stub_http(code: u16, payload: &str) -> (String, mpsc::Receiver<bool>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().unwrap().port();
    let payload = payload.to_string();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let Ok((mut sock, _)) = listener.accept() else {
            return;
        };
        let _ = sock.set_read_timeout(Some(std::time::Duration::from_millis(500)));
        let mut buf = vec![0u8; 16384];
        let _ = sock.read(&mut buf);
        let resp = format!(
            "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
            payload.len()
        );
        let _ = sock.write_all(resp.as_bytes());
        let _ = tx.send(true);
    });
    (format!("http://127.0.0.1:{port}"), rx)
}

#[test]
fn whoami_401_is_reachable_rejected_not_unreachable() {
    let (url, rx) = stub_http(401, r#"{"error":"identity_required"}"#);
    let (reachable, rejected) =
        aimail::core::webhookplan::gw_admin_reachable(&url, "k123", "sid-1");
    assert!(
        rx.recv_timeout(std::time::Duration::from_secs(5)).is_ok(),
        "probe sent"
    );
    assert!(reachable, "a 401 answer means the gateway is up");
    assert_eq!(rejected, Some(401));

    // plan() 必须把它报成 identity 问题，而不是不可达。
    let plan = aimail::core::webhookplan::plan(&aimail::core::webhookplan::PlanInputs {
        gw_reachable: reachable,
        gw_rejected: rejected,
        bridge_host: None,
        probe_gw_to_bridge: None,
        probe_bridge_to_agent: None,
        gw_is_local: false,
        probe_gw_to_agent: None,
        bridge_deployable: false,
    });
    match plan {
        aimail::core::webhookplan::WebhookPlan::Fail { reason } => {
            assert!(reason.contains("401"), "{reason}");
            assert!(reason.contains("identity"), "{reason}");
            assert!(!reason.contains("not reachable"), "{reason}");
        }
        other => panic!("expected Fail, got {other:?}"),
    }
}

#[test]
fn whoami_2xx_is_reachable_not_rejected() {
    let (url, rx) = stub_http(200, r#"{"scope":"system"}"#);
    let (reachable, rejected) =
        aimail::core::webhookplan::gw_admin_reachable(&url, "k123", "sid-1");
    assert!(
        rx.recv_timeout(std::time::Duration::from_secs(5)).is_ok(),
        "probe sent"
    );
    assert!(reachable);
    assert_eq!(rejected, None);
}

#[test]
fn dead_gateway_is_not_reachable_and_not_rejected() {
    // 127.0.0.1:1 — 无监听 ⇒ 连接拒绝 ⇒ (false, None)。
    let (reachable, rejected) =
        aimail::core::webhookplan::gw_admin_reachable("http://127.0.0.1:1", "k123", "sid-1");
    assert!(!reachable, "connection refused is not reachable");
    assert_eq!(rejected, None, "no HTTP status exists to reject with");
}
