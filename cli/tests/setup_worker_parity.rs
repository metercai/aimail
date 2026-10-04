//! `install --system-only`（L1 激活/复用 worker）**整面跨语言 parity**。
//!
//! 走的是真入口：Python 侧跑 `cli/aimail install --system-only …`（它自己装配 INTEGRATE_* 并 spawn
//! worker），Rust 侧跑新二进制同参数。判据：
//! · stdout **逐字相同**（单行 JSON，Python 空格分隔符）；rc 相同；
//! · 落盘的 `aimail_gateway.json` **逐字节相同**、`.system_raw_key.key` 相同；
//! · stderr 只做"都包含关键日志行"的弱断言（两侧 logging 前缀同形，但行序/措辞可能差一字，
//!   不强判；stdout 才是一行 JSON 契约的载体）。
//!
//! 场景（stub 网关固定应答）：system 级 key → 降级成功（whoami 说 system、api-keys 给新 raw_key）。

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::thread;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .to_path_buf()
}

fn rust_bin() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("target");
    p.push(if cfg!(debug_assertions) {
        "debug"
    } else {
        "release"
    });
    p.push("aimail");
    p
}

/// 起 stub 网关：按路径应答（含 whoami / api-keys / activate-system）。
fn stub_gateway(replies: Vec<(&'static str, u16, &'static str)>) -> String {
    let map: BTreeMap<String, (u16, String)> = replies
        .into_iter()
        .map(|(p, c, b)| (p.to_string(), (c, b.to_string())))
        .collect();
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        for sock in listener.incoming() {
            let Ok(mut sock) = sock else { break };
            let _ = sock.set_read_timeout(Some(std::time::Duration::from_millis(300)));
            let mut raw: Vec<u8> = Vec::new();
            let mut buf = vec![0u8; 16384];
            loop {
                match sock.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        raw.extend_from_slice(&buf[..n]);
                        if let Some(pos) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
                            let head = String::from_utf8_lossy(&raw[..pos]).to_lowercase();
                            let want: usize = head
                                .lines()
                                .find_map(|l| l.strip_prefix("content-length:"))
                                .and_then(|v| v.trim().parse().ok())
                                .unwrap_or(0);
                            if raw.len() >= pos + 4 + want {
                                break;
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
            let text = String::from_utf8_lossy(&raw).to_string();
            let path = text
                .lines()
                .next()
                .and_then(|l| l.split_whitespace().nth(1))
                .unwrap_or("")
                .to_string();
            let (code, payload) = map
                .get(&path)
                .cloned()
                .unwrap_or((404, "{\"error\":\"not found\"}".to_string()));
            let resp = format!(
                "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                payload.len()
            );
            let _ = sock.write_all(resp.as_bytes());
        }
    });
    format!("http://127.0.0.1:{port}")
}

struct Run {
    rc: i32,
    stdout: String,
    stderr: String,
}

fn read_cfg(home: &Path, sid: &str) -> String {
    std::fs::read_to_string(home.join("systems").join(sid).join("aimail_gateway.json"))
        .unwrap_or_default()
}

fn read_raw_key(home: &Path, sid: &str) -> String {
    std::fs::read_to_string(home.join("systems").join(sid).join(".system_raw_key.key"))
        .unwrap_or_default()
}

