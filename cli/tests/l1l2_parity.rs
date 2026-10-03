//! L1（gateway）+ L2（bridge）探针的跨语言验收 —— 与 `l0_parity.rs` 同一思路，
//! 用同一夹具上"Rust 记录 vs Python `check_status.py --json` 记录"比对。
//!
//! **唯一的归一化**：三条记录的 detail 里带 OS/网络错误原文（Python 用 urllib/socket，
//! Rust 用 ureq/std::net，错误串天然不同），只比到错误原文之前的前缀：
//! `gateway/health`（`HTTP 0: …`）、`gateway/smtp_port`（`Port 25 unreachable: …`）、
//! `bridge/self_health`（`Unreachable at …/health: …`）。前缀不同 = 走了不同分支 ⇒ 仍会被抓到。

mod common;

use std::path::PathBuf;

use aimail::core::check::Check;
use aimail::core::checks::l0::Ctx;
use aimail::core::checks::{l1, l2};
use aimail::core::{config, contract};
use common::{filter_records, python_check_json, Record, TempDir};
use serde_json::Value;

/// L1/L2 两个组的记录名（L0 的 bridge 完备性记录不在此列，由 l0_parity 负责）。
const WANTED: &[(&str, &str)] = &[
    ("gateway", "config"),
    ("gateway", "health"),
    ("gateway", "smtp_port"),
    ("gateway", "api_key"),
    ("bridge", "config"),
    ("bridge", "process"),
    ("bridge", "activity"),
    ("bridge", "pull_path"),
    ("bridge", "self_health"),
    ("bridge", "config_consistency"),
];

/// 含 OS 错误原文的记录 ⇒ 只比前缀（其余记录逐字比）。
fn normalize(level: &str, check: &str, detail: &str) -> String {
    match (level, check) {
        ("gateway", "health") | ("gateway", "smtp_port") | ("bridge", "self_health") => {
            detail.split(": ").next().unwrap_or(detail).to_string()
        }
        _ => detail.to_string(),
    }
}

fn normalize_record(r: &Record) -> Record {
    (
        r.0.clone(),
        r.1.clone(),
        r.2,
        normalize(&r.0, &r.1, &r.3),
        r.4.clone(),
    )
}

fn build_fixture(tmp: &TempDir) -> (PathBuf, PathBuf) {
    // 端口 1 = 必然连不上（死端口用例）
    build_fixture_with(tmp, 1)
}

/// `port` 同时用于网关 URL、桥 addr 与 pull.aimail_url —— 传桩端口即可覆盖成功分支，
/// 传 1 即覆盖"连不上"分支。
fn build_fixture_with(tmp: &TempDir, port: u16) -> (PathBuf, PathBuf) {
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    let sysdir = aimail_home.join("systems").join("s1");
    std::fs::create_dir_all(&sysdir).unwrap();
    std::fs::create_dir_all(user_home.join(".hermes")).unwrap();

    std::fs::write(
        sysdir.join(config::GATEWAY_CONFIG_NAME),
        format!(
            r#"{{
  "gateway_url": "http://127.0.0.1:{port}",
  "admin_key": "k",
  "system_id": "s1",
  "system_name": "s1",
  "domain": "example.test",
  "save_raw_snapshots": true
}}"#
        ),
    )
    .unwrap();

    // 桥：pull 模式 + 本机 addr + 完整 pull 凭据
    let bridge = aimail_home.join("bridge");
    std::fs::create_dir_all(&bridge).unwrap();
    std::fs::write(
        bridge.join("aimail_bridge.toml"),
        format!(
            "mode = \"pull\"\naddr = \"127.0.0.1:{port}\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\naimail_url = \"http://127.0.0.1:{port}\"\napi_key = \"k\"\n"
        ),
    )
    .unwrap();
    // 指针（L0 的 pointer 记录也由 python 产出，这里过滤掉不看）
    std::fs::write(
        user_home.join(".hermes").join(contract::pointer_file()),
        r#"{"system_id":"s1","email":"a@example.test"}"#,
    )
    .unwrap();

    (aimail_home, user_home)
}

#[test]
fn l1_l2_records_match_python_check_status() {
    let tmp = TempDir::new("l1l2");
    let (aimail_home, user_home) = build_fixture(&tmp);

    let Some(py_checks) = python_check_json(&aimail_home, &user_home, "s1") else {
        println!("SKIP: python3 或 check_status.py 不可用 —— 未做跨语言比对");
        return;
    };
    let expected: Vec<Record> = filter_records(&py_checks, WANTED)
        .iter()
        .map(normalize_record)
        .collect();
    assert!(
        expected.len() >= 6,
        "L1/L2 记录太少（{} 条）—— 夹具或筛选可能失效，别让这条测试空过",
        expected.len()
    );

    // 顺序照抄 Python `main()`：check_gateway → check_bridge
    let ctx = Ctx {
        aimail_home,
        user_home,
    };
    let mut c = Check::new();
    l1::gateway(&mut c, "s1", &ctx);
    l2::bridge(&mut c, "s1", &ctx);
    let actual: Vec<Record> = c
        .checks
        .iter()
        .filter(|r| WANTED.iter().any(|(l, k)| *l == r.level && *k == r.check))
        .map(|r| {
            normalize_record(&(
                r.level.clone(),
                r.check.clone(),
                r.pass,
                r.detail.clone(),
                r.fix.clone(),
            ))
        })
        .collect();

    assert_eq!(
        actual.len(),
        expected.len(),
        "记录条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致");
    }
}

