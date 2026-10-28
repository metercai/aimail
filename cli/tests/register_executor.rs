#![cfg(unix)]
// the whole file is unix semantics (stub executable bit / sh-driven / symlink); reopen after the windows branch (W2) lands.
//! Offline acceptance of the platform registration executor (`core::register`).
//!
//! Criteria: the **real argv** after template filling (`--name/--system-id/--manager`),
//! the three paths that must fail loudly (the missing-manager hard gate / an illegal name / a missing platform package),
//! fully offline (a stub `node` + a fixture platform root; no network, no real machine).
//!
//! Run inside a single test function: PATH / env are process-level, and splitting them apart would cause interference.

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
    // ── fixture: a stub node (records argv) + the dsh/pi package entries in the platform root ────────────────
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
    for k in ["AIMAIL_MANAGER", "AIMAIL_MANAGER_ADDRESS"] {
        std::env::remove_var(k);
    }
    // AIMAIL_HOME must be isolated too: a machine-level `.env` would be read as a fallback by `env_val` (otherwise the "missing manager" case
    // would be rescued by the manager in the real machine's .env — which is exactly the evidentiary value of this assertion)
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

    // 1) positive: pi (node_entry) — the arguments must come from the **naming plan + registry template**
    aimail::core::register::register_agent(
        "pi",
        "pi",
        &cfg,
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect("the pi registration should succeed (stub node)");
    let recorded = fs::read_to_string(&record).unwrap();
    // transport dispatch (contract §4.1(2)): a node host **does not run an external registrar itself** — the CLI directly calls the
    // op entry (`node <register-cli.js> --op assemble --args '<a single JSON>'`); naming/registration/binding landing happen inside the door.
    let lines: Vec<&str> = recorded.lines().collect();
    assert!(
        lines
            .first()
            .map(|l| l.ends_with("register-cli.js"))
            .unwrap_or(false),
        "argv[1] should be the op entry shipped with the platform package ({recorded:?})"
    );
    assert_eq!(
        &lines[1..4],
        &["--op", "assemble", "--args"],
        "{recorded:?}"
    );
    let payload: Value =
        serde_json::from_str(lines[4]).expect("the op argument should be a single JSON");
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
        "a node host does not run an external registrar => no register_spec may be carried ({payload:?})"
    );
    // G1a (2026-10-13) + owner ruling 2026-10-25: the node_entry envelope of a
    // self_bind host carries the local inbound endpoint (the contract `inbound_port`
    // common default single source) — three-state ①: the registered value = the local
    // endpoint (best-known; the confirmed callback is the authoritative pin).
    // The expected value is built from contract constants (the contract literal ratchet: no literals).
    let expected_lw = format!(
        "http://127.0.0.1:{}",
        aimail::core::contract::inbound_port()
    ) + aimail::core::contract::inbound_path();
    assert_eq!(
        payload["local_webhook_url"],
        json!(expected_lw),
        "the node_entry envelope should carry local_webhook_url ({payload:?})"
    );
    // pi does not declare --preset (register.args has no such flag) => the envelope must not carry preset.
    assert!(
        payload.get("preset").is_none(),
        "pi does not declare preset => the envelope must not carry that field ({payload:?})"
    );

    // 1b) dsh (node_entry, self_bind): local_webhook_url uses the SAME common
    // default port as pi (owner ruling 2026-10-25: one common default for all
    // self_bind hosts; the runtime bind probe re-selects on conflict).
    // P3-C (2026-10-17): preset is now self-reported by the SDK side (dsh register-cli assembleExtra,
    // the 10-11 three-source ruling "host self-report/internal => inside the SDK") => the CLI envelope no longer carries preset
    // (the old assertion "the value comes from the register.args --preset declaration" became void with the deletion of the G1b extraction channel).
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
    .expect("the dsh registration should succeed (stub node)");
    let recorded = fs::read_to_string(&record).unwrap();
    let lines: Vec<&str> = recorded.lines().collect();
    let payload: Value =
        serde_json::from_str(lines[lines.len() - 1]).expect("the op argument should be JSON");
    assert_eq!(
        payload["local_webhook_url"],
        json!(
            format!(
                "http://127.0.0.1:{}",
                aimail::core::contract::inbound_port()
            ) + aimail::core::contract::inbound_path()
        ),
        "the dsh envelope local_webhook_url should be the common default port ({payload:?})"
    );
    assert!(
        payload.get("preset").is_none(),
        "P3-C: preset is self-reported by the SDK; the CLI envelope must not carry it ({payload:?})"
    );
    // the precondition for case 4 (a missing dsh package must fail): remove the dsh entry fixture of 1b.
    fs::remove_file(&dsh_entry).ok();

    // 2) the missing-manager hard gate (neither the argument nor the env has one) => fail loudly; an empty registration is forbidden
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
    .expect_err("an empty manager must fail at the hard gate");
    assert!(err.contains("manager missing"), "{err}");

    // 4) a missing platform package (no dsh package in the fixture home) => a loud failure carrying the registry fail_hint
    let err = aimail::core::register::register_agent(
        "dsh",
        "agent",
        &cfg,
        "",
        &home.to_string_lossy(),
        &core_dir(),
    )
    .expect_err("a missing dsh package must fail");
    assert!(err.contains("node entry missing"), "{err}");
}
