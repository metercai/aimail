//! The Rust CLI surface contract (plan v3 S1 acceptance, extended from S3).
//!
//! Lock in five things:
//! 1. the `version` output shape == `aimail <ver> (<dev|bootstrapped>)`;
//! 2. all 15 top-level subcommands + the 5 `prompt` nested ones are registered (the command surface is frozen);
//! 3. the machine surface (`--system-only` / `--payload` / the old name `ensure-system`) does **not** appear in the human help;
//! 4. **unported commands must be honest**: a subcommand not listed in `IMPLEMENTED` always gets rc≠0 and stderr containing
//!    "not yet ported" (never a silent success); conversely, an implemented command must not be left in the unported list
//!    (forced maintenance, to prevent "ported the code but forgot the list");
//! 5. every real run happens inside a **hermetic HOME** (a temp dir), never reading the dev machine's `~/.aimail`.
//!
//! Item 5 is a real problem exposed when S3 added `stats`: the old `run()` used the process environment directly, and `stats`
//! once implemented printed the dev machine's real systems (both a test-pollution source and a misjudgment source).

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

const TOP_LEVEL: &[&str] = &[
    "install",
    "uninstall",
    "reset",
    "stats",
    "renew",
    "version",
    "check",
    "repair",
    "ping",
    "welcome",
    "persona",
    "domain",
    "address",
    "prompt",
    "bridge",
    "agent",
];

const PROMPT_SUBCOMMANDS: &[&str] = &["add", "list", "rm", "test", "create-file"];

/// The machine-surface words (none of the three may appear in the human help; the same-named assertion on the Python side).
const MACHINE_LITERALS: &[&str] = &["ensure-system", "payload", "system-only"];

/// The subcommands ported so far (add one each time one is ported — a ratchet with `unported_commands_are_honest`).
/// Note the granularity = **subcommand**: the unported **surface** of a ported command (e.g. `address set-name`, `domain --add`)
/// is handled by each module's explicit `not_yet_ported`, and does not change the list here.
// implemented = the command surface is wired and the behavior verified (`reset`'s bridge route reconciliation still explicitly warns: "unported (bridge)",
// but the command body, the activation worker, and the registration chain are all real implementations => counted as an implemented surface)
const IMPLEMENTED: &[&str] = &[
    "version",
    "stats",
    "persona",
    "address",
    "domain",
    "check",
    "reset",
    // install: the machine surface (--system-only / --payload) and the human path (activate/reuse + platform wiring) are both wired;
    // the only two unported **bridge-related surfaces** (remote bridge deployment / route reconciliation) are explicitly warned in the command, not silently skipped.
    "install",
    "uninstall",
    // bridge: status/per-system re-refresh are wired; --restart/--upgrade (deployment surface) are **loudly unported** before P2 slice 3.
    "bridge",
    // renew: a read-only view + renewal (rust native HTTP). **A registered divergence**: after a Python parse failure it still takes an empty key
    // to hit the default gateway (prod) => this implementation prints the same wording, then rc=1 and sends no network.
    "renew",
    // ping: the identity chain + SMTP (rustls STARTTLS) + the three-phase log verdict. The real-machine path is in the CLI L2 gate.
    "ping",
    // welcome: API mode (default, admin key → /api/v1/system/welcome → poll for the reply → identity approval) +
    // SMTP mode (--smtp). Exit semantics 0/1/2 (no draft obtainable => 2, no pretending success). The real-machine path is in the CLI L2.
    "welcome",
    // prompt: list/rm/create-file are wired; add goes through the SDK to disk (binding files are written only by the SDK); test is loudly unported.
    "prompt",
    // repair: the ten-step ladder + the `-D` deep extension (the engine was long in core::repair) + the command-surface wiring.
    // the write-surface behavior (really running repairs) is covered by the CLI L2; here only --dry-run and the surface are verified.
    "repair",
    // agent: the address-level bootstrap control surface (doctor's read-only probe + SDK selection).
    "agent",
];

static SEQ: AtomicU32 = AtomicU32::new(0);

/// A one-shot temp dir (dedicated to this file; the lib's testutil is `cfg(test)` private, invisible to integration tests).
struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(tag: &str) -> Self {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "aimail-rs-it-{}-{}-{}",
            std::process::id(),
            seq,
            tag
        ));
        std::fs::create_dir_all(&path).expect("temp dir");
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// Run the binary once under a hermetic HOME/AIMAIL_HOME.
fn run(args: &[&str]) -> (i32, String, String) {
    run_in(args, false)
}

