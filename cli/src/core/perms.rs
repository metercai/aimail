//! CLI -> SDK delegation executor (**language-agnostic**) -- 2026-10-04 owner ruling A'.
//!
//! Current code reference: Python write points use `os.chmod(path, 0o600)` (e.g.
//! `cli/setup_system.py:370`, `cli/aimail:2464`), and are generally wrapped in `try/except OSError`
//! ⇒ **Best-effort**: failures do not interrupt the main flow.
//!
//! Platform differences (intentional, not omission): POSIX permission bits do not exist on Windows (`os.chmod` only toggles
//! read-only bit) ⇒ on non-unix platforms this module is a no-op, delegating "user-private" to the inherited ACL of the installation directory;
//! This is consistent with the actual effect of the Python side on Windows, not a functional deficiency.

use std::io;
use std::path::Path;

/// Set file to **owner read/write only** (unix: 0600; non-unix: no-op).
///
/// Caller **ignores failure** same as current Python (tightening credential file permissions is not a prerequisite for the main flow).
#[cfg(unix)]
pub fn set_user_only(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
}

/// See unix version notes: Windows has no POSIX permission bits, this call is a no-op.
#[cfg(not(unix))]
pub fn set_user_only(_path: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn set_user_only_tightens_permissions() {
        let tmp = TempDir::new("perms");
        let f = tmp.path().join("cred.json");
        std::fs::write(&f, b"{}").unwrap();
        set_user_only(&f).expect("chmod");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&f).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "want 0600, got {mode:o}");
        }
        #[cfg(not(unix))]
        {
            assert!(
                f.is_file(),
                "no-op on non-unix must still leave the file intact"
            );
        }
    }
}
