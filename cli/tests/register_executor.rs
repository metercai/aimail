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

use serde_json::json;

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
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> {}\nexit 0\n",
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
    let node_entry = home.join("agent/npm/node_modules/pi-aimail/dist/register-cli.js");
    fs::create_dir_all(node_entry.parent().unwrap()).unwrap();
    fs::write(&node_entry, "// stub\n").unwrap();
    let cfg = json!({
        "system_id": "s1",
        "domain": "example.test",
        "system_name": "",
        "system_home": home.to_string_lossy(),
        "manager_address": "mgr@example.test",
        "admin_key": "k",
    });

    // 1) 正向：pi（node_entry）—— 参数必须来自**定名计划 + 注册表模板**
    aimail::core::register::register_agent(
        "pi",
        "pi",
        &cfg,
        "billing",
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect("pi 注册应成功（stub node）");
    let recorded = fs::read_to_string(&record).unwrap();
    assert!(
        recorded.contains("--name\nbilling\n"),
        "目标基名必须直达（{recorded:?}）"
    );
    assert!(recorded.contains("--system-id\ns1\n"), "{recorded:?}");
    assert!(
        recorded.contains("--manager\nmgr@example.test\n") || recorded.contains("mgr@example.test"),
        "manager 必须落到 argv（{recorded:?}）"
    );

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
        "billing",
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect_err("空 manager 必须硬门失败");
    assert!(err.contains("缺 manager"), "{err}");

    // 3) 定名非法 ⇒ SDK 的 ValueError 消息原样透出（CLI 不臆断）
    let err = aimail::core::register::register_agent(
        "pi",
        "pi",
        &cfg,
        "has.dot",
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect_err("非法名必须失败");
    assert!(
        err.contains("非法地址名") || err.contains("invalid address name"),
        "SDK 的定名校验消息必须原样透出: {err}"
    );

    // 4) 平台包缺失（夹具 home 里没有 dsh 包）⇒ 带注册表 fail_hint 的响亮失败
    let err = aimail::core::register::register_agent(
        "dsh",
        "agent",
        &cfg,
        "agent",
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect_err("dsh 包缺失必须失败");
    assert!(err.contains("node 入口缺失"), "{err}");
}
