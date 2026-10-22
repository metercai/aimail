#![cfg(unix)]
// 整文件 unix 语义(stub 可执行位 / sh 驱动 / symlink), windows 分支(W2)落地后重开。
//! 平台注册执行器（`core::register`）的离线验收。
//!
//! 判据：模板填充后的**真实 argv**（`--name/--system-id/--manager`）、
//! 三条必须响亮失败的路径（缺 manager 硬门 / 定名非法 / 平台包缺失），
//! 全程离线（stub `node` + 夹具平台根，不触网、不碰真机）。
//!
//! 放在同一个测试函数里跑：PATH / env 是进程级的，拆开会互相干扰。

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

fn core_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .join("pysdk")
}

fn write_exec(p: &Path, body: &str) {
    fs::create_dir_all(p.parent().unwrap()).unwrap();
    fs::write(p, body).unwrap();
    let mut perm = fs::metadata(p).unwrap().permissions();
    perm.set_mode(0o755);
    fs::set_permissions(p, perm).unwrap();
}

#[test]
fn register_executor_offline_behaviour() {
    // ── 夹具：stub node（记录 argv）+ 平台根里的 dsh/pi 包入口 ────────────────
    let tmp = tempfile::tempdir().unwrap();
    let bin = tmp.path().join("bin");
    let record = tmp.path().join("argv.txt");
    write_exec(
        &bin.join("node"),
        &format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> {}\nprintf '%s\\n' '{{\"ok\":true,\"result\":{{\"email\":\"agent@example.test\"}}}}'\n",
            record.display()
        ),
    );
    let old_path = std::env::var("PATH").unwrap_or_default();
    std::env::set_var("PATH", format!("{}:{}", bin.display(), old_path));
    for k in [
        "AIMAIL_MANAGER",
        "AIMAIL_MANAGER_ADDRESS",
        "INTEGRATE_MANAGER_ADDRESS",
    ] {
        std::env::remove_var(k);
    }
    // AIMAIL_HOME 也必须隔离：机器级 `.env` 会被 `env_val` 兜底读到（否则"缺 manager"用例
    // 会被真机 .env 里的 manager 兜住 —— 这正是本条断言的取证价值）
    let ahome = tmp.path().join("ahome");
    fs::create_dir_all(&ahome).unwrap();
    std::env::set_var("AIMAIL_HOME", &ahome);

    let home = tmp.path().join("home");
    let node_entry = home.join("agent/npm/node_modules/agent-aimail/dist/pi/register-cli.js");
    fs::create_dir_all(node_entry.parent().unwrap()).unwrap();
    fs::write(&node_entry, "// stub\n").unwrap();
    let cfg = json!({
        "system_id": "s1",
        "domain": "example.test",
        "system_name": "",
        "system_home": home.to_string_lossy(),
        "manager_address": "mgr@example.test",
        "gateway_url": "http://127.0.0.1:1",
        "admin_key": "k",
    });

    // 1) 正向：pi（node_entry）—— 参数必须来自**定名计划 + 注册表模板**
    aimail::core::register::register_agent(
        "pi",
        "pi",
        &cfg,
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect("pi 注册应成功（stub node）");
    let recorded = fs::read_to_string(&record).unwrap();
    // transport 分派（契约 §4.1(2)）：node 宿主**不自跑外部注册器** —— CLI 直接调平台包自带的
    // op 入口（`node <register-cli.js> --op assemble --args '<单个 JSON>'`），定名/注册/落绑定在门内。
    let lines: Vec<&str> = recorded.lines().collect();
    assert!(
        lines
            .first()
            .map(|l| l.ends_with("register-cli.js"))
            .unwrap_or(false),
        "argv[1] 应是平台包自带的 op 入口（{recorded:?}）"
    );
    assert_eq!(
        &lines[1..4],
        &["--op", "assemble", "--args"],
        "{recorded:?}"
    );
    let payload: Value = serde_json::from_str(lines[4]).expect("op 入参应为一个 JSON");
    assert_eq!(payload["system_id"], json!("s1"), "{payload:?}");
    assert_eq!(
        payload["manager_address"],
        json!("mgr@example.test"),
        "{payload:?}"
    );
    assert_eq!(
        payload["home"],
        json!(home.to_string_lossy()),
        "{payload:?}"
    );
    assert!(
        payload.get("register_spec").is_none(),
        "node 宿主不自跑外部注册器 ⇒ 不得带 register_spec（{payload:?}）"
    );
    // G1a（2026-10-13）：node_entry 信封携带本机入站端点（契约 inbound_ports 单真源）
    // —— 三态①：注册值 = 本机端点。期望值从契约常量构造（契约字面量棘轮：禁字面量）。
    let expected_lw = format!(
        "http://127.0.0.1:{}",
        aimail::core::contract::inbound_port("pi").expect("契约应有 pi 入站端口")
    ) + aimail::core::contract::inbound_path();
    assert_eq!(
        payload["local_webhook_url"],
        json!(expected_lw),
        "node_entry 信封应携带 local_webhook_url（{payload:?}）"
    );
    // pi 未声明 --preset（register.args 无该 flag）⇒ 信封不得带 preset。
    assert!(
        payload.get("preset").is_none(),
        "pi 未声明 preset ⇒ 信封不得带该字段（{payload:?}）"
    );

    // 1b) dsh（node_entry）：local_webhook_url 用 dsh 契约端口。
    // P3-C(2026-10-17):preset 改由 SDK 侧自报(dsh register-cli assembleExtra,
    // 10-11 三来源裁决「宿主自报/内部 → SDK 内部」)⇒ CLI 信封不再携带 preset
    // (旧断言「值来自 register.args --preset 声明」随 G1b 提取通道删除而失效)。
    let dsh_entry = home.join("profiles/agent/node_modules/agent-aimail/dist/dsh/register-cli.js");
    fs::create_dir_all(dsh_entry.parent().unwrap()).unwrap();
    fs::write(&dsh_entry, "// stub\n").unwrap();
    aimail::core::register::register_agent(
        "dsh",
        "agent",
        &cfg,
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect("dsh 注册应成功（stub node）");
    let recorded = fs::read_to_string(&record).unwrap();
    let lines: Vec<&str> = recorded.lines().collect();
    let payload: Value = serde_json::from_str(lines[lines.len() - 1]).expect("op 入参应为 JSON");
    assert_eq!(
        payload["local_webhook_url"],
        json!(
            format!(
                "http://127.0.0.1:{}",
                aimail::core::contract::inbound_port("dsh").expect("契约应有 dsh 入站端口")
            ) + aimail::core::contract::inbound_path()
        ),
        "dsh 信封 local_webhook_url 应为 dsh 契约端口（{payload:?}）"
    );
    assert!(
        payload.get("preset").is_none(),
        "P3-C: preset 由 SDK 自报, CLI 信封不得携带（{payload:?}）"
    );
    // 用例 4（dsh 包缺失必须失败）的前提：删掉 1b 的 dsh 入口夹具。
    fs::remove_file(&dsh_entry).ok();

    // 2) 缺 manager 硬门（参数/env 均无）⇒ 响亮失败，禁以空注册
    let cfg_nomgr = json!({
        "system_id": "s1",
        "domain": "example.test",
        "system_home": home.to_string_lossy(),
    });
    let err = aimail::core::register::register_agent(
        "pi",
        "pi",
        &cfg_nomgr,
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect_err("空 manager 必须硬门失败");
    assert!(err.contains("缺 manager"), "{err}");

    // 4) 平台包缺失（夹具 home 里没有 dsh 包）⇒ 带注册表 fail_hint 的响亮失败
    let err = aimail::core::register::register_agent(
        "dsh",
        "agent",
        &cfg,
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect_err("dsh 包缺失必须失败");
    assert!(err.contains("node 入口缺失"), "{err}");
}
