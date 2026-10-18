//! ① 身份面：宿主识别特征 + 语言类型 + 指针形态。
//! 判定算法（Strict/Scan/Repair 三档）在 P2 迁入本模块。

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// agent 系统的实现语言（由 `register.kind` 派生，取代分散的字符串比较）。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lang {
    Node,
    Python,
}

/// 指针形态：根指针 / 根 + `profiles/*`。
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

/// 序列化成注册表里的形状 `{"kind": "..."}`（spec 输出逐字节保真）。
impl Serialize for PointerKind {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(1))?;
        m.serialize_entry("kind", self.as_str())?;
        m.end()
    }
}

/// 平台识别特征：`dir_name`（可选目录名闸）+ `markers`（必命中）。
#[derive(Serialize, Clone, Copy)]
pub struct Detect {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dir_name: Option<&'static str>,
    pub markers: &'static [&'static str],
}

use std::path::{Path, PathBuf};

use crate::hosts::{all, get, order, Host};

/// 三档判定语义。差异是历史契约，**禁止合并**：
///
/// * `Strict` —— basename 必须等于 `dir_name`（若声明）+ 全部 markers 命中。
///   install / reset / uninstall 用它回答"这个 `--home` 是哪个平台"。
/// * `Scan` —— `dir_name` 缺省回落 `home_dir`，`--agent-home` 直接判该平台。
///   扫 `user_home` 找"这台机器上装了哪些 agent"。
/// * `Repair` —— basename 闸（若声明）+ markers **任一**（外加宿主私有 `repair_hints`）。
///   修的时候宁认不弃：`.hermes` 里有 `profiles/` 也算 hermes 的线索。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum DetectMode {
    Strict,
    Scan,
    Repair,
}

/// `Strict`：目录本身命中哪个平台（`dir_name` 可选 + `markers` 全命中）。
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

/// `Strict`：`home` 的判定结果（识别不出 ⇒ `"unknown"`，不猜）。
pub fn detect_platform_from_home(home: &Path) -> &'static str {
    hits_dir(home).unwrap_or("unknown")
}

/// `Scan`：按"平台根候选"判定（原 `checks::adapters::detect_agent_type`）。
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

/// `Repair`：宽松判定（原 `repair::detect_platform_for_repair`）。
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

fn expand(p: &Path) -> PathBuf {
    let s = p.to_string_lossy();
    if let Some(rest) = s.strip_prefix("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    p.to_path_buf()
}

/// 把 `--home` 归一成**平台根**：自身命中 ⇒ 原样；恰有一个子目录命中 ⇒ 该子目录；
/// 都不中 ⇒ 原样（让下游按原逻辑报错，不静默改语义）。返回值一律绝对路径。
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

/// 平台根目录（`<user_home>/<home_dir>`）。
pub fn platform_root(user_home: &Path, name: &str) -> PathBuf {
    let dir = get(name).map(|h| h.home_dir()).unwrap_or("");
    if dir.is_empty() {
        user_home.join(format!(".{name}"))
    } else {
        user_home.join(dir)
    }
}

/// 平台根存在**且**特征识别为该平台。
pub fn platform_root_exists(user_home: &Path, name: &str) -> bool {
    let root = platform_root(user_home, name);
    if !root.exists() {
        return false;
    }
    detect_platform_from_home(&root) == name
}

/// `--platform` 显式覆盖的取值校验：空 ⇒ `Ok(None)`；未知 ⇒ `Err(文案)`（不静默回落）。
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

/// 平台解析：home 特征优先，回退"第一个有指针指向某 system 的宿主"；都没有 ⇒ `""`。
///
/// 与 [`detect_platform_from_home`] 的区别：后者无命中返回 `"unknown"`（**非空**），
/// 直接当平台名会把"没识别出来"当成平台 ⇒ 必须走本函数收成 `""`。
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

    /// 反例（历史事故）：`.hermes` 里也有 `profiles/`，Strict 必须靠 basename 闸
    /// 拒掉 dsh，否则 install 会把 hermes home 判成 dsh。
    #[test]
    fn strict_basename_gate_keeps_hermes_out_of_dsh() {
        let t = tmp("id-strict");
        let home = t.path().join(".hermes");
        std::fs::create_dir_all(home.join("profiles")).unwrap();
        std::fs::create_dir_all(home.join("hermes-agent")).unwrap();
        assert_eq!(detect_platform_from_home(&home), "hermes");
    }

    /// Strict：pi/dsh 的 basename 闸生效（同特征换目录名 ⇒ 不认）。
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

    /// Scan：扫 user_home 认平台；`--agent-home` 语义归 hermes。
    #[test]
    fn scan_mode_finds_hosts_under_user_home_and_honors_agent_home() {
        let t = tmp("id-scan");
        let uh = t.path();
        std::fs::create_dir_all(uh.join(".hermes/hermes-agent")).unwrap();
        assert_eq!(detect_agent_type(uh, None, false), "hermes");
        assert_eq!(detect_agent_type(uh, None, true), "hermes");
    }

    /// Repair：宽松语义 —— 只有 `profiles/` 的 `.hermes` 也认（Strict 认不出）。
    #[test]
    fn repair_mode_is_lenient_where_strict_is_not() {
        let t = tmp("id-repair");
        let home = t.path().join(".hermes");
        std::fs::create_dir_all(home.join("profiles")).unwrap();
        assert_eq!(detect_platform_from_home(&home), "unknown");
        assert_eq!(detect_for_repair(&home), "hermes");
    }

    /// 平台根归一：给父目录且恰有一个子目录命中 ⇒ 返回该子目录；不认识 ⇒ 原样。
    #[test]
    fn normalize_picks_the_single_recognized_child() {
        let t = tmp("id-normalize");
        let root = t.path().join("machine");
        std::fs::create_dir_all(root.join("openclaw").join("x")).unwrap();
        std::fs::write(root.join("openclaw/openclaw.json"), "{}").unwrap();
        // basename 不是 .openclaw ⇒ Strict 不认（openclaw 无 dir_name 闸，但目录名随意）
        let got = normalize_platform_home(&root);
        assert_eq!(got, root.join("openclaw"));
        assert_eq!(detect_platform_from_home(&got), "openclaw");
    }

    /// `--platform` 取值校验：空 ⇒ None；合法 ⇒ 原样；未知 ⇒ 文案带全部合法值（升序）。
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