#[test]
fn system_only_activation_reuse_matches_python() {
    let gw = stub_gateway(vec![
        (
            "/api/v1/whoami",
            200,
            r#"{"category":"system","scope":"system","system_id":"s1","email":""}"#,
        ),
        (
            "/api/v1/admin/api-keys",
            200,
            r#"{"raw_key":"domain-key-1"}"#,
        ),
    ]);
    let tmp = tempfile::tempdir().unwrap();
    let cwd = repo_root();
    // 平台根：两侧**共用同一个**路径 ⇒ cfg 里的 system_home 字段相同 ⇒ 可逐字节比
    let platform_home = tmp.path().join("platform");
    std::fs::create_dir_all(&platform_home).unwrap();

    // 部署形态布局：`<prog>/aimail-src` → 仓库 ⇒ 两侧装载同一份 repo `.env`（_load_env 契约）
    let prog = tmp.path().join("prog");
    std::fs::create_dir_all(&prog).unwrap();
    let _ = std::os::unix::fs::symlink(&cwd, prog.join("aimail-src"));
    let py_home = tmp.path().join("py-home");
    let rs_home = tmp.path().join("rs-home");
    std::fs::create_dir_all(&py_home).unwrap();
    std::fs::create_dir_all(&rs_home).unwrap();
    let platform = platform_home.to_string_lossy().to_string();

    let py = std::process::Command::new("python3")
        .args([
            "cli/aimail",
            "install",
            "--system-only",
            "-H",
            &platform,
            "-s",
            "s1",
            "-k",
            "test-key",
            "-g",
            &gw,
        ])
        .current_dir(&cwd)
        .env("AIMAIL_HOME", &py_home)
        .env("AIMAIL_PROG_DIR", &prog)
        .env("INTEGRATE_NAME_EXPLICIT", "true")
        .env("AIMAIL_SAVE_SNAPSHOTS", "yes")
        .output()
        .expect("python3");
    let py_run = Run {
        rc: py.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&py.stdout).to_string(),
        stderr: String::from_utf8_lossy(&py.stderr).to_string(),
    };

    let rs = std::process::Command::new(rust_bin())
        .args([
            "install",
            "--system-only",
            "-H",
            &platform,
            "-s",
            "s1",
            "-k",
            "test-key",
            "-g",
            &gw,
        ])
        .current_dir(&cwd)
        .env("AIMAIL_HOME", &rs_home)
        .env("AIMAIL_PROG_DIR", &prog)
        .env("INTEGRATE_NAME_EXPLICIT", "true")
        .env("AIMAIL_SAVE_SNAPSHOTS", "yes")
        .output()
        .expect("rust bin");
    let rs_run = Run {
        rc: rs.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&rs.stdout).to_string(),
        stderr: String::from_utf8_lossy(&rs.stderr).to_string(),
    };

    // ── stdout（单行 JSON 契约）+ rc ──────────────────────────────────────────
    assert_eq!(py_run.rc, 0, "python 侧应成功: {}", py_run.stderr);
    assert_eq!(
        py_run.stdout, rs_run.stdout,
        "stdout（单行 JSON）必须逐字相同\npy={:?}\nrs={:?}\nrs_stderr={:?}",
        py_run.stdout, rs_run.stdout, rs_run.stderr
    );
    assert_eq!(py_run.stdout.trim_end().lines().count(), 1, "恰一行");
    assert_eq!(rs_run.rc, 0, "rust 侧应成功: {}", rs_run.stderr);

    // ── 落盘：cfg 逐字节 + 原始 key ──────────────────────────────────────────
    let py_cfg = read_cfg(&py_home, "s1");
    let rs_cfg = read_cfg(&rs_home, "s1");
    assert!(!py_cfg.is_empty(), "python 应写出 cfg");
    assert_eq!(py_cfg, rs_cfg, "cfg 必须逐字节相同");
    // 没给域 ⇒ 无域可收窄 ⇒ **不降级**（cfg 保留系统级 key），但系统级 key 照旧落盘
    // （Python `_downgrade_to_domain_admin_key` 的 domain 用的是未套默认值的那份 ⇒ 空）
    let v: serde_json::Value = serde_json::from_str(&py_cfg).unwrap();
    assert_eq!(v["admin_key"], serde_json::json!("test-key"));
    assert_eq!(v["domain"], serde_json::json!("admin.local"));
    assert_eq!(read_raw_key(&py_home, "s1"), "test-key\n");
    assert_eq!(read_raw_key(&rs_home, "s1"), "test-key\n");

    // ── stderr：弱断言（两侧都应出现关键日志）────────────────────────────────
    // stderr 也逐字比：Python 默认 WARNING 级 ⇒ `logger.info` 不输出、lastResort 打裸消息
    assert_eq!(
        py_run.stderr, rs_run.stderr,
        "stderr 应逐字相同（日志级别/前缀口径都要照抄）"
    );
}

