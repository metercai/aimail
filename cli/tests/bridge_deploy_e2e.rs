//! P2 切片3d/3e 离线验收：桥部署编排（`deploy`）与二进制升级（`upgrade_bridge`）。
//!
//! 判据：
//! ①`deploy`（pull 模式）—— 二进制就位 → **幂等复用**已有 bridge api_key（此路径**不打网关**）→
//!   配置落地（含 api_key）→ 起桥成功；②缺 key 时才向网关 mint（断言 stub 收到
//!   `POST /api/v1/admin/api-keys`）；③`upgrade_bridge` —— sha 不同 ⇒ 原子替换 + 重启 + 回报新 sha；
//!   sha 相同 ⇒ no-op（"已是最新"）。全程 stub（网关/桥），不触网。

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::mpsc::{channel, Receiver};
use std::thread;

use aimail::core::bridge_deploy::{deploy, upgrade_bridge, DeploySpec};

/// 契约 stub 桥（与 tests/bridge_restart.rs 同源思路：守护进程就是自己）。
const STUB_BRIDGE: &str = r#"#!/usr/bin/env python3
import sys, os, json, subprocess, time
argv = sys.argv[1:]
def opt(n):
    return argv[argv.index(n)+1] if n in argv else ""
pidf = opt("--pid-file")
def alive(p):
    try:
        os.kill(p, 0); return True
    except OSError:
        return False
if "--status" in argv:
    pid = None
    if pidf and os.path.exists(pidf):
        try: pid = int(open(pidf).read().strip())
        except Exception: pid = None
    ok = bool(pid) and alive(pid)
    print(json.dumps({"running": ok, "pid": pid}))
    sys.exit(0 if ok else 3)
if "--stop" in argv:
    if pidf and os.path.exists(pidf):
        try:
            pid = int(open(pidf).read().strip()); os.kill(pid, 15)
            for _ in range(50):
                if not alive(pid): break
                time.sleep(0.1)
        except (ValueError, OSError): pass
        try: os.remove(pidf)
        except OSError: pass
    sys.exit(0)
if "--serve" in argv:
    time.sleep(300); sys.exit(0)
