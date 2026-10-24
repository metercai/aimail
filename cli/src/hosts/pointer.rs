//! ⑤ Pointer surface: locating, ownership, and scanning of pointer files in host home (`<home>/<pointer_file>`).
//!
//! The pointer is the sole cross-process fact of "which system a given host is connected to": `aimail address` / `stats` /
//! `reset` / `uninstall` / `check` all rely on it to establish the host ↔ sid association. The filename belongs to the contract
//! ([`crate::core::contract::pointer_file`]), and the shape belongs to the host ([`Host::pointer_kind`]):
//! `Root` ⇒ only root pointer; `RootOrProfiles` ⇒ root + `profiles/*/`.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::core::contract;
use crate::hosts::identity::platform_root;
use crate::hosts::{all, get, Host, PointerKind};

/// All pointer candidate paths for a given host (`RootOrProfiles` ⇒ root + each profile subdirectory, sorted).
pub fn paths(user_home: &Path, h: &dyn Host) -> Vec<PathBuf> {
    let root = platform_root(user_home, h.id());
    let file = contract::pointer_file();
    let root_ptr = root.join(file);
    if h.pointer_kind() != PointerKind::RootOrProfiles {
        return vec![root_ptr];
    }
    let mut out = vec![root_ptr];
    let profiles = root.join("profiles");
    if profiles.is_dir() {
        let mut subs: Vec<PathBuf> = std::fs::read_dir(&profiles)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        subs.sort();
        for sub in subs {
            out.push(sub.join(file));
        }
    }
    out
}

/// The **current** pointer path for a host: use root if root pointer exists, otherwise take the first of `profiles/*`; return root path if neither exists.
pub fn path(user_home: &Path, h: &dyn Host) -> PathBuf {
    let root_ptr = platform_root(user_home, h.id()).join(contract::pointer_file());
    if h.pointer_kind() != PointerKind::RootOrProfiles {
        return root_ptr;
    }
    if root_ptr.is_file() {
        return root_ptr;
    }
    let profiles = platform_root(user_home, h.id()).join("profiles");
    let mut candidates: Vec<PathBuf> = std::fs::read_dir(&profiles)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path().join(contract::pointer_file()))
        .filter(|p| p.is_file())
        .collect();
    candidates.sort();
    candidates.into_iter().next().unwrap_or(root_ptr)
}

/// Read `system_id` from the pointer file (unreadable/unparseable ⇒ `"")`.
pub fn sid(ptr: &Path) -> String {
    let Ok(text) = std::fs::read_to_string(ptr) else {
        return String::new();
    };
    serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|v| {
            v.get("system_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default()
}

/// The sid pointed to by this host's pointer (no pointer ⇒ `"")`.
pub fn sid_for(user_home: &Path, h: &dyn Host) -> String {
    let ptr = path(user_home, h);
    if ptr.is_file() {
        sid(&ptr)
    } else {
        String::new()
    }
}

/// `sid -> [host names]` (preserving enumeration order; if the same sid is pointed to by multiple hosts, accumulate in order).
pub fn all_sids(user_home: &Path) -> Vec<(String, Vec<&'static str>)> {
    let mut out: Vec<(String, Vec<&'static str>)> = Vec::new();
    for h in all() {
        let sid = sid_for(user_home, *h);
        if sid.is_empty() {
            continue;
        }
        match out.iter_mut().find(|(s, _)| *s == sid) {
            Some((_, v)) => v.push(h.id()),
            None => out.push((sid, vec![h.id()])),
        }
    }
    out
}

// ── Thin wrappers for getting values by host name (callers usually only have the id string)──────────────────

/// By-name version of [`paths`] (unknown host ⇒ empty table).
pub fn paths_for(user_home: &Path, name: &str) -> Vec<PathBuf> {
    get(name).map(|h| paths(user_home, h)).unwrap_or_default()
}

/// By-name version of [`path`] (unknown host ⇒ empty path).
pub fn path_for(user_home: &Path, name: &str) -> PathBuf {
    get(name).map(|h| path(user_home, h)).unwrap_or_default()
}

/// By-name version of [`sid_for`] (unknown host ⇒ `"")`.
pub fn sid_for_name(user_home: &Path, name: &str) -> String {
    get(name).map(|h| sid_for(user_home, h)).unwrap_or_default()
}

/// By-name version of pointer shape (unknown host ⇒ `"root"`, consistent with historical default).
pub fn kind_of(name: &str) -> &'static str {
    get(name)
        .map(|h| h.pointer_kind().as_str())
        .unwrap_or("root")
}

/// Pointer found under the platform root: `(agent_name, email, system_id)`.
///
/// Recursive, skipping `.git` / `node_modules` / `.venv`; root-level pointer corresponds to the main agent (address base name normalized by SDK
/// to `agent`), named directories use the directory name. Only recognizes contract constants (no historical names for imagined old machines).
pub fn scan(root: &Path) -> Vec<(String, String, String)> {
    let mut found = Vec::new();
    if !root.is_dir() {
        return found;
    }
    let names = [contract::pointer_file(), contract::binding_file()];
    walk(root, root, &names, &mut found);
    found.sort();
    found
}

fn walk(dir: &Path, root: &Path, names: &[&str], out: &mut Vec<(String, String, String)>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if entry.path().is_dir() {
            if matches!(name.as_str(), ".git" | "node_modules" | ".venv") {
                continue;
            }
            dirs.push(entry.path());
        } else {
            files.push(entry.path());
        }
    }
    dirs.sort();
    files.sort();
    for path in files {
        let fname = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        if !names.contains(&fname.as_str()) {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(v) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let email = v
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let sid = v
            .get("system_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if email.is_empty() || sid.is_empty() {
            continue;
        }
        let agent = if dir == root {
            // Root pointer = main agent; address base name normalized by SDK to `agent` (platform private root name does not participate in naming).
            "agent".to_string()
        } else {
            dir.file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        };
        out.push((agent, email, sid));
    }
    for d in dirs {
        walk(&d, root, names, out);
    }
}
