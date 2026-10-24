//! Program root / main root resolution — single point with same semantics as Python side.
//!
//! Python source of truth: `cli/runtime_core.py:161-171` (`program_root()` / `toolkit_dir()`)
//! and `pysdk/aimail_base.aimail_home()`. Rust side must not assemble `~/.aimail/bin` elsewhere.
//!
//! Hierarchy:
//! - Main root (aimail_home) = `AIMAIL_HOME` env var > `~/.aimail`
//! - Program root (program_root) = `AIMAIL_PROG_DIR` environment variable > `<main_root>/bin`

use std::path::PathBuf;

fn env_nonempty(key: &str) -> Option<String> {
    match std::env::var(key) {
        Ok(v) if !v.trim().is_empty() => Some(v.trim().to_string()),
        _ => None,
    }
}

fn expand(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = env_nonempty("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    PathBuf::from(path)
}

/// Local aimail main root: `AIMAIL_HOME` > `~/.aimail`.
/// Absolute path but does not resolve symlinks (equivalent to Python `os.path.abspath`).
pub fn abs_path(p: &std::path::Path) -> std::path::PathBuf {
    if p.is_absolute() {
        return p.to_path_buf();
    }
    std::env::current_dir()
        .map(|c| c.join(p))
        .unwrap_or_else(|_| p.to_path_buf())
}

pub fn aimail_home() -> PathBuf {
    match env_nonempty("AIMAIL_HOME") {
        Some(v) => expand(&v),
        None => expand("~/.aimail"),
    }
}

/// Local program root: `AIMAIL_PROG_DIR` > `<main_root>/bin` (bootstrap installation location).
pub fn program_root() -> PathBuf {
    match env_nonempty("AIMAIL_PROG_DIR") {
        Some(v) => expand(&v),
        None => aimail_home().join("bin"),
    }
}

/// Local user home directory (`HOME`) — platform root (`~/.hermes` etc.) hangs under it.
///
/// Difference from `aimail_home()`: main root is the **aimail data root** (`~/.aimail`), user home directory is
/// **the parent directory of the platform root** (used for `stats` platform segment scanning). If `HOME` is missing, falls back to the parent of the main root,
/// ensuring no panic and no guessing of other paths.
pub fn user_home() -> PathBuf {
    match env_nonempty("HOME") {
        Some(v) => expand(&v),
        None => aimail_home()
            .parent()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/")),
    }
}

/// Release form: `dev` (running in repo) | `bootstrapped` (installed into program root).
///
/// Python side criterion is "whether the script directory is under toolkit_dir" (`cli/aimail:772`);
/// Rust is a single-file binary, equivalent criterion = whether the current executable is under the program root.
pub fn release_mode() -> &'static str {
    let Ok(exe) = std::env::current_exe() else {
        return "dev";
    };
    if exe.starts_with(program_root()) {
        "bootstrapped"
    } else {
        "dev"
    }
}

/// `~` expansion (common subset of `Path.expanduser()`: `~` and `~/…`).
pub fn expand_user(s: &str) -> std::path::PathBuf {
    if let Some(rest) = s.strip_prefix("~/") {
        user_home().join(rest)
    } else if s == "~" {
        user_home()
    } else {
        std::path::PathBuf::from(s)
    }
}

/// `runtime_core.system_home_from_sid`: reverse lookup of `system_home` from system-level config (none ⇒ empty string,
/// handled by caller as `_fail`).
pub fn system_home_from_sid(sid: &str) -> String {
    crate::core::config::load_gateway_config(sid)
        .map(|c| c.system_home)
        .unwrap_or_default()
}
