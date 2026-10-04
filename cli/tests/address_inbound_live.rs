//! P4 切片5 验收：`address --inbound-live/--inbound-down`（hidden 面）。
//!
//! 判据：①两开关互斥逐字；②缺定位逐字；③`-e` 未命中；④`-a` 未命中并列本地 agent；
//! ⑤（略，见测试内注释）；⑥live + pull 绑定 ⇒ `skipped` 合成 + **rc=0**（幂等）；
//! ⑦down 且未声明桥 ⇒ `no_bridge` **静默 rc=0**；⑧down 且已声明桥 + 表内有本系统行 ⇒
//!   撤行 + `; backlog N pending`（stub 网关）+ 锚点 `route withdrawn` + rc=0。
//!
//! 说明：夹具必须带**绑定文件**（否则 `list_agents` 无从知道该 leaf 的 email ⇒ `-e` 查不到，
//! 这正是上一轮⑦判红的真因：夹具缺绑定文件，走的是"无绑定"守卫，不是 write 失败）。

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

/// 夹具：`with_binding` 决定是否写绑定文件；`webhook` 决定 pull(空) 还是 push。
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
        r#"{{"gateway_url":"http://127.0.0.1:{gw_port}","admin_key":"AK","system_id":"s1","domain":"example.test","manager_address":"m@example.test","default_agent_name":"agent""#
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
    // ①② 互斥 / 缺定位：夹具带绑定（避免误撞守卫）
    let (_t0, h0) = fixture(stub.port, true, "", Some(0));
    let (rc, out, _) = run(
        &h0,
        &["address", "-s", "s1", "--inbound-live", "--inbound-down"],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("互斥"), "{out}");
    let (rc, out, _) = run(&h0, &["address", "-s", "s1", "--inbound-live"]);
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("needs -a <agent> (or -e <email>) to locate the binding"),
        "{out}"
    );

    // ③④ 定位未命中
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

    // ⑤（略）"已定位但无绑定文件 ⇒ 守卫" 只在**平台枚举到、本地无绑定**时才可能触发：
    // 纯本地夹具下 list_agents 为空 ⇒ 与 Python 同样落 "no local address"（不是守卫）。
    // 该路径需平台 home 夹具（hermes profiles 等），留 CLI L2 门禁覆盖。

    // ⑥ live + pull 绑定 + 未声明桥 ⇒ skipped 合成、rc=0
    let (_t2, h2) = fixture(stub.port, true, "", None);
    let (rc, out, err) = run(
        &h2,
        &["address", "-s", "s1", "--inbound-live", "-a", "agent"],
    );
    assert_eq!(rc, 0, "⑥ 幂等应 rc=0\nout={out}\nerr={err}");
    assert!(
        out.contains("nothing to do"),
        "⑥ 应为 pull 的 skipped 合成: {out}"
    );

    // ⑦ down 且未声明桥 ⇒ no_bridge 静默 rc=0
    let (rc, out, err) = run(
        &h2,
        &["address", "-s", "s1", "--inbound-down", "-a", "agent"],
    );
    assert_eq!(rc, 0, "⑦ no_bridge 应静默 rc=0\nout={out}\nerr={err}");

    // ⑧ down 且已声明桥 + 表内有本系统行 ⇒ 撤行 + backlog + 锚点 already absent
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
    assert_eq!(rc, 0, "⑧ 应 rc=0\nout={out}\nerr={err}");
    assert!(
        out.contains("route(s) withdrawn for system s1"),
        "⑧ 应播报撤行数: {out}"
    );
    assert!(
        out.contains("backlog 2 pending"),
        "⑧ backlog 应来自 stub: {out}"
    );
    // 锚点行**在**表内 ⇒ 真实结果是 `route withdrawn`（already absent 只用于"锚点不在表里"）
    assert!(
        out.contains("route withdrawn: agent@example.test"),
        "⑧ 锚点结果: {out}"
    );
    let mut saw_delete = false;
    while let Ok((m, p, _)) = stub.rx.try_recv() {
        if m == "DELETE" && p.contains("agent%40example.test") {
            saw_delete = true;
        }
    }
    assert!(saw_delete, "⑧ 应真的发过 DELETE 撤行");
}
