//! Local mail data statistics (used by `stats`, reused later by `check`/`repair`).
//!
//! Current code reference: `cli/aimail:2882-2920`
//! （`_addr_clean` / `_sid_for_addr` / `_mail_stats` / `_fmt_size`）。
//!
//! Directory shape (three-layer convergence: agent layer `mail/` leaf):
//! `<home>/systems/<sid|_unassigned>/<cleaned_addr>/mail/**`

use crate::core::{config, contract};
use std::path::{Path, PathBuf};

/// Address → directory key (same formula as `_addr_clean` / `clean_agent_dir_name`, single implementation).
pub fn addr_dir_name(email: &str) -> String {
    config::clean_agent_dir_name(email)
}

/// Scan `systems/*/<cleaned>/<binding_file>` to get owning sid; if not found ⇒ `""`.
pub fn sid_for_addr(home_dir: &Path, email: &str) -> String {
    let cleaned = addr_dir_name(email);
    let systems = config::systems_root_in(home_dir);
    let Ok(entries) = std::fs::read_dir(&systems) else {
        return String::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter(|e| {
            e.path()
                .join(&cleaned)
                .join(contract::binding_file())
                .is_file()
        })
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort(); // Same order as Python `sorted(...).iterdir()` to take the first
    names.into_iter().next().unwrap_or_default()
}

/// Local mail directory for this address (`<home>/systems/<sid|_unassigned>/<cleaned>/mail`).
pub fn mail_dir(home_dir: &Path, email: &str) -> PathBuf {
    let sid = sid_for_addr(home_dir, email);
    let key = if sid.is_empty() { "_unassigned" } else { &sid };
    config::systems_root_in(home_dir)
        .join(key)
        .join(addr_dir_name(email))
        .join("mail")
}

/// `(received_count, byte_count)` —— received_count = count of `in-*.json` filenames; byte_count = sum of sizes of **all** files
/// in the directory (unreadable files are skipped, no interruption).
pub fn mail_stats(home_dir: &Path, email: &str) -> (u64, u64) {
    let dir = mail_dir(home_dir, email);
    let mut count = 0u64;
    let mut size = 0u64;
    walk(&dir, &mut count, &mut size);
    (count, size)
}

fn walk(dir: &Path, count: &mut u64, size: &mut u64) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let Ok(ft) = entry.file_type() else { continue };
        let path = entry.path();
        if ft.is_dir() {
            // Do not follow directory symlinks (same as Python `os.walk(followlinks=False)`)
            walk(&path, count, size);
            continue;
        }
        if !ft.is_file() {
            continue;
        }
        if let Ok(meta) = entry.metadata() {
            *size += meta.len();
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with("in-") && name.ends_with(".json") {
            *count += 1;
        }
    }
}

/// Human-readable size (identical to `_fmt_size`: integer for B range, one decimal for others, unit table B/KB/MB/GB).
pub fn fmt_size(size: u64) -> String {
    let mut n = size as f64;
    for (i, unit) in ["B", "KB", "MB", "GB"].iter().enumerate() {
        if n < 1024.0 || *unit == "GB" {
            return if *unit == "B" {
                format!("{} B", n as u64)
            } else {
                format!("{n:.1} {unit}")
            };
        }
        n /= 1024.0;
        let _ = i;
    }
    format!("{n:.1} GB")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn fmt_size_matches_python_formatting() {
        assert_eq!(fmt_size(0), "0 B");
        assert_eq!(fmt_size(1023), "1023 B");
        assert_eq!(fmt_size(1024), "1.0 KB");
        assert_eq!(fmt_size(1536), "1.5 KB");
        assert_eq!(fmt_size(1024 * 1024), "1.0 MB");
        assert_eq!(fmt_size(3 * 1024 * 1024 * 1024), "3.0 GB");
    }

    #[test]
    fn mail_stats_counts_only_in_json_and_sums_every_file() {
        let tmp = TempDir::new("mailstats");
        let home = tmp.path();
        let sid = "sid1";
        let email = "agent@host.test";
        let dir = mail_dir(home, email);
        let month = dir.join("202610");
        std::fs::create_dir_all(&month).unwrap();
        std::fs::write(month.join("in-1.json"), b"abcd").unwrap();
        std::fs::write(month.join("in-2.json"), b"ab").unwrap();
        std::fs::write(month.join("out-1.json"), b"abcdef").unwrap();
        std::fs::write(month.join("notes.txt"), b"x").unwrap();

        // No binding file ⇒ falls to _unassigned key, still readable
        assert_eq!(sid_for_addr(home, email), "");
        let (n, size) = mail_stats(home, email);
        assert_eq!(n, 2, "only in-*.json counts as received");
        assert_eq!(size, 4 + 2 + 6 + 1);

        // After creating binding file, belongs to that sid (same data, still readable)
        let bind = config::binding_file_path_in(home, sid, email);
        std::fs::create_dir_all(bind.parent().unwrap()).unwrap();
        std::fs::write(&bind, b"{}").unwrap();
        assert_eq!(sid_for_addr(home, email), sid);
        assert_eq!(
            mail_stats(home, email).0,
            0,
            "data lives under the other key"
        );
    }
}