#[test]
fn system_only_platform_key_is_refused_by_both() {
    // 平台级 key 必须被拒（身份对齐），且两侧 stdout/rc 一致
    let gw = stub_gateway(vec![(
        "/api/v1/whoami",
        200,
        r#"{"category":"platform","scope":"platform","system_id":"gw"}"#,
    )]);
    let tmp = tempfile::tempdir().unwrap();
    let platform = tmp.path().join("p");
    std::fs::create_dir_all(&platform).unwrap();
    let platform = platform.to_string_lossy().to_string();
    let cwd = repo_root();

    let run = |home: &Path, prog: &Path, is_py: bool| -> Run {
        let mut cmd = if is_py {
            let mut c = std::process::Command::new("python3");
            c.args(["cli/aimail"]);
            c
        } else {
            std::process::Command::new(prog)
        };
        let out = cmd
            .args([
                "install",
                "--system-only",
                "-H",
                &platform,
                "-s",
                "s1",
                "-k",
                "gw-key",
                "-g",
                &gw,
            ])
            .current_dir(&cwd)
            .env("AIMAIL_HOME", home)
            .env("AIMAIL_PROG_DIR", home.join("prog"))
            .output()
            .expect("spawn");
        Run {
            rc: out.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&out.stdout).to_string(),
            stderr: String::from_utf8_lossy(&out.stderr).to_string(),
        }
    };
    let py = run(&tmp.path().join("py"), &rust_bin(), true);
    std::fs::create_dir_all(tmp.path().join("rs")).unwrap();
    let rs = run(&tmp.path().join("rs"), &rust_bin(), false);
    assert_eq!(py.rc, 1, "平台级 key 必须失败: {}", py.stdout);
    assert_eq!(rs.rc, 1, "rust 同判: {}", rs.stdout);
    assert_eq!(py.stdout, rs.stdout, "错误信封必须逐字相同");
    assert!(
        py.stdout.contains("gateway-side admin key"),
        "python 文案: {}",
        py.stdout
    );
}

/// 有 `-d <域>` ⇒ 走降级路径：whoami 说 system + api-keys 给新 key ⇒ cfg.admin_key 变新 key。
#[test]
fn system_only_downgrades_when_domain_is_given() {
    let gw = stub_gateway(vec![
        (
            "/api/v1/whoami",
            200,
            r#"{"category":"system","scope":"system","system_id":"s1","email":""}"#,
        ),
        (
            "/api/v1/admin/api-keys",
            200,
            r#"{"raw_key":"domain-key-1"}"#,
        ),
    ]);
    let tmp = tempfile::tempdir().unwrap();
    let cwd = repo_root();
    let prog = tmp.path().join("prog");
    std::fs::create_dir_all(&prog).unwrap();
    let _ = std::os::unix::fs::symlink(&cwd, prog.join("aimail-src"));
    let platform_dir = tmp.path().join("platform");
    std::fs::create_dir_all(&platform_dir).unwrap();
    let platform = platform_dir.to_string_lossy().to_string();
    let py_home = tmp.path().join("py-home");
    let rs_home = tmp.path().join("rs-home");
    std::fs::create_dir_all(&py_home).unwrap();
    std::fs::create_dir_all(&rs_home).unwrap();

    let args = |extra: &mut Vec<String>| {
        let mut v: Vec<String> = vec![
            "install".into(),
            "--system-only".into(),
            "-H".into(),
            platform.clone(),
            "-s".into(),
            "s1".into(),
            "-k".into(),
            "test-key".into(),
            "-g".into(),
            gw.clone(),
            "-d".into(),
            "example.test".into(),
        ];
        v.append(extra);
        v
    };
    let py = std::process::Command::new("python3")
        .args(["cli/aimail"])
        .args(args(&mut vec![]))
        .current_dir(&cwd)
        .env("AIMAIL_HOME", &py_home)
        .env("AIMAIL_PROG_DIR", &prog)
        .env("INTEGRATE_NAME_EXPLICIT", "true")
        .output()
        .expect("python3");
    let rs = std::process::Command::new(rust_bin())
        .args(args(&mut vec![]))
        .current_dir(&cwd)
        .env("AIMAIL_HOME", &rs_home)
        .env("AIMAIL_PROG_DIR", &prog)
        .env("INTEGRATE_NAME_EXPLICIT", "true")
        .output()
        .expect("rust bin");
    assert_eq!(
        String::from_utf8_lossy(&py.stdout),
        String::from_utf8_lossy(&rs.stdout),
        "stdout 必须逐字相同; py_stderr={} rs_stderr={}",
        String::from_utf8_lossy(&py.stderr),
        String::from_utf8_lossy(&rs.stderr)
    );
    let py_cfg = read_cfg(&py_home, "s1");
    let rs_cfg = read_cfg(&rs_home, "s1");
    assert_eq!(py_cfg, rs_cfg, "cfg 逐字节相同");
    let v: serde_json::Value = serde_json::from_str(&py_cfg).unwrap();
    assert_eq!(
        v["admin_key"],
        serde_json::json!("domain-key-1"),
        "有域 ⇒ 降级"
    );
    assert_eq!(v["domain"], serde_json::json!("example.test"));
    assert_eq!(read_raw_key(&py_home, "s1"), "test-key\n");
    assert_eq!(read_raw_key(&rs_home, "s1"), "test-key\n");
    assert_eq!(
        String::from_utf8_lossy(&py.stderr),
        String::from_utf8_lossy(&rs.stderr),
        "stderr 逐字相同"
    );
}
