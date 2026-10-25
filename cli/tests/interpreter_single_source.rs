//! Static ratchet (contract v1.0 §4.1(2)): host interpreter location chain **single source of truth** ——
//! only `src/core/sdkroot.rs` can read/write `AIMAIL_PYTHON`; all other modules must go through
//! `sdkroot::python_bin()` / `sdkroot::bind_domain*()`。
//!
//! 2026-10-12 owner decision: deerflow recovery surface (`reset`/`uninstall`) uses CLI's own `python3` to find SDK,
//! reports "interpreter python3 did not locate aimail package" (while SDK is installed in host venv) = "self-check and target system mismatch".
//! Root cause = second degenerate chain (`setup::python_bin` = env→python3) + three bare env reads. This ratchet prevents recurrence.

use std::path::{Path, PathBuf};

fn rust_sources(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    let mut subs: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
    subs.sort();
    for p in subs {
        if p.is_dir() {
            rust_sources(&p, out);
        } else if p.extension().and_then(|s| s.to_str()) == Some("rs") {
            out.push(p);
        }
    }
}

#[test]
fn interpreter_chain_has_exactly_one_implementation() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    rust_sources(&src, &mut files);
    assert!(
        files.len() > 50,
        "source tree scan anomaly ({} .rs files)",
        files.len()
    );

    let needles = [
        "env::var(\"AIMAIL_PYTHON\")",
        "env::set_var(\"AIMAIL_PYTHON\"",
    ];
    let mut offenders = Vec::new();
    for f in files {
        if f.ends_with("core/sdkroot.rs") {
            continue; // single source of truth body
        }
        let Ok(text) = std::fs::read_to_string(&f) else {
            continue;
        };
        for (i, line) in text.lines().enumerate() {
            if needles.iter().any(|n| line.contains(n)) {
                offenders.push(format!("{}:{}", f.display(), i + 1));
            }
        }
    }
    assert!(
        offenders.is_empty(),
        "interpreter location chain has second implementation (should use sdkroot::python_bin()/bind_domain*): {offenders:?}"
    );
}
