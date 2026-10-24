//! ① identity surface: host recognition features + language type + pointer form.
//! The decision algorithm (Strict/Scan/Repair tiers) will be migrated to this module in P2.

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// The implementation language of the agent system (derived from `register.kind`, replacing scattered string comparisons).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lang {
    Node,
    Python,
}

/// Pointer shape: root pointer / root + `profiles/*`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PointerKind {
    Root,
    RootOrProfiles,
}

impl PointerKind {
    pub fn as_str(self) -> &'static str {
        match self {
            PointerKind::Root => "root",
            PointerKind::RootOrProfiles => "root_or_profiles",
        }
    }
}

/// Serialize into the registry shape `{"kind": "..."}` (spec output is byte-for-byte faithful).
impl Serialize for PointerKind {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(1))?;
        m.serialize_entry("kind", self.as_str())?;
        m.end()
    }
}

/// Platform identification features: `dir_name` (optional directory name gate) + `markers` (must hit).
#[derive(Serialize, Clone, Copy)]
pub struct Detect {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dir_name: Option<&'static str>,
    pub markers: &'static [&'static str],
}

use std::path::{Path, PathBuf};

use crate::hosts::{all, get, order, Host};

/// `Strict`: which platform the directory itself hits (`dir_name` optional + all `markers` hit).
pub fn hits_dir(d: &Path) -> Option<&'static str> {
    let name_of = d.file_name().map(|s| s.to_string_lossy().into_owned());
    for h in all() {
        let det = h.detect();
        if det.markers.is_empty() {
            continue;
        }
        if let Some(want) = det.dir_name {
            if name_of.as_deref() != Some(want) {
                continue;
            }
        }
        if det.markers.iter().all(|m| d.join(m).exists()) {
            return Some(h.id());
        }
    }
    None
}

/// `Strict`: the decision result for `home` (if not identified ⇒ `"unknown"`, do not guess).
pub fn detect_platform_from_home(home: &Path) -> &'static str {
    hits_dir(home).unwrap_or("unknown")
}

/// `Scan`: decide based on "platform root candidates" (formerly `checks::adapters::detect_agent_type`).
pub fn detect_agent_type(
    user_home: &Path,
    root: Option<&Path>,
    agent_home_explicit: bool,
) -> &'static str {
    if agent_home_explicit {
        if let Some(h) = all().iter().find(|h| h.uses_agent_home()) {
            return h.id();
        }
    }
    let home = match root {
        Some(r) => expand(r),
        None => user_home.to_path_buf(),
    };
    for h in all() {
        let det = h.detect();
        if det.markers.is_empty() {
            continue;
        }
        let dir_name = det.dir_name.unwrap_or_else(|| h.home_dir());
        let cands: Vec<PathBuf> = if root.is_none() {
            vec![home.join(dir_name)]
        } else {
            vec![home.clone(), home.join(dir_name)]
        };
        for base in cands {
            if det.markers.iter().all(|m| base.join(m).exists()) {
                return h.id();
            }
        }
    }
    "unknown"
}

/// `Repair`: loose decision (formerly `repair::detect_platform_for_repair`).
pub fn detect_for_repair(dir: &Path) -> &'static str {
    let name_of = dir.file_name().map(|s| s.to_string_lossy().into_owned());
    for h in all() {
        let det = h.detect();
        if let Some(want) = det.dir_name {
            if name_of.as_deref() != Some(want) {
                continue;
            }
        }
        let hit_marker = det.markers.iter().any(|m| dir.join(m).exists());
        let hit_hint = h.repair_hints().iter().any(|m| dir.join(m).exists());
        if hit_marker || hit_hint {
            return h.id();
        }
    }
    "unknown"
}

/// Expansion of `~`/`~/x` goes through [`crate::core::home::expand_user`] (single cross-platform implementation:
/// falls back to the parent directory of the program root when HOME is missing; same source on Windows), do not write another copy here.
fn expand(p: &Path) -> PathBuf {
    crate::core::home::expand_user(&p.to_string_lossy())
}

/// Normalize `--home` to a **platform root**: if it hits itself ⇒ keep as is; if exactly one subdirectory hits ⇒ that subdirectory;
/// if none hit ⇒ keep as is (let downstream report errors according to original logic, do not silently change semantics). Return value is always an absolute path.
pub fn normalize_platform_home(home: &Path) -> PathBuf {
    let p = if home.is_absolute() {
        home.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|c| c.join(home))
            .unwrap_or_else(|_| home.to_path_buf())
    };
    if !p.is_dir() {
        return p;
    }
    if hits_dir(&p).is_some() {
        return p;
    }
    let mut subs: Vec<PathBuf> = std::fs::read_dir(&p)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|q| q.is_dir())
        .collect();
    subs.sort();
    for sub in subs {
        if hits_dir(&sub).is_some() {
            return sub;
        }
    }
    p
}

/// Platform root directory (`<user_home>/<home_dir>`).
pub fn platform_root(user_home: &Path, name: &str) -> PathBuf {
    let dir = get(name).map(|h| h.home_dir()).unwrap_or("");
    if dir.is_empty() {
        user_home.join(format!(".{name}"))
    } else {
        user_home.join(dir)
    }
}

