//! `aimail install` —— integration entry (human face + only hidden machine face `--system-only`).
//!
//! Why not "silent success": CLI gates and user scripts interpret exit codes; a silent 0 would read "did nothing"
//! as "done". The acceptance requirement for plan v3 S1 = unimplemented commands return a clear non-zero exit.

/// Print a one-line English explanation to stderr (user-facing is all English), return exit code 1.
pub fn not_yet_ported(name: &str) -> i32 {
    eprintln!(
        "aimail: '{name}' is not yet ported to the Rust CLI \
         (see cli-rust-ization-plan-v3 S3-S9); the Python CLI is still authoritative"
    );
    1
}
