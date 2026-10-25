//! P3c slice 2 offline acceptance: `welcome` engine remainder (ownership / draft parsing / two letter bodies).
//! Unit tests serial (set `AIMAIL_HOME` fixture in same process, avoid race).

use std::path::Path;

use aimail::core::welcome::{
    agent_log_path_for, approve_message, parse_draft_from_reply, sid_for, welcome_message,
    WELCOME_SUBJECT_MARKER,
};

fn fx() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("AIMAIL_HOME", &home);
    std::env::set_var("HOME", home.join("uhome"));
    tmp
}

#[test]
fn welcome_engine_offline() {
    let tmp = fx();
    let home = tmp.path().join("ahome");
    let cleaned = "a_example.test"; // addr_clean("a@example.test")
    let leaf = home.join("systems/s1").join(cleaned);
    std::fs::create_dir_all(leaf.join("mail/202610")).unwrap();
    // Bound file names follow contract constants (ratchet: no literals allowed in new files)
    std::fs::write(
        leaf.join(aimail::core::contract::binding_file()),
        r#"{"email":"a@example.test","api_key":"k"}"#,
    )
    .unwrap();

    // 1) Three-layer closure: can attribute sid by bound file
    assert_eq!(sid_for(&home, cleaned), "s1");
    assert_eq!(sid_for(&home, "nobody"), "");
    // 2) Log path: sid attribution + agentmail.log
    let lp = agent_log_path_for("a@example.test");
    assert!(
        lp.ends_with("systems/s1/a_example.test/agentmail.log"),
        "{}",
        lp.display()
    );
    assert!(agent_log_path_for("zz@elsewhere.test")
        .to_string_lossy()
        .contains("_unassigned"));

    // 3) Draft parsing: three tags present + direction=outbound ⇒ hit
    std::fs::write(
        leaf.join("mail/202610/out-1.json"),
        r#"{"direction":"outbound","subject":"Re: whatever","body":"persona: I operate the mail system.\nsignature: -- Agent A\ncurrent_time: 2026-10-04 09:00 UTC\n"}"#,
    )
    .unwrap();
    let got = parse_draft_from_reply("a@example.test");
    assert_eq!(got["persona"], "I operate the mail system.");
    assert_eq!(got["signature"], "-- Agent A");
    assert_eq!(got["current_time"], "2026-10-04 09:00 UTC");
    assert!(got["source"].as_str().unwrap().ends_with("out-1.json"));

    // 4a) newer snapshot "hit but missing section" ⇒ continue flipping to earlier complete version (Python semantics: only treat as missing section if still missing after flipping through all)
    std::fs::write(
        leaf.join("mail/202610/out-2.json"),
        format!(
            r#"{{"direction":"outbound","subject":"Re: {}","body":"persona: x\n"}}"#,
            WELCOME_SUBJECT_MARKER
        ),
    )
    .unwrap();
    let got2 = parse_draft_from_reply("a@example.test");
    assert_eq!(
        got2["persona"], "I operate the mail system.",
        "should continue flipping to complete version: {got2}"
    );

    // 4b) Missing segment and no earlier complete version ⇒ return empty (no guessing, no fabrication)
    std::fs::remove_file(leaf.join("mail/202610/out-1.json")).unwrap();
    let got2b = parse_draft_from_reply("a@example.test");
    assert!(
        got2b.as_object().unwrap().is_empty(),
        "Missing segment after translation still missing ⇒ must return empty: {got2b}"
    );

    // 5) direction is not outbound ⇒ skip (inbound snapshot does not count), still empty after translation
    std::fs::write(
        leaf.join("mail/202610/out-3.json"),
        r#"{"direction":"inbound","subject":"x","body":"persona: p\nsignature: s\ncurrent_time: t\n"}"#,
    )
    .unwrap();
    let got3 = parse_draft_from_reply("a@example.test");
    assert!(got3.as_object().unwrap().is_empty(), "{got3}");

    // 6) Two letters: headers complete + exactly one blank line + subject marker/trigger words in place
    let w = welcome_message("m@example.com", "a@example.test");
    assert!(
        w.starts_with("From: m@example.com\nTo: a@example.test\nMessage-ID: <welcome-"),
        "{w}"
    );
    assert!(
        w.contains("\nSubject: Welcome to AIMail World, a, since "),
        "{w}"
    );
    assert!(w.contains("\n\nWelcome to the AIMail world!"), "{w}");
    assert!(w.contains("  persona: <"), "{w}");
    assert!(w.contains("  signature: <"), "{w}");
    assert!(w.contains("  current_time: <"), "{w}");
    assert!(w.contains("\nSent "), "{w}");
    // Exactly one blank line separating headers/body (welcome body itself has multiple blank line paragraphs ⇒ cannot count globally)
    assert!(
        w.contains("\n\nWelcome to the AIMail world!"),
        "Should be followed by exactly one blank line after headers"
    );
    assert!(
        w.contains("active.\n\nTo verify"),
        "Preserve blank lines in body paragraphs"
    );

    let ap = approve_message("m@example.com", "a@example.test", "P", "S");
    assert!(
        ap.contains("\nSubject: approve persona\n\napprove persona\npersona: P\nsignature: S\n"),
        "{ap}"
    );
    assert!(
        ap.starts_with("From: m@example.com\nTo: a@example.test\nMessage-ID: <approve-"),
        "{ap}"
    );

    // 7) Constants
    assert_eq!(WELCOME_SUBJECT_MARKER, "welcome to aimail world");
    let _ = Path::new(".");
}
