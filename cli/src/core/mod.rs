//! Core infrastructure layer (filled module by module from plan v3 S2 onwards).
//!
//! | Module | Responsibility | Current code reference |
//! |---|---|---|
//! | [`home`] | Main root / program root / release form | `cli/runtime_core.py:161-171`, `pysdk/aimail_base.aimail_home()` |
//! | [`perms`] | Credential file permissions (cross-platform helper) | `os.chmod(..., 0o600)` at each Python write point |
//! | [`contract`] | Agent-side contract constants (compile-time embedded single source of truth) | `contract/aimail-contract.json` |
//! | [`platforms`] | Value view of platform host table (data in `hosts/`) | `cli/src/hosts/mod.rs` |
//! | [`config`] | System-level environment file read/write (CLI exclusive write permission) + binding file read-only entry | `cli/setup_system.py:331-370`, `cli/aimail:2460-2467` |
//!
//! Not yet implemented are `gateway` / `smtp` (HTTP/SMTP clients) and `proc`: they rely on S3/S6's
//! Only meaningful for real consumers; landing it first would become "dead code with zero callers + unnecessary dependencies" (principle of minimal mechanism).

pub mod bridge;
pub mod bridge_deploy;
pub mod bridge_wire;
pub mod check;
pub mod checks;
pub mod config;
pub mod contract;
pub mod detect;
pub mod gateway;
pub mod home;
pub mod http;
pub mod inbound_route;
pub mod mail;
pub mod netenv;
pub mod perms;
pub mod ping;
pub mod probe;
pub mod procwin;
pub mod pyjson;
pub mod register;
pub mod repair;
pub mod sdk;
pub mod sdkcall;
pub mod sdkroot;
pub mod setup;
pub mod sig;
pub mod smtp;
pub mod steps;
pub mod style;
pub mod time;
pub mod webhookplan;
pub mod welcome;

#[cfg(test)]
pub(crate) mod testutil {
    //! Minimal temporary directory helper — does not import third-party crate (tempfile), sufficient for the purpose.
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU32, Ordering};

    /// Process-level test environment lock (P8 layer 2): Tests touching process environment (set_var/remove_var) or process-local
    /// Tests involving bound state must always hold this lock first — process env is global across threads, under parallel `cargo test`
    /// will cause mutual interference if each modifies its own (observed alternating reds in bridge_pids/drain_stuck). Hold the lock to cover the entire
    /// set value → code under test reads → restore sequence.
    pub(crate) static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    static SEQ: AtomicU32 = AtomicU32::new(0);

    /// `<system temp dir>/aimail-rs-test-<pid>-<seq>-<tag>`, recursively deleted on Drop.
    pub struct TempDir {
        path: PathBuf,
    }

    impl TempDir {
        pub fn new(tag: &str) -> Self {
            let seq = SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "aimail-rs-test-{}-{}-{}",
                std::process::id(),
                seq,
                tag
            ));
            std::fs::create_dir_all(&path).expect("create temp dir");
            Self { path }
        }

        pub fn path(&self) -> &Path {
            &self.path
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}
