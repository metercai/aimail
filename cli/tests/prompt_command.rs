#![cfg(unix)]
// the whole file is unix semantics (stub executable bit / sh-driven / symlink); reopen after the windows branch (W2) lands.
//! `prompt` slice 1 acceptance: `list`/`rm`/`create-file` and the command surface (the SDK disk path of `add` is left to L2; `test` is unported).
//!
//! Criteria: ① bare `prompt` => rc=2 (`paction` required); ② `list` renders the three states (address-level role / the common.md fallback /
//! MISSING) + name ordering + fields joined with `|` + `[disabled]` + the warning for field-less lines; ③ the `rm` miss, verbatim;
//! ④ `create-file` validation and disk write; ⑤ `test` loudly unported.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use aimail::core::{bridge_wire as bw, contract};

fn bin() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("target/debug/aimail");
    p
}

fn run(home: &Path, argv: &[&str]) -> (i32, String, String) {
    // the 3-op door (`python -m aimail.sdk_ops`) always goes to the **installed package** (zero paths) => hitting an old installed snapshot misses the new op
    // (e.g. `prompt-test`). The unit test does not depend on the installed version (the iron rule against relying on the local environment): use `PYTHONPATH` to point `aimail`
    // at the **repo pysdk** (the same stance as the old `sdkcall` staging) — the door interpreter is still `python3` (with
    // `AIMAIL_PYTHON` removed), and `-m aimail.sdk_ops` imports the repo package (with the new op) via `PYTHONPATH`.
    let prog = home.join("prog");
    std::fs::create_dir_all(&prog).unwrap();
    let stage = prog.join("sdk-pythonpath");
    let link = stage.join("aimail");
    if !link.exists() {
        std::fs::create_dir_all(&stage).unwrap();
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("pysdk");
        std::os::unix::fs::symlink(&repo, &link).unwrap();
    }
    let out = Command::new(bin())
        .args(argv)
        .env("AIMAIL_PROG_DIR", &prog)
        .env("AIMAIL_HOME", home)
        .env("HOME", home)
        .env("PYTHONPATH", &stage)
        .env_remove("AIMAIL_PYTHON")
        .stdin(Stdio::null())
        .output()
        .expect("spawn");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

/// Fixture: two rules (one with fields+disabled, one without fields), three role files (address-level / the system-level common fallback / none).
fn fixture() -> (tempfile::TempDir, PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    let sys = home.join("systems/s1");
    let leaf = sys.join(bw::addr_clean("agent@example.test"));
    std::fs::create_dir_all(leaf.join("role_prompt")).unwrap();
    std::fs::write(
        sys.join("aimail_gateway.json"),
        r#"{"gateway_url":"http://127.0.0.1:1","admin_key":"AK","system_id":"s1","domain":"example.test"}"#,
    )
    .unwrap();
    let rules = r#"[
      {"name":"30_later","file":"later","subject":["审批","approve"],"body":["x"]},
      {"name":"10_first","file":"first","sender":["m@example.test"],"enabled":false},
      {"name":"40_nofield","file":"nofield"},
      {"name":"50_common","file":"commononly"}
    ]"#;
    std::fs::write(
        leaf.join(contract::binding_file()),
        format!(
            r#"{{"email":"agent@example.test","api_key":"K","webhook_url":"","prompt_rules":{rules}}}"#
        ),
    )
    .unwrap();
    // the address-level role (10_first); the system-level common.md fallback (the 50_common file itself does not exist => it goes to common.md)
    std::fs::write(leaf.join("role_prompt/first.md"), "# first\n").unwrap();
    std::fs::create_dir_all(sys.join("board/role_prompt")).unwrap();
    std::fs::write(sys.join("board/role_prompt/common.md"), "# common\n").unwrap();
    (tmp, home)
}

