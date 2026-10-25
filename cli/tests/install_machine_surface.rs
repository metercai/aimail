//! `install` machine-side gate: dispatch / reject args / human path criteria.
//!
//! Machine-side only has `--system-only` (SDK reverse call ABI); `--payload` channel has been retired and deleted with payload mechanism
//! (self-contained payload replaced by owner 2026-10-06's "zero-copy, use installed package", no callers left in repo).

use std::path::PathBuf;
use std::process::Command;

fn bin() -> PathBuf {
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

fn run(prog_dir: &str, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(bin())
        .args(args)
        .env("AIMAIL_PROG_DIR", prog_dir)
        .env("AIMAIL_HOME", format!("{prog_dir}/home"))
        .output()
        .expect("rust binary executable");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn machine_channels_reject_the_other_side_and_exit_2() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    let cases: &[(&[&str], &str)] = &[
        (
            &["install", "--system-only", "--all-agents"],
            "ERROR: the --system-only channel does not accept --all-agents",
        ),
        (
            &["install", "--system-only", "-a", "web"],
            "ERROR: the --system-only channel does not accept -a",
        ),
        (
            &["install", "--home", "/tmp", "--all-agents", "-a", "web"],
            "ERROR: --all-agents is mutually exclusive with -a/--agent",
        ),
    ];
    for (args, msg) in cases {
        let (rc, out, err) = run(&prog, args);
        assert_eq!(rc, 2, "{args:?} must exit 2 (reject args, no silent pass)");
        assert!(
            err.contains(msg),
            "{args:?} stderr should contain {msg:?}, actual {err:?}"
        );
        assert!(
            out.is_empty(),
            "{args:?} stdout should be empty when rejecting args"
        );
    }
}

#[test]
fn install_surfaces_behave_like_python() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    {
        // Human path **implemented**: without --home/--system-id, fail loudly per Python spec (message to stdout, rc=1)
        let args = vec!["install"];
        let (rc, out, _err) = run(&prog, &args);
        assert_eq!(rc, 1, "{args:?} should rc=1");
        assert!(
            out.contains("install requires --home"),
            "{args:?} should report missing params: out={out:?}"
        );
    }
    // `--system-only` without credentials ⇒ **ABI error envelope** (single-line JSON, rc=1), not "not ported"
    let (rc, out, _e) = run(&prog, &["install", "--system-only"]);
    assert_eq!(rc, 1, "--system-only missing home/sid should rc=1");
    assert!(
        out.trim().starts_with(r#"{"success": false, "error": "#),
        "should output Python-style single-line JSON envelope: {out:?}"
    );
    assert_eq!(
        out.trim_end().lines().count(),
        1,
        "stdout must be exactly one line"
    );
    // Reached the active worker ⇒ implemented: when the gateway is unreachable, **continue and succeed** per Python semantics (warn only),
    // but must use `-g` to point to dead address to avoid network (default falls to production gateway).
    let home2 = d.path().join("h2");
    std::fs::create_dir_all(&home2).unwrap();
    let (rc2, out2, err2) = run(
        &prog,
        &[
            "install",
            "--system-only",
            "-H",
            &home2.to_string_lossy(),
            "-s",
            "s1",
            "-k",
            "k",
            "-g",
            "http://127.0.0.1:9",
        ],
    );
    assert_eq!(
        rc2, 0,
        "Gateway unreachable ⇒ fallback to system-level key and succeed (same as Python): {err2}"
    );
    assert!(
        out2.trim()
            .starts_with(r#"{"success": true, "system_id": "s1""#),
        "Single-line JSON success envelope: {out2:?}"
    );
    assert_eq!(out2.trim_end().lines().count(), 1, "Exactly one line");
}

/// Subprocess-level env injection (does not pollute test process, avoids process-level env race in parallel cases).
/// Also **clears AIMAIL_* business env**——the test process may inherit env exported by the dev machine/L3
/// AIMAIL_PRODUCT_CODE etc., causing "no credential" control to take product code path (env cross-talk, not product behavior).
fn run_env(prog_dir: &str, args: &[&str], extra: &[(&str, &str)]) -> (i32, String, String) {
    let mut cmd = Command::new(bin());
    cmd.args(args)
        .env("AIMAIL_PROG_DIR", prog_dir)
        .env("AIMAIL_HOME", format!("{prog_dir}/home"))
        // HOME points to the prog temp directory: isolates the dev machine's ~/.aimail/.env (HOME fallback for env_val
        // source, includes machine-level config like AIMAIL_PRODUCT_CODE, strace proves read at startup)
        .env("HOME", prog_dir);
    for (k, _) in std::env::vars() {
        // harness variable exemption (just set above), clear all other AIMAIL_* business env
        if k.starts_with("AIMAIL_") && k != "AIMAIL_PROG_DIR" && k != "AIMAIL_HOME" {
            cmd.env_remove(k);
        }
    }
    for (k, v) in extra {
        cmd.env(k, v);
    }
    let out = cmd.output().expect("rust binary executable");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn install_reads_agent_home_env_when_no_flag_or_sid() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    let agent_home = d.path().join("agent-home");
    std::fs::create_dir_all(&agent_home).unwrap();
    // C-7: AGENT_HOME + no --home/--system-id ⇒ home resolved by env (aligned with welcome identity chain).
    // Empty temp dir causes downstream resolve_platform to fail to determine platform ⇒ loud failure (rc=1), proving C-7 gate passed.
    let (rc, out, _err) = run_env(
        &prog,
        &["install"],
        &[("AGENT_HOME", agent_home.to_str().unwrap())],
    );
    assert_eq!(
        rc, 1,
        "Empty dir cannot determine platform ⇒ rc=1 (proves home resolution passed): {out:?}"
    );
    assert!(
        out.contains("--home resolved from the AGENT_HOME env var"),
        "Should show AGENT_HOME home resolution source: out={out:?}"
    );
    assert!(
        !out.contains("install requires --home"),
        "C-7: no longer report missing --home (home provided by AGENT_HOME): out={out:?}"
    );
}

#[test]
fn install_reads_admin_key_env_when_no_flag() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    let home = d.path().join("h");
    std::fs::create_dir_all(&home).unwrap();
    // C-1: no -k flag, AIMAIL_ADMIN_KEY env as last fallback (after reuse scan).
    // Dead gateway isolation C-1 variable: with env ⇒ error falls on gateway activation call (credentials assembled);
    // without env ⇒ no credential (credentials not assembled). Different = env fallback effective.
    let (rc, out, err) = run_env(
        &prog,
        &[
            "install",
            "--system-only",
            "-H",
            home.to_str().unwrap(),
            "-g",
            "http://127.0.0.1:9",
        ],
        &[("AIMAIL_ADMIN_KEY", "envkey1")],
    );
    assert_ne!(rc, 0, "Dead gateway must be non-zero");
    assert!(
        !out.contains("no credential to activate with"),
        "With env should not report missing credentials (credentials should be assembled): out={out:?}"
    );
    assert!(
        err.contains("admin_key path") || err.contains("admin key"),
        "Error should fall on admin_key activation path (credentials assembled from env, passed missing credential gate): err={err:?}"
    );
    // Contrast: same args without env ⇒ loudly reports missing credentials (rc=1). **Independent empty home**——before the activation failure with env
    // writing pointer in this home, reusing same dir would make control take reuse branch (read sid) instead of missing credentials.
    let home2 = d.path().join("h2");
    std::fs::create_dir_all(&home2).unwrap();
    let (rc2, out2, _) = run_env(
        &prog,
        &[
            "install",
            "--system-only",
            "-H",
            home2.to_str().unwrap(),
            "-g",
            "http://127.0.0.1:9",
        ],
        &[],
    );
    assert_eq!(rc2, 1, "No -k no env must be non-zero: {out2:?}");
    assert!(
        out2.contains("no credential to activate with"),
        "No credentials should report missing credentials: out={out2:?}"
    );
}

#[test]
fn welcome_auto_resolves_single_system_without_flag() {
    // C-6: welcome text (welcome.rs:59) promises "local single system can be auto-determined".
    // AIMAIL_HOME has exactly one valid system (systems/<sid>/aimail_gateway.json) when,
    // Welcome without --system-id should automatically resolve to that sid (bypassing the "system_id unresolved" gate).
    // Observation point: single system ⇒ lands on "Missing gateway_url" (fixture has empty gw_url);
    //        multiple systems ⇒ still reports "system_id unresolved" (does not guess).
    // AGENT_HOME set to empty, excluding inherited platform pointer interference (only looking at single system determination).
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    let sysdir = d.path().join("home").join("systems").join("solo");
    std::fs::create_dir_all(&sysdir).unwrap();
    std::fs::write(
        sysdir.join("aimail_gateway.json"),
        r#"{"system_id":"solo","gateway_url":"","admin_key":""}"#,
    )
    .unwrap();
    let (rc, out, _err) = run_env(&prog, &["welcome"], &[("AGENT_HOME", "")]);
    assert_eq!(
        rc, 1,
        "fixture empty gw_url ⇒ rc=1 (passed sid gate): {out:?}"
    );
    assert!(
        out.contains("Missing gateway_url"),
        "Single system should auto-resolve to solo (passed unresolved gate, landed on missing gw_url): out={out:?}"
    );
    assert!(
        !out.contains("system_id unresolved"),
        "C-6: Single-system machine should no longer report system_id unresolved (should auto-resolve): out={out:?}"
    );
    // Control: two systems ⇒ do not guess, loudly report "system_id unresolved"
    let d2 = tempfile::tempdir().unwrap();
    let prog2 = d2.path().to_string_lossy().to_string();
    for sid in ["a", "b"] {
        let sd = d2.path().join("home").join("systems").join(sid);
        std::fs::create_dir_all(&sd).unwrap();
        std::fs::write(
            sd.join("aimail_gateway.json"),
            format!(r#"{{"system_id":"{sid}","gateway_url":"","admin_key":""}}"#),
        )
        .unwrap();
    }
    let (rc2, out2, _e2) = run_env(&prog2, &["welcome"], &[("AGENT_HOME", "")]);
    assert_eq!(
        rc2, 1,
        "Multiple systems must not guess, must be non-zero: {out2:?}"
    );
    assert!(
        out2.contains("system_id unresolved"),
        "Multiple systems should loudly report system_id unresolved (do not guess): out={out2:?}"
    );
}
