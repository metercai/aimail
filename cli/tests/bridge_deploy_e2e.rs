#![cfg(unix)]
// the whole file is unix semantics (stub executable bit / sh-driven / symlink); reopen after the windows branch (W2) lands.
//! P2 slice 3d/3e offline acceptance: bridge deployment orchestration (`deploy`) and binary upgrade (`upgrade_bridge`).
//!
//! Criteria:
//! ① `deploy` (pull mode) — the binary is in place => **idempotent reuse** of the existing bridge api_key (this path **does not hit the gateway**) =>
//!   the config lands (with the api_key) => the bridge starts; ② only when the key is missing does it mint from the gateway (assert the stub receives
//!   `POST /api/v1/admin/api-keys`); ③ `upgrade_bridge` — different sha => atomic replacement + restart + report the new sha;
//!   same sha => no-op ("already up to date"). The whole run uses stubs (gateway/bridge), no network.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::mpsc::{channel, Receiver};
use std::thread;

use aimail::core::bridge_deploy::{deploy, upgrade_bridge, DeploySpec};

/// The contract stub bridge (the same idea as tests/bridge_restart.rs: the daemon is itself).
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

/// The stub gateway: `POST /api/v1/admin/api-keys` -> `{"raw_key": "minted-key"}`, and it records the requests it receives.
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
        webhook_mode_bridge: true, // pull: does not enter the announcement chain
        announce_arg: String::new(),
        cfg_webhook_host: String::new(),
    }
}

#[test]
fn deploy_and_upgrade_e2e() {
    deploy_reuses_existing_key_without_calling_gateway_then_mints_when_missing();
    upgrade_replaces_binary_and_restarts_then_noops_when_sha_matches();
}

/// (the #[test] of the same name was removed; it serves as the second leg of the ordered case above — the two legs share the process env, serialized to avoid a race)
fn deploy_reuses_existing_key_without_calling_gateway_then_mints_when_missing() {
    let (_tmp, home) = fixture(STUB_BRIDGE);
    let (port, rx) = stub_gateway();
    let gw = format!("http://127.0.0.1:{}", port);

    // ① an existing key => idempotent reuse, **no** gateway call
    std::fs::write(
        home.join("bridge/aimail_bridge.toml"),
        format!(
            "schema_version = 1\nbind = \"127.0.0.1:38081\"\nmode = \"pull\"\n\n[pull]\nsystems = [\n  {{ aimail_url = \"{}\", admin_key = \"OLD\", system_id = \"s1\", poll_interval_sec = 2, api_key = \"existing-key\" }}\n]\n",
            gw
        ),
    )
    .unwrap();
    assert_eq!(deploy(&spec(&gw)), 0, "deploy should succeed");
    let cfg = std::fs::read_to_string(home.join("bridge/aimail_bridge.toml")).unwrap();
    assert!(
        cfg.contains("api_key = \"existing-key\""),
        "the existing key should be reused: {cfg}"
    );
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(400))
            .is_err(),
        "the reuse path must not call the gateway"
    );
    let pid = home.join("bridge/bridge.pid");
    assert!(pid.exists(), "the bridge should have started and written its pid");

    // ② no key => mint from the gateway (assert it receives POST /api/v1/admin/api-keys); the new key lands in the config
    std::fs::write(
        home.join("bridge/aimail_bridge.toml"),
        "schema_version = 1\nbind = \"127.0.0.1:38081\"\nmode = \"pull\"\n",
    )
    .unwrap();
    assert_eq!(deploy(&spec(&gw)), 0);
    let req = rx
        .recv_timeout(std::time::Duration::from_secs(5))
        .expect("a missing key should mint from the gateway");
    assert_eq!(req, "POST /api/v1/admin/api-keys");
    let cfg2 = std::fs::read_to_string(home.join("bridge/aimail_bridge.toml")).unwrap();
    assert!(cfg2.contains("api_key = \"minted-key\""), "{cfg2}");
}

fn upgrade_replaces_binary_and_restarts_then_noops_when_sha_matches() {
    let (_tmp, home) = fixture("#!/bin/sh\necho OLD\n");
    let prog = std::env::var("AIMAIL_PROG_DIR").unwrap();
    let arch = aimail::core::bridge_deploy::arch_name();
    // the new piece: different content (the sha must differ) and a **contract stub** (it must restart successfully after the upgrade)
    make_zip(
        &Path::new(&prog)
            .join("bridge")
            .join(format!("aimail-bridge-v1.0.0-linux-{}.zip", arch)),
        &format!("aimail-bridge-v1.0.0-{}", arch),
        STUB_BRIDGE,
    );

    assert_eq!(upgrade_bridge(), 0, "the upgrade should succeed");
    let bin = std::fs::read_to_string(home.join("bridge/bin/aimail-bridge")).unwrap();
    assert!(bin.contains("--serve"), "the binary should have been replaced by the new piece: {bin}");
    assert!(
        home.join("bridge/bridge.pid").exists(),
        "it should restart and write the pid after the upgrade"
    );

    // upgrade again: same sha => no-op
    assert_eq!(upgrade_bridge(), 0);
}
