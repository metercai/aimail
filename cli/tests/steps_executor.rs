#![cfg(unix)]
// Whole file unix semantics (stub executable bit / sh driver / symlink), windows branch (W2) to be reopened after landing.
//! Offline acceptance for `install_steps` executor (`core::steps`).
//!
//! Driven by **synthetic step table** (executor itself accepts external table ⇒ testable; `run_install_steps` is just a thin shell reading registry),
//! Criteria: `when` gate · `spawn`'s `skip_if`/`on_missing`/`on_error` three states · unknown kind only warns ·
//! **manager hard gate** takes effect before any exception fallback (empty is Err, forbidden to register empty/write whitelist).

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

    // 1) when gate: path_exists not true ⇒ step skipped (not executed)
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
    aimail::core::steps::run_steps("openclaw", &steps, &mut ctx, &core_dir()).expect("should succeed");
    let recorded = fs::read_to_string(&rec).unwrap();
    assert!(
        recorded.contains("--name\ns1\n"),
        "Template filling + execution: {recorded:?}"
    );
    assert!(
        !recorded.contains("should-not-run"),
        "when not true must skip: {recorded:?}"
    );

    // 2) skip_if probe says "already present" ⇒ skip (idempotent)
    fs::write(&rec, "").unwrap();
    let steps = vec![json!({
        "kind": "spawn",
        "argv": [bin.join("ok.sh").to_string_lossy(), "ensure-plugin"],
        "skip_if": {"argv": ["/bin/sh", "-c", "echo PRESENT"], "match": "present"}
    })];
    let mut ctx = ctx_with(cfg.clone());
    aimail::core::steps::run_steps("openclaw", &steps, &mut ctx, &core_dir()).expect("should succeed");
    assert!(
        fs::read_to_string(&rec).unwrap().is_empty(),
        "Probe says already present ⇒ should not run install again"
    );

    // 3) on_error=warn (default) ⇒ only warn not interrupt; on_error=fail ⇒ interrupt
    let warn_step = vec![json!({"kind": "spawn", "argv": [bin.join("fail.sh").to_string_lossy()]})];
    let mut ctx = ctx_with(cfg.clone());
    assert!(aimail::core::steps::run_steps("openclaw", &warn_step, &mut ctx, &core_dir()).is_ok());
    let fail_step = vec![
        json!({"kind": "spawn", "argv": [bin.join("fail.sh").to_string_lossy()],
                                "on_error": "fail", "warn_hint": "plugin install failed"}),
    ];
    let mut ctx = ctx_with(cfg.clone());
    let err = aimail::core::steps::run_steps("openclaw", &fail_step, &mut ctx, &core_dir())
        .expect_err("on_error=fail must interrupt");
    assert!(err.contains("plugin install failed"), "{err}");

    // 4) on_missing=fail and skip two states
    let missing_fail = vec![json!({"kind": "spawn", "argv": ["/tmp/no-such-bin-aimail"],
                                   "fail_hint": "`no-such-bin` is not executable"})];
    let mut ctx = ctx_with(cfg.clone());
    let err = aimail::core::steps::run_steps("openclaw", &missing_fail, &mut ctx, &core_dir())
        .expect_err("default on_missing=fail");
    assert!(err.contains("not executable"), "{err}");
    let missing_skip = vec![json!({"kind": "spawn", "argv": ["/tmp/no-such-bin-aimail"],
                                   "on_missing": "skip", "warn_hint": "`no-such-bin` missing, skipped"})];
    let mut ctx = ctx_with(cfg.clone());
    assert!(
        aimail::core::steps::run_steps("openclaw", &missing_skip, &mut ctx, &core_dir()).is_ok()
    );

    // 5) manager hard gate: when manager is empty, sdk_install (needs_manager defaults to true) must fail first,
    //    and is **not** affected by on_error=warn fallback (F9 lesson: exception fallback cannot swallow basic contracts)
    let sdk_step = vec![
        json!({"kind": "sdk_install", "target": "hermes", "fn": "install_hermes",
                               "on_error": "warn"}),
    ];
    let mut ctx = ctx_with(cfg.clone());
    let err = aimail::core::steps::run_steps("hermes", &sdk_step, &mut ctx, &core_dir())
        .expect_err("empty manager must hard gate fail");
    assert!(err.contains("manager missing"), "{err}");

    // 6) needs_manager:false (pure assembly step) is not subject to gate restrictions; unknown kind only warns and does not interrupt
    let pure_step = vec![
        json!({"kind": "sdk_install", "target": "dsh", "fn": "no_such_entry_aimail",
                                "needs_manager": false, "on_error": "warn"}),
    ];
    let mut ctx = ctx_with(cfg);
    assert!(aimail::core::steps::run_steps("dsh", &pure_step, &mut ctx, &core_dir()).is_ok());
    // 6b) unknown step kind ⇒ **hard fail** (owner 2026-10-18: reject half-install; old behavior was warn then skip)
    let unknown = vec![json!({"kind": "no_such_kind"})];
    let mut ctx = Map::new();
    let err = aimail::core::steps::run_steps("dsh", &unknown, &mut ctx, &core_dir())
        .expect_err("unknown kind must hard fail");
    assert!(err.contains("unknown kind 'no_such_kind'"), "{err}");
    let _ = Value::Null;
}
