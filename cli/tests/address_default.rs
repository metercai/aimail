//! P4 切片3 验收：`address -d/--default`（默认主 agent 名）。
//!
//! 判据（对齐 `cli/aimail:2472 _set_default_agent_name`）：
//! ①非法名字（`.`/空格/`@`）⇒ 失败且**不发请求**；②同名 ⇒ `  已是当前默认名: …` + rc=0（不写盘）；
//! ③域名 base 段占用 ⇒ `名字 '{n}' 已占用: ['…']`（Python list repr）rc=1；
//! ④无冲突 ⇒ 写盘（`default_agent_name` 落盘）+ `默认主 agent 名: {c} → {n}` + 两行生效提示，rc=0。
//!
//! **踩坑记录**：`-n <名字>` 单独出现走的是 **set-name**（Python op 链：default > set-manager >
//! set-name > show）⇒ 本面必须用 `-d`；上一轮我误用 `-n` 做验收，把 rc=1 误判成"写盘失败"。
//!
//! 已知差异：写盘走本仓 canonical `config::save_gateway_config_in`（结构化重写，会补默认字段、
//! 不保留未建模键），而 Python 是 `_atomic_json_write` **盲写原 dict** ⇒ 文件内容不完全逐字一致（净效果同）。

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
        .output()
        .expect("run aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
    )
}

fn stub(reply: &'static str) -> (u16, std::sync::mpsc::Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut sock = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            let mut buf = [0u8; 4096];
            let n = sock.read(&mut buf).unwrap_or(0);
            let text = String::from_utf8_lossy(&buf[..n]).to_string();
            let mut it = text.lines().next().unwrap_or("").split_whitespace();
            let _ = tx.send(format!(
                "{} {}",
                it.next().unwrap_or(""),
                it.next().unwrap_or("")
            ));
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                reply.len(), reply
            );
            let _ = sock.write_all(resp.as_bytes());
            let _ = sock.flush();
        }
    });
    (port, rx)
}

fn fixture(gw: &str) -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("systems/s1")).unwrap();
    std::fs::create_dir_all(home.join("uhome")).unwrap();
    std::fs::write(
        home.join("systems/s1/aimail_gateway.json"),
        format!(
            r#"{{"gateway_url":"{}","admin_key":"AK","system_id":"s1","domain":"example.test","system_name":"","default_agent_name":"agent"}}"#,
            gw
        ),
    )
    .unwrap();
    tmp
}

#[test]
fn address_default_validates_short_circuits_conflicts_and_writes() {
    // ① 非法名字：不发请求
    let (port, rx) = stub("[]");
    let t1 = fixture(&format!("http://127.0.0.1:{}", port));
    let home1 = t1.path().join("ahome");
    for bad in ["bad.name", "has space", "a@b"] {
        let (rc, out) = run(&home1, &["address", "-s", "s1", "-d", bad]);
        assert_eq!(rc, 1, "bad={bad} out={out}");
        assert!(
            out.contains(&format!(
                "非法名字 '{bad}':须为 atext-no-dot 字符(不能含点/空格/@)"
            )),
            "{out}"
        );
    }
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(300))
            .is_err(),
        "非法名不得请求"
    );

    // ② 同名短路：rc=0、不请求
    let (rc2, out2) = run(&home1, &["address", "-s", "s1", "-d", "agent"]);
    assert_eq!(rc2, 0, "{out2}");
    assert!(out2.contains("  已是当前默认名: agent"), "{out2}");
    assert!(
        rx.recv_timeout(std::time::Duration::from_millis(300))
            .is_err(),
        "同名短路不得请求"
    );

    // ③ 冲突：base 段占用 ⇒ list repr 文案 + rc=1
    let (port3, _rx3) =
        stub(r#"[{"domain":"newname@example.test"},{"domain":"other@example.test"}]"#);
    let t3 = fixture(&format!("http://127.0.0.1:{}", port3));
    let (rc3, out3) = run(
        &t3.path().join("ahome"),
        &["address", "-s", "s1", "-d", "newname"],
    );
    assert_eq!(rc3, 1, "{out3}");
    assert!(
        out3.contains("名字 'newname' 已占用: ['newname@example.test']"),
        "{out3}"
    );

    // ④ 无冲突 ⇒ 写盘 + 三行播报
    let (port4, rx4) = stub(r#"[{"domain":"other@example.test"}]"#);
    let t4 = fixture(&format!("http://127.0.0.1:{}", port4));
    let home4 = t4.path().join("ahome");
    let (rc4, out4) = run(&home4, &["address", "-s", "s1", "-d", "newname"]);
    assert_eq!(rc4, 0, "{out4}");
    let first = rx4.recv_timeout(std::time::Duration::from_secs(3)).unwrap();
    assert_eq!(first, "GET /api/v1/admin/systems/s1/domains");
    assert!(out4.contains("默认主 agent 名: agent → newname"), "{out4}");
    assert!(
        out4.contains("  生效方式:agent 地址将变为 newname@example.test(共享域加 . 后缀)"),
        "{out4}"
    );
    assert!(
        out4.contains("  需重注册:aimail reset --system-id s1 或 aimail install"),
        "{out4}"
    );
    let written = std::fs::read_to_string(home4.join("systems/s1/aimail_gateway.json")).unwrap();
    assert!(
        written.contains(r#""default_agent_name": "newname""#),
        "{written}"
    );
}