#[test]
fn l1_l2_records_match_python_with_live_gateway_stub() {
    // 死端口只能覆盖"health 早退"一条分支；这里起一个**极小 HTTP 桩**，让
    // health 200 / whoami 的 scope 判读 / 桥自健康 三条成功路径也进跨语言比对。
    // 桩不校验签名（两侧时间戳必然不同），只回固定 JSON ⇒ 两侧都该得到同样判读。
    let port = serve_stub();
    let tmp = TempDir::new("l1l2-live");
    let (aimail_home, user_home) = build_fixture_with(&tmp, port);
    let base = format!("http://127.0.0.1:{port}");

    let Some(py_checks) = python_check_json(&aimail_home, &user_home, "s1") else {
        println!("SKIP: python3 不可用");
        return;
    };
    let expected: Vec<Record> = filter_records(&py_checks, WANTED)
        .iter()
        .map(normalize_record)
        .collect();
    assert!(
        expected.len() >= 8,
        "活网关用例记录太少（{} 条）：{expected:#?}",
        expected.len()
    );

    let ctx = Ctx {
        aimail_home,
        user_home,
    };
    let mut c = Check::new();
    l1::gateway(&mut c, "s1", &ctx);
    l2::bridge(&mut c, "s1", &ctx);
    let actual: Vec<Record> = c
        .checks
        .iter()
        .filter(|r| WANTED.iter().any(|(l, k)| *l == r.level && *k == r.check))
        .map(|r| {
            normalize_record(&(
                r.level.clone(),
                r.check.clone(),
                r.pass,
                r.detail.clone(),
                r.fix.clone(),
            ))
        })
        .collect();

    // 关键分支必须真的走到了（否则这条测试会退化成"只比了早退路径"）
    let detail = |name: &str| -> String {
        actual
            .iter()
            .find(|r| r.1 == name)
            .map(|r| r.3.clone())
            .unwrap_or_default()
    };
    assert_eq!(
        detail("health"),
        "HTTP 200, uptime 42s".to_string(),
        "{actual:#?}"
    );
    assert!(
        detail("api_key").starts_with("scope=platform"),
        "{actual:#?}"
    );

    assert_eq!(
        actual.len(),
        expected.len(),
        "条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致\n（活网关 {base}）");
    }
}

/// 极小 HTTP 桩：`/health` ⇒ 200 带 uptime；`/api/v1/whoami` ⇒ 200 带 scope；
/// 其余 ⇒ 404（含 `/api/v1/admin/pending`）。返回端口。
fn serve_stub() -> u16 {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    let listener = TcpListener::bind("127.0.0.1:0").expect("bind stub");
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut s) = stream else { continue };
            let mut buf = [0u8; 4096];
            let n = s.read(&mut buf).unwrap_or(0);
            let head = String::from_utf8_lossy(&buf[..n]).into_owned();
            let path = head.split_whitespace().nth(1).unwrap_or("").to_string();
            let (code, body) = if path == "/health" {
                (200u16, r#"{"uptime_secs":42}"#.to_string())
            } else if path == "/api/v1/whoami" {
                (
                    200,
                    r#"{"scope":"platform","category":"agent_admin","system_id":"s1"}"#.to_string(),
                )
            } else {
                (404, r#"{"error":"not found"}"#.to_string())
            };
            let resp = format!(
                "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\n\
                 Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = s.write_all(resp.as_bytes());
            let _ = s.flush();
        }
    });
    port
}

#[test]
fn bridge_absent_is_reported_identically_as_not_deployed() {
    // 无 bridge 配置文件 ⇒ 只产一条"未部署"记录（合法，不算失败）
    let tmp = TempDir::new("l1l2-nobridge");
    let (aimail_home, user_home) = build_fixture(&tmp);
    std::fs::remove_dir_all(aimail_home.join("bridge")).unwrap();

    let Some(py_checks) = python_check_json(&aimail_home, &user_home, "s1") else {
        println!("SKIP: python3 不可用");
        return;
    };
    let expected = filter_records(&py_checks, &[("bridge", "config")]);
    assert_eq!(
        expected,
        vec![(
            "bridge".to_string(),
            "config".to_string(),
            true,
            "not deployed (gateway → agent-gateway direct)".to_string(),
            String::new(),
        )]
    );

    let ctx = Ctx {
        aimail_home,
        user_home,
    };
    let mut c = Check::new();
    l2::bridge(&mut c, "s1", &ctx);
    let actual: Vec<Value> = c
        .checks
        .iter()
        .map(|r| {
            serde_json::json!({
                "level": r.level, "check": r.check, "pass": r.pass,
                "detail": r.detail, "fix": r.fix,
            })
        })
        .collect();
    assert_eq!(actual.len(), 1, "{actual:?}");
    assert_eq!(common::tuple_of(&actual[0]), expected[0]);
}