/// Platform root exists **and** features identify it as this platform.
pub fn platform_root_exists(user_home: &Path, name: &str) -> bool {
    let root = platform_root(user_home, name);
    if !root.exists() {
        return false;
    }
    detect_platform_from_home(&root) == name
}

/// Validation of explicit override values for `--platform`: empty ⇒ `Ok(None)`; unknown ⇒ `Err(message)` (no silent fallback).
pub fn platform_override(name: &str) -> Result<Option<String>, String> {
    let p = name.trim();
    if p.is_empty() {
        return Ok(None);
    }
    if !order().contains(&p) {
        let mut names: Vec<&str> = order().to_vec();
        names.sort_unstable();
        return Err(format!(
            "--platform '{p}' unknown; valid: {}",
            names.join(", ")
        ));
    }
    Ok(Some(p.to_string()))
}

/// Platform resolution: home features take priority, fallback to "the first host with a pointer pointing to a system"; if none ⇒ `""`.
///
/// Difference from [`detect_platform_from_home`]: the latter returns `"unknown"` ( **non-empty** ) when no hit,
/// using it directly as a platform name would treat "not identified" as a platform ⇒ must use this function to collect into `""`.
pub fn resolve_platform(system_home: &Path) -> String {
    let p = if system_home.as_os_str().is_empty() {
        String::new()
    } else {
        detect_platform_from_home(system_home).to_string()
    };
    if p != "unknown" {
        return p;
    }
    let uh = crate::core::home::user_home();
    for h in all() {
        if !crate::hosts::pointer::sid_for(&uh, *h).is_empty() {
            return h.id().to_string();
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> tempfile::TempDir {
        tempfile::Builder::new()
            .prefix(name)
            .tempdir()
            .expect("tempdir")
    }

    /// Counter-example (historical incident): `.hermes` also has `profiles/`, Strict must rely on the basename gate
    /// to reject dsh, otherwise install would judge hermes home as dsh.
    #[test]
    fn strict_basename_gate_keeps_hermes_out_of_dsh() {
        let t = tmp("id-strict");
        let home = t.path().join(".hermes");
        std::fs::create_dir_all(home.join("profiles")).unwrap();
        std::fs::create_dir_all(home.join("hermes-agent")).unwrap();
        assert_eq!(detect_platform_from_home(&home), "hermes");
    }

    /// Strict: pi/dsh basename gate is active (same feature with a different directory name ⇒ not recognized).
    #[test]
    fn strict_requires_the_declared_dir_name() {
        let t = tmp("id-gate");
        let pi = t.path().join(".pi");
        std::fs::create_dir_all(pi.join("agent")).unwrap();
        assert_eq!(detect_platform_from_home(&pi), "pi");

        let other = t.path().join("not-pi");
        std::fs::create_dir_all(other.join("agent")).unwrap();
        assert_eq!(detect_platform_from_home(&other), "unknown");
    }

    /// Scan: scan user_home to recognize platform; `--agent-home` semantics belong to hermes.
    #[test]
    fn scan_mode_finds_hosts_under_user_home_and_honors_agent_home() {
        let t = tmp("id-scan");
        let uh = t.path();
        std::fs::create_dir_all(uh.join(".hermes/hermes-agent")).unwrap();
        assert_eq!(detect_agent_type(uh, None, false), "hermes");
        assert_eq!(detect_agent_type(uh, None, true), "hermes");
    }

    /// Repair: loose semantics -- only `.hermes` in `profiles/` is also recognized (Strict cannot recognize it).
    #[test]
    fn repair_mode_is_lenient_where_strict_is_not() {
        let t = tmp("id-repair");
        let home = t.path().join(".hermes");
        std::fs::create_dir_all(home.join("profiles")).unwrap();
        assert_eq!(detect_platform_from_home(&home), "unknown");
        assert_eq!(detect_for_repair(&home), "hermes");
    }

    /// Platform root normalization: given a parent directory and exactly one subdirectory hits ⇒ return that subdirectory; not recognized ⇒ keep as is.
    #[test]
    fn normalize_picks_the_single_recognized_child() {
        let t = tmp("id-normalize");
        let root = t.path().join("machine");
        std::fs::create_dir_all(root.join("openclaw").join("x")).unwrap();
        std::fs::write(root.join("openclaw/openclaw.json"), "{}").unwrap();
        // basename is not .openclaw ⇒ Strict does not recognize (openclaw has no dir_name gate, but directory name is arbitrary)
        let got = normalize_platform_home(&root);
        assert_eq!(got, root.join("openclaw"));
        assert_eq!(detect_platform_from_home(&got), "openclaw");
    }

    /// `--platform` value validation: empty ⇒ None; valid ⇒ keep as is; unknown ⇒ message contains all valid values (ascending order).
    #[test]
    fn platform_override_validates_against_the_registry() {
        assert_eq!(platform_override("").unwrap(), None);
        assert_eq!(platform_override("   ").unwrap(), None);
        for n in order() {
            assert_eq!(platform_override(n).unwrap().as_deref(), Some(*n));
        }
        let e = platform_override("nope").unwrap_err();
        let mut names: Vec<&str> = order().to_vec();
        names.sort_unstable();
        assert_eq!(
            e,
            format!("--platform 'nope' unknown; valid: {}", names.join(", "))
        );
    }
}
