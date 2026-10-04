//! `install_steps` 执行器（`core::steps`）的离线验收。
//!
//! 用**合成步骤表**驱动（执行器本身接受外部表 ⇒ 可测；`run_install_steps` 只是读注册表的薄壳），
//! 判据：`when` 门 · `spawn` 的 `skip_if`/`on_missing`/`on_error` 三态 · 未知 kind 只 warn ·
//! **manager 硬门**在任何异常兜底之前生效（空即 Err，禁以空注册/写白名单）。

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

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

fn ctx_with(cfg: Value) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("home".into(), json!("/tmp/nonexistent-aimail-test"));
    m.insert("sid".into(), json!("s1"));
    m.insert("manager".into(), json!(""));
    m.insert("cfg".into(), cfg);
    m
}

#[test]
fn step_executor_offline_behaviour() {
    let tmp = tempfile::tempdir().unwrap();
    let bin = tmp.path().join("bin");
    let rec = tmp.path().join("rec.txt");
    write_exec(
        &bin.join("ok.sh"),
        &format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" >> {}\nexit 0\n",
            rec.display()
        ),
    );
    write_exec(&bin.join("fail.sh"), "#!/bin/sh\nexit 7\n");
    let old_path = std::env::var("PATH").unwrap_or_default();
    std::env::set_var("PATH", format!("{}:{}", bin.display(), old_path));
    for k in [
        "AIMAIL_MANAGER",
        "AIMAIL_MANAGER_ADDRESS",
        "INTEGRATE_MANAGER_ADDRESS",
    ] {
        std::env::remove_var(k);
    }
    let ahome = tmp.path().join("ahome");
    fs::create_dir_all(&ahome).unwrap();
    std::env::set_var("AIMAIL_HOME", &ahome);

    // 1) when 门：path_exists 不成立 ⇒ 该步被跳过（不执行）
    let cfg = json!({"gateway_url": "http://127.0.0.1:9", "admin_key": "k",
                     "system_id": "s1", "system_home": tmp.path().to_string_lossy()});
    let steps = vec![
        json!({"kind": "spawn",
               "when": {"path_exists": "/tmp/definitely-missing-aimail-x"},
               "argv": [bin.join("ok.sh").to_string_lossy(), "should-not-run"]}),
        json!({"kind": "spawn",
               "argv": [bin.join("ok.sh").to_string_lossy(), "--name", "{sid}"],
               "ok_text": "spawned ok"}),
    ];
    let mut ctx = ctx_with(cfg.clone());
    aimail::core::steps::run_steps("openclaw", &steps, &mut ctx, &core_dir()).expect("应成功");
    let recorded = fs::read_to_string(&rec).unwrap();
    assert!(
        recorded.contains("--name\ns1\n"),
        "模板填充 + 执行: {recorded:?}"
    );
    assert!(
        !recorded.contains("should-not-run"),
        "when 不成立必须跳过: {recorded:?}"
    );

    // 2) skip_if 探针说"已在场" ⇒ 跳过（幂等）
    fs::write(&rec, "").unwrap();
    let steps = vec![json!({
        "kind": "spawn",
        "argv": [bin.join("ok.sh").to_string_lossy(), "ensure-plugin"],
        "skip_if": {"argv": ["/bin/sh", "-c", "echo PRESENT"], "match": "present"}
    })];
    let mut ctx = ctx_with(cfg.clone());
    aimail::core::steps::run_steps("openclaw", &steps, &mut ctx, &core_dir()).expect("应成功");
    assert!(
        fs::read_to_string(&rec).unwrap().is_empty(),
        "探针说已在场 ⇒ 不该再跑安装"
    );

    // 3) on_error=warn（默认）⇒ 只告警不中断；on_error=fail ⇒ 中断
    let warn_step = vec![json!({"kind": "spawn", "argv": [bin.join("fail.sh").to_string_lossy()]})];
    let mut ctx = ctx_with(cfg.clone());
    assert!(aimail::core::steps::run_steps("openclaw", &warn_step, &mut ctx, &core_dir()).is_ok());
    let fail_step = vec![
        json!({"kind": "spawn", "argv": [bin.join("fail.sh").to_string_lossy()],
                                "on_error": "fail", "warn_hint": "插件安装失败"}),
    ];
    let mut ctx = ctx_with(cfg.clone());
    let err = aimail::core::steps::run_steps("openclaw", &fail_step, &mut ctx, &core_dir())
        .expect_err("on_error=fail 必须中断");
    assert!(err.contains("插件安装失败"), "{err}");

    // 4) on_missing=fail 与 skip 两态
    let missing_fail = vec![json!({"kind": "spawn", "argv": ["/tmp/no-such-bin-aimail"],
                                   "fail_hint": "`no-such-bin` 不可执行"})];
    let mut ctx = ctx_with(cfg.clone());
    let err = aimail::core::steps::run_steps("openclaw", &missing_fail, &mut ctx, &core_dir())
        .expect_err("默认 on_missing=fail");
    assert!(err.contains("不可执行"), "{err}");
    let missing_skip = vec![json!({"kind": "spawn", "argv": ["/tmp/no-such-bin-aimail"],
                                   "on_missing": "skip", "warn_hint": "缺 `no-such-bin`,跳过"})];
    let mut ctx = ctx_with(cfg.clone());
    assert!(
        aimail::core::steps::run_steps("openclaw", &missing_skip, &mut ctx, &core_dir()).is_ok()
    );

    // 5) manager 硬门：空 manager 时 sdk_install（needs_manager 默认 true）必须先失败，
    //    且**不受** on_error=warn 兜底影响（F9 教训：异常兜底吞不掉基本契约）
    let sdk_step = vec![
        json!({"kind": "sdk_install", "target": "hermes", "fn": "install_hermes",
                               "on_error": "warn"}),
    ];
    let mut ctx = ctx_with(cfg.clone());
    let err = aimail::core::steps::run_steps("hermes", &sdk_step, &mut ctx, &core_dir())
        .expect_err("空 manager 必须硬门失败");
    assert!(err.contains("缺 manager"), "{err}");

    // 6) needs_manager:false（纯装配步）不受门限制；未知 kind 只 warn 不中断
    let pure_step = vec![
        json!({"kind": "sdk_install", "target": "dsh", "fn": "no_such_entry_aimail",
                                "needs_manager": false, "on_error": "warn"}),
    ];
    let mut ctx = ctx_with(cfg);
    assert!(aimail::core::steps::run_steps("dsh", &pure_step, &mut ctx, &core_dir()).is_ok());
    let unknown = vec![json!({"kind": "no_such_kind"})];
    let mut ctx = Map::new();
    assert!(aimail::core::steps::run_steps("dsh", &unknown, &mut ctx, &core_dir()).is_ok());
    let _ = Value::Null;
}
