//! ⑤ 指针面：宿主 home 里的指针文件（`<home>/<pointer_file>`）定位、归属与扫描。
//!
//! 指针是"某个宿主接了哪个 system"的唯一跨进程事实：`aimail address` / `stats` /
//! `reset` / `uninstall` / `check` 都靠它建立宿主 ↔ sid 的关联。文件名归契约
//! （[`crate::core::contract::pointer_file`]），形态归宿主（[`Host::pointer_kind`]）：
//! `Root` ⇒ 只有根指针；`RootOrProfiles` ⇒ 根 + `profiles/*/`。

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::core::contract;
use crate::hosts::identity::platform_root;
use crate::hosts::{all, get, Host, PointerKind};

/// 某宿主的全部指针候选路径（`RootOrProfiles` ⇒ 根 + 每个 profile 子目录，排序）。
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

/// 某宿主的**当前**指针路径：根指针存在用根，否则取 `profiles/*` 的第一个；都不存在返回根路径。
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

/// 读指针文件里的 `system_id`（读不到/解析不了 ⇒ `""`）。
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

/// 该宿主指针指向的 sid（无指针 ⇒ `""`）。
pub fn sid_for(user_home: &Path, h: &dyn Host) -> String {
    let ptr = path(user_home, h);
    if ptr.is_file() {
        sid(&ptr)
    } else {
        String::new()
    }
}

/// `sid -> [宿主名]`（按枚举序保序；同一 sid 被多宿主指向时按序累积）。
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

// ── 按宿主名取值的薄封装（调用方手里通常只有 id 字符串）──────────────────

/// [`paths`] 的按名版本（未知宿主 ⇒ 空表）。
pub fn paths_for(user_home: &Path, name: &str) -> Vec<PathBuf> {
    get(name).map(|h| paths(user_home, h)).unwrap_or_default()
}

/// [`path`] 的按名版本（未知宿主 ⇒ 空路径）。
pub fn path_for(user_home: &Path, name: &str) -> PathBuf {
    get(name).map(|h| path(user_home, h)).unwrap_or_default()
}

/// [`sid_for`] 的按名版本（未知宿主 ⇒ `""`）。
pub fn sid_for_name(user_home: &Path, name: &str) -> String {
    get(name).map(|h| sid_for(user_home, h)).unwrap_or_default()
}

/// 指针形态的按名版本（未知宿主 ⇒ `"root"`，与历史缺省一致）。
pub fn kind_of(name: &str) -> &'static str {
    get(name)
        .map(|h| h.pointer_kind().as_str())
        .unwrap_or("root")
}

/// 平台根下扫到的指针：`(agent_name, email, system_id)`。
///
/// 递归、跳过 `.git` / `node_modules` / `.venv`；根级指针对应主 agent（地址基名由 SDK
/// 归一为 `agent`），命名目录用目录名。只认契约常量（不为想象中的旧机器留历史名）。
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
            // 根指针 = 主 agent；地址基名由 SDK 归一为 `agent`（平台私有根名不参与定名）。
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