p = subprocess.Popen([sys.argv[0], "--serve", "--pid-file", pidf], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if pidf: open(pidf, "w").write(str(p.pid))
sys.exit(0)
"#;

fn make_zip(path: &Path, entry: &str, content: &str) {
    let f = std::fs::File::create(path).unwrap();
    let mut zw = zip::ZipWriter::new(f);
    let opts = zip::write::SimpleFileOptions::default().unix_permissions(0o755);
    zw.start_file(entry, opts).unwrap();
    zw.write_all(content.as_bytes()).unwrap();
    zw.finish().unwrap();
}

/// stub 网关：`POST /api/v1/admin/api-keys` → `{"raw_key": "minted-key"}`，并记录收到的请求。
fn stub_gateway() -> (u16, Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = channel();
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
            loop {
                let n = match sock.read(&mut tmpb) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(_) => break,
                };
                buf.extend_from_slice(&tmpb[..n]);
                if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
            }
            let text = String::from_utf8_lossy(&buf).to_string();
            let first = text.lines().next().unwrap_or("").to_string();
            let mut it = first.split_whitespace();
            let method = it.next().unwrap_or("").to_string();
            let path = it.next().unwrap_or("").to_string();
            let _ = tx.send(format!("{} {}", method, path));
            let payload = r#"{"raw_key":"minted-key","id":"k1"}"#;
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

fn fixture(stub_bin_content: &str) -> (tempfile::TempDir, std::path::PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    let prog = tmp.path().join("prog");
    std::fs::create_dir_all(home.join("bridge/bin")).unwrap();
    std::fs::create_dir_all(prog.join("bridge")).unwrap();
    std::fs::create_dir_all(home.join("uhome")).unwrap();
    let bin = home.join("bridge/bin/aimail-bridge");
    std::fs::write(&bin, stub_bin_content).unwrap();
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    std::fs::write(
        home.join("bridge/aimail_bridge.toml"),
        "schema_version = 1\nbind = \"127.0.0.1:38081\"\nmode = \"pull\"\n",
    )
    .unwrap();
    std::env::set_var("AIMAIL_HOME", &home);
    std::env::set_var("AIMAIL_PROG_DIR", &prog);
    std::env::set_var("HOME", home.join("uhome"));
    (tmp, home)
}

fn spec(gw: &str) -> DeploySpec {
    DeploySpec {
        gateway_url: gw.to_string(),
        admin_key: "ADMIN".to_string(),
        system_id: "s1".to_string(),
        domain: "example.test".to_string(),
        webhook_mode_bridge: true, // pull：不进入公告链
        announce_arg: String::new(),
        cfg_webhook_host: String::new(),
    }
}

#[test]
fn deploy_and_upgrade_e2e() {
    deploy_reuses_existing_key_without_calling_gateway_then_mints_when_missing();
    upgrade_replaces_binary_and_restarts_then_noops_when_sha_matches();
}

/// （同名移除 #[test]，作为上面顺序用例的第二段 —— 两段共用进程 env，串行避免竞态）
fn deploy_reuses_existing_key_without_calling_gateway_then_mints_when_missing() {
    let (_tmp, home) = fixture(STUB_BRIDGE);
    let (port, rx) = stub_gateway();
    let gw = format!("http://127.0.0.1:{}", port);

    // ① 已有 key ⇒ 幂等复用，**不**打网关
    std::fs::write(
        home.join("bridge/aimail_bridge.toml"),
        format!(
            "schema_version = 1\nbind = \"127.0.0.1:38081\"\nmode = \"pull\"\n\n[pull]\nsystems = [\n  {{ aimail_url = \"{}\", admin_key = \"OLD\", system_id = \"s1\", poll_interval_sec = 2, api_key = \"existing-key\" }}\n]\n",
            gw
        ),
    )
    .unwrap();
    assert_eq!(deploy(&spec(&gw)), 0, "deploy 应成功");
    let cfg = std::fs::read_to_string(home.join("bridge/aimail_bridge.toml")).unwrap();
    assert!(
        cfg.contains("api_key = \"existing-key\""),
        "应复用既有 key: {cfg}"
    );
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(400))
            .is_err(),
        "复用路径不得调用网关"
    );
    let pid = home.join("bridge/bridge.pid");
    assert!(pid.exists(), "桥应已启动并写 pid");

    // ② 无 key ⇒ 向网关 mint（断言收到 POST /api/v1/admin/api-keys），新 key 落配置
    std::fs::write(
        home.join("bridge/aimail_bridge.toml"),
        "schema_version = 1\nbind = \"127.0.0.1:38081\"\nmode = \"pull\"\n",
    )
    .unwrap();
    assert_eq!(deploy(&spec(&gw)), 0);
    let req = rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("缺 key 时应向网关 mint");
    assert_eq!(req, "POST /api/v1/admin/api-keys");
    let cfg2 = std::fs::read_to_string(home.join("bridge/aimail_bridge.toml")).unwrap();
    assert!(cfg2.contains("api_key = \"minted-key\""), "{cfg2}");
}

fn upgrade_replaces_binary_and_restarts_then_noops_when_sha_matches() {
    let (_tmp, home) = fixture("#!/bin/sh\necho OLD\n");
    let prog = std::env::var("AIMAIL_PROG_DIR").unwrap();
    let arch = aimail::core::bridge_deploy::arch_name();
    // 新件：内容不同（sha 必不同）且是**契约 stub**（升级后要能重启成功）
    make_zip(
        &Path::new(&prog)
            .join("bridge")
            .join(format!("aimail-bridge-v1.0.0-linux-{}.zip", arch)),
        &format!("aimail-bridge-v1.0.0-{}", arch),
        STUB_BRIDGE,
    );

    assert_eq!(upgrade_bridge(), 0, "升级应成功");
    let bin = std::fs::read_to_string(home.join("bridge/bin/aimail-bridge")).unwrap();
    assert!(bin.contains("--serve"), "二进制应已替换为新件: {bin}");
    assert!(
        home.join("bridge/bridge.pid").exists(),
        "升级后应重启并写 pid"
    );

    // 再升一次：sha 相同 ⇒ no-op
    assert_eq!(upgrade_bridge(), 0);
}