/// with `with_system=true`, first create a **legal system config** (`systems/s1/aimail_gateway.json`),
/// so the command can pass validation and reach its own branch (used to assert the honesty of an "unported surface").
fn run_in(args: &[&str], with_system: bool) -> (i32, String, String) {
    let tmp = TempDir::new("surface");
    let home = tmp.path().join("home");
    let aimail_home = tmp.path().join("aimail-home");
    std::fs::create_dir_all(&home).unwrap();
    if with_system {
        let sid_dir = aimail_home.join("systems").join("s1");
        std::fs::create_dir_all(&sid_dir).unwrap();
        std::fs::write(
            sid_dir.join("aimail_gateway.json"),
            br#"{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1","system_name":"s1","save_raw_snapshots":true}"#,
        )
        .unwrap();
    } else {
        std::fs::create_dir_all(&aimail_home).unwrap();
    }
    let out = Command::new(env!("CARGO_BIN_EXE_aimail"))
        .args(args)
        .env("HOME", &home)
        .env("AIMAIL_HOME", &aimail_home)
        .env_remove("AIMAIL_PROG_DIR")
        .output()
        .expect("spawn aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

#[test]
fn version_line_shape_matches_python_contract() {
    let (rc, out, err) = run(&["version"]);
    assert_eq!(rc, 0, "rc={rc} stderr={err}");
    let line = out.trim_end();
    let parts: Vec<&str> = line.split(' ').collect();
    assert_eq!(parts.len(), 3, "want `aimail <ver> (<mode>)`, got {line:?}");
    assert_eq!(parts[0], "aimail", "prefix: {line:?}");
    assert_eq!(parts[1].split('.').count(), 3, "semver: {line:?}");
    assert!(
        parts[1]
            .split('.')
            .all(|p| p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty()),
        "numeric semver: {line:?}"
    );
    assert!(
        matches!(parts[2], "(dev)" | "(bootstrapped)"),
        "mode: {line:?}"
    );
}

#[test]
fn top_level_subcommands_registered() {
    let (rc, out, _) = run(&["--help"]);
    assert_eq!(rc, 0);
    for name in TOP_LEVEL {
        assert!(out.contains(name), "missing subcommand in help: {name}");
    }
}

#[test]
fn prompt_nested_subcommands_registered() {
    let (rc, out, err) = run(&["prompt", "--help"]);
    assert_eq!(rc, 0, "rc={rc} err={err}");
    for name in PROMPT_SUBCOMMANDS {
        assert!(out.contains(name), "missing prompt subcommand: {name}");
    }
}

#[test]
fn machine_surface_is_hidden_from_human_help() {
    for args in [vec!["--help"], vec!["install", "--help"]] {
        let (rc, out, err) = run(&args);
        assert_eq!(rc, 0, "rc={rc} err={err}");
        for lit in MACHINE_LITERALS {
            assert!(
                !out.contains(lit),
                "{args:?}: leaked machine literal {lit:?}"
            );
        }
    }
}

#[test]
fn unported_commands_are_honest() {
    for name in TOP_LEVEL {
        if IMPLEMENTED.contains(name) {
            continue;
        }
        let (rc, out, err) = run(&[name]);
        assert_ne!(rc, 0, "'{name}' must not exit 0 while unported: {out:?}");
        assert!(
            err.contains("not yet ported"),
            "'{name}' stderr must say so, got {err:?}"
        );
    }
}

#[test]
fn implemented_list_does_not_claim_unported_commands() {
    // the reverse ratchet: a command in IMPLEMENTED must really no longer print the stub wording
    for name in IMPLEMENTED {
        let (_, _, err) = run(&[name]);
        assert!(
            !err.contains("not yet ported"),
            "'{name}' is listed IMPLEMENTED but still hits the stub: {err:?}"
        );
    }
}

#[test]
fn stats_on_empty_machine_is_the_single_line_and_rc0() {
    let (rc, out, err) = run(&["stats"]);
    assert_eq!(rc, 0, "rc={rc} err={err}");
    assert_eq!(
        out.trim_end(),
        "  no aimail systems configured on this machine"
    );
}

#[test]
fn persona_pointer_shell_keeps_rc2() {
    let (rc, out, err) = run(&["persona"]);
    assert_eq!(
        rc, 2,
        "persona is a pointer shell (contract rc 2), out={out:?}"
    );
    assert!(
        err.contains("welcome"),
        "stderr must point at welcome: {err:?}"
    );
}

#[test]
fn unported_faces_are_honest() {
    // the unported **surface** on a ported **command**: must be an explicit non-zero + an explanation, and must never silently degrade into the view surface
    // (the Python side `cli/aimail:2045-2065` recorded two silent-degradation incidents; assertions nail this down here).
    let cases: &[&[&str]] = &[
        // `address -n` (set-name) was wired in P4 slice 4 => removed from this list.
        // `address -m` (set-manager) was wired in P4 slice 2 (via the SDK by-name invocation) => removed from this list;
        //    validation-path acceptance: no -a/-e locator => rc=1 "this operation needs -a <agent> or -e <email> to locate the target address".
        // `address -d/--default` was wired in P4 slice 3 => removed (behavior acceptance in tests/address_default.rs).
        // `address --inbound-live/--inbound-down` (hidden surface) was wired in P4 slice 5 => this list is **empty**;
        // acceptance in tests/address_inbound_live.rs (8 groups: mutual exclusion/locator/idempotency both states/row withdrawal+backlog).
        // `domain --add` was really implemented in P4 slice 1 (the create surface) => removed from the "unported surface" list;
        // behavior acceptance in tests/domain_add.rs (stub gateway).
    ];
    for args in cases {
        let (rc, out, err) = run_in(args, true);
        assert_ne!(rc, 0, "{args:?} must not exit 0 while unported: {out:?}");
        assert!(
            err.contains("not yet ported"),
            "{args:?} stderr must say so, got {err:?}"
        );
        assert!(
            !out.contains("webhook   status") && !out.contains("Domains of system"),
            "{args:?} silently fell back to a view: {out:?}"
        );
    }
}

#[test]
fn missing_subcommand_is_rc2_like_argparse() {
    let (rc, _, _) = run(&[]);
    assert_eq!(rc, 2, "no subcommand should mirror argparse rc 2");
}
