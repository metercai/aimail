//! Acceptance of the `repair` command surface (the read-only surface: `-n/--dry-run` and help), **without touching the real disk** (repair is a write surface; the real run is left to the CLI L2).
//!
//! Criteria: ① the four help switches are verbatim with Python (`-s/-H/-D/-n`); ② `-n` => print the plan, then rc=0 (no write step is executed);
//! ③ `repair` is recognized as **implemented** (it no longer lands on `not yet ported`).

use std::path::PathBuf;
use std::process::Command;

fn exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_aimail"))
}

fn run(home: &std::path::Path, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(exe())
        .args(args)
        .env("AIMAIL_HOME", home)
        .env("AGENT_HOME", home)
        .env("AIMAIL_PROG_DIR", home)
        .output()
        .expect("spawn");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

#[test]
fn repair_dry_run_prints_plan_and_help_matches_python() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("systems")).unwrap();

    // ① help: the four switches
    let (rc, out, _) = run(&home, &["repair", "--help"]);
    assert_eq!(rc, 0, "help rc: {out}");
    for flag in ["--system-id", "--home", "--deep", "--dry-run"] {
        assert!(out.contains(flag), "the switch {flag} is missing: {out}");
    }

    // ② `-n`: print the plan, rc=0, **do not land on not yet ported**
    let (rc, out, err) = run(&home, &["repair", "-n"]);
    assert!(
        !out.contains("not yet ported") && !err.contains("not yet ported"),
        "repair is still treated as unported: {out} / {err}"
    );
    assert_eq!(
        rc, 0,
        "dry-run should be rc=0 (no write step is executed): out={out} err={err}"
    );
    assert!(
        out.contains("[dry-run] plan:"),
        "dry-run did not print the plan: {out}"
    );
    // the plan must show the ladder steps (using stable identifiers, to avoid coupling with the wording)
    for step in ["bridge", "route", "webhook"] {
        assert!(
            out.to_lowercase().contains(step),
            "the plan is missing {step}: {out}"
        );
    }
}
