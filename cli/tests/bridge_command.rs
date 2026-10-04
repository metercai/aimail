//! `aimail bridge` 命令面（`cmd::bridge`）的离线验收。
//!
//! 判据：无参 ⇒ 状态查看（rc=0，含"配置不存在/路由表为空"的诚实播报）· `-s` ⇒ 按系统重刷
//! （目标取绑定 webhook_url、`host` 传**完整 URL**、请求落 `/api/v1/routes`、汇总行逐字）·
//! `--restart`/`--upgrade`（部署面，P2 切片3）⇒ **响亮未移植**（rc=1）而非静默成功。
//!
//! 说明：重刷按 Python 口径**直接打固定 admin 地址 `127.0.0.1:38081`** ⇒ stub 就绑这个端口
//! （正是要被验证的"固定地址"行为）；若该端口被占则本测试会失败 —— 属预期（真机同此）。

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

    // 1) 无参 ⇒ 状态查看：进程未运行 + 配置不存在 + 路由表为空，全是诚实播报，rc=0
    let (rc, out, _) = run(&home, &["bridge"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("  bridge 状态:"), "{out}");
    assert!(out.contains("进程: 未运行"), "{out}");
    assert!(out.contains("配置: aimail_bridge.toml 不存在"), "{out}");
    assert!(out.contains("路由表为空(aimail_routes.toml)"), "{out}");

    // 2) 部署面：`--upgrade` 仍**响亮未移植**（rc=1）；`--restart` 已接线 —— 本夹具无桥二进制
    //    ⇒ 走 legacy 回退并因启动失败而 rc=1（响亮失败，不是静默 0）
    let (rc, _, err) = run(&home, &["bridge", "--upgrade"]);
    assert_eq!(rc, 1, "--upgrade 应 rc=1");
    assert!(err.contains("not yet ported"), "--upgrade stderr: {err}");
    let (rc, out, _) = run(&home, &["bridge", "--restart"]);
    assert_eq!(
        rc, 1,
        "无桥二进制时 --restart 必须 rc=1（不得静默成功）: {out}"
    );
    assert!(
        out.contains("桥启动失败") || out.contains("旧桥"),
        "--restart 输出: {out}"
    );

    // 3) 重刷：stub admin 绑固定地址 127.0.0.1:38081（Python 同款行为）
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
            // 读到 Content-Length 满足为止（ureq 可能分两次写头与体 —— 早退会读到空 body）
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

    // 夹具：系统配置（存在性检查用）+ 一个已注册绑定（webhook_url 是唯一信任源）
    // 平台由"配置里的 system_home 反查"得出（与 Python 同序）⇒ 夹具给一个带 hermes 特征的 home
    // 平台 detect 必须**全部** markers 命中（hermes: hermes-agent + profiles）
    std::fs::create_dir_all(home.join("phome/hermes-agent")).unwrap();
    std::fs::create_dir_all(home.join("phome/profiles")).unwrap();
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
    assert!(out.contains("bridge 未运行"), "{out}");
    assert!(
        out.contains(&format!(
            "↻ a@example.test: (无) → http://127.0.0.1:9101{}",
            aimail::core::contract::inbound_path()
        )),
        "{out}"
    );
    assert!(out.contains("重刷完成: 1 条路由已注册/确认"), "{out}");

    let (method, path, body) = rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("stub 应收到一次路由请求");
    assert_eq!(method, "POST");
    assert_eq!(path, "/api/v1/routes");
    assert!(
        body.contains(&format!(
            "\"host\":\"http://127.0.0.1:9101{}\"",
            aimail::core::contract::inbound_path()
        )),
        "host 必须是完整 URL: {body}"
    );
    // 一个绑定只应产生一次请求（stub 记录的就是证据）
    assert!(rx
        .recv_timeout(std::time::Duration::from_millis(300))
        .is_err());
    let _: HashMap<String, String> = HashMap::new();
}