/// The role files are entirely missing (even `common.md` is gone) => `list` must report MISSING (the loader skips them when matching).
#[test]
fn role_missing_is_reported_when_no_common_md() {
    let (_t, home) = fixture();
    std::fs::remove_file(home.join("systems/s1/board/role_prompt/common.md")).unwrap();
    let (rc, out, err) = run(
        &home,
        &["prompt", "list", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    assert!(
        out.contains("MISSING (loader will skip on match)"),
        "a missing role should report MISSING: {out}"
    );
}

#[test]
fn prompt_faces_list_rm_createfile_and_command_surface() {
    let (_t, home) = fixture();
    // ① bare prompt => rc=2 (the subcommand is required)
    let (rc, _, err) = run(&home, &["prompt"]);
    assert_eq!(rc, 2, "bare prompt should be rc=2: {err}");

    // ② list: name ordering + field rendering + the disabled marker + the three role states
    let (rc, out, err) = run(
        &home,
        &["prompt", "list", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    let i30 = out.find("30_later").unwrap();
    let i10 = out.find("10_first").unwrap();
    assert!(i10 < i30, "it should be ordered by name (10 before 30): {out}");
    assert!(
        out.contains("subject=[审批 | approve]; body=[x]"),
        "the field rendering: {out}"
    );
    assert!(out.contains("file=first  [disabled]"), "the disabled marker: {out}");
    assert!(
        out.contains("(NO FIELD — loader skips this rule)"),
        "the field-less line warning: {out}"
    );
    assert!(out.contains("role_prompt/first.md"), "the address-level role: {out}");
    assert!(
        out.contains("common.md (common.md fallback)"),
        "the system-level common fallback: {out}"
    );

    // ③ the rm miss
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "rm",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "-n",
            "90_missing",
        ],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("rule 90_missing not found for this agent"),
        "{out}"
    );

    // ④ create-file: a bad stem => rc=1; a good stem => the disk write + the content header
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "create-file",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "-n",
            "20_new",
            "--file",
            "Bad.Stem",
        ],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("bad file stem"), "{out}");
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "create-file",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "-n",
            "20_new",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("created"), "{out}");
    let f = home
        .join("systems/s1")
        .join(bw::addr_clean("agent@example.test"))
        .join("role_prompt/new.md");
    let text = std::fs::read_to_string(&f).expect("the role file should have been created");
    assert!(text.starts_with("# new"), "{text}");

    // ⑤ test is wired (the L1–L5 simulation): a bare run (no input) => rc=0 + the fallback line
    //    (the layer-by-layer semantics of L1–L5 are covered by `prompt_test_walks_the_chain_and_matches_via_sdk`)
    let (rc, out, err) = run(
        &home,
        &["prompt", "test", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    assert!(
        out.contains("(matched against: subject='' sender='' to='')"),
        "{out}"
    );
}

/// `prompt test` (the L1–L5 single-source matching simulation): the two built-in levels + the L4 header + the L5 local rules + the fallback line.
#[test]
fn prompt_test_walks_the_chain_and_matches_via_sdk() {
    let (_t, home) = fixture();

    // the L1 built-in [WHOAMI] (the role file missing => MISSING, but **it returns as soon as it hits**)
    let (rc, out, err) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "[whoami] x",
        ],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    assert!(
        out.contains("L1 [WHOAMI] (built-in) → file=whoami"),
        "{out}"
    );
    // the system-level `common.md` exists in the fixture => whoami takes the fallback (MISSING only appears when even common.md is missing,
    // that state is covered by `role_missing_is_reported_when_no_common_md`)
    assert!(out.contains("common.md (common.md fallback)"), "{out}");

    // the L2 built-in welcome (the marker + all three tags present)
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "Welcome to AIMail World, agent, since 2026-10-04!",
            "--body",
            "persona: p\nsignature: s\ncurrent_time: t",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("L2 welcome (built-in) → file=role_calibrator"),
        "{out}"
    );

    // the marker is present but a tag is missing => the MISMATCH hint (and it keeps going down)
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "Welcome to AIMail World",
            "--body",
            "persona: p",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("marker=true labels=false"), "{out}");

    // L5: `30_later` hits — note that matching is **AND across fields** (the rule carries subject and body keywords at once)
    // => the sample must satisfy **both** fields at the same time, otherwise the correct result is a miss.
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "please approve this",
            "--body",
            "contains x here",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("L5 HIT 30_later → file=later"), "{out}");
    assert!(out.contains("common.md (common.md fallback)"), "{out}");
    assert!(
        out.contains("(matched against: subject='please approve this' sender='' to='')"),
        "{out}"
    );

    // counter example: only the subject is given (body empty) => `body:["x"]` does not hit => correctly lands on miss (AND across fields)
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "please approve this",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("L5 miss 30_later"), "AND across fields: {out}");

    // L5: no hit => the fallback line; and **disabled rules do not take part** (`10_first` already has enabled=false)
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--sender",
            "m@example.test",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("L5: no rule matched — chain falls through (default prompt)"),
        "{out}"
    );
    assert!(!out.contains("L5 HIT 10_first"), "a disabled rule must not hit: {out}");
    assert!(
        !out.contains("L5 miss 10_first"),
        "a disabled rule does not enter the matching loop: {out}"
    );

    // L4: the header is specified but the role file does not exist => it still hits via the `common.md` fallback (the same stance as L1);
    // "even common.md is missing => MISSING + falls through" is covered by the fixture without common.md (see the other case).
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--header",
            "nope",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("L4 X-AIMail-Prompt (gateway header) → file=nope"),
        "{out}"
    );
    assert!(out.contains("common.md (common.md fallback)"), "{out}");
}
