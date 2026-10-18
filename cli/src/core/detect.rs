//! CLI 检测面 —— 契约 v1.0 §5（owner 2026-10-11 增列）。
//!
//! **平台知识的唯一真源 = `crate::hosts`**（每个宿主一个 `HostDef`）。本模块只做
//! "给定 home 回答这是哪个宿主 / 东西在哪 / 装了什么"，数据一律从宿主表派生：
//!   · 特征标记 ← `HostDef.detect.markers`            · 落点 ← `HostDef.home_dir`
//!   · 插件入口 ← `register.node_path`（node 宿主）   · 语言 ← `HostDef.lang()`
//!   · agent 键容器 ← `HostDef.agents_container`
//!
//! 本模块**不实现**平台适配与 SDK 逻辑（那是 SDK 的事），也不持有任何平台常量。
//!
//! 优先级（契约 §5）：显式 `--platform`（校验，不静默回落）→ 已装组件（入口在位）
//! → 宿主特征标记（未装态兜底）→ `unknown` ⇒ 响亮失败。未装态**只允许**前两法。
//!
//! 候选根 = `[home, home/<home_dir>]`：调用方按约定给**宿主根**（如 `~/.hermes`），
//! 也可能给它们的父目录（如 `~`）。**只在候选根下按该宿主自己的布局找入口**
//! ——不做无界深度遍历：否则父目录下任意嵌套的另一个宿主树都会被误 claim（实测缺陷）。

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::hosts::exec::Register;
use crate::hosts::{all, order, Host, HostDef, Lang};

/// 探测结果（`platform spec --json` 的 `detected` 段）。
#[derive(Debug, Clone)]
pub struct StaticInfo {
    pub platform: String,
    /// `"node"`（平台注册器入口在位）| `"python"`（python 宿主，走门）| `""`（未知/未装）。
    pub transport: &'static str,
    /// 命中的候选根（平台 home）。
    pub home: PathBuf,
    /// 扫描所得 agent 键（多 agent 宿主）；单 agent 宿主为空 ⇒ 由 SDK 取默认名。
    pub agent_keys: Vec<String>,
    pub pointer: Option<PathBuf>,
    /// node 宿主：`<pkg>/dist/register-cli.js`（按宿主布局模式命中；owner 2026-10-11：三包同目录 `dist/`）。
    pub entry: Option<PathBuf>,
}

impl StaticInfo {
    pub fn to_json(&self) -> Value {
        json!({
            "platform": self.platform,
            "transport": self.transport,
            "home": self.home.display().to_string(),
            "agent_keys": self.agent_keys,
            "pointer": self.pointer.as_ref().map(|p| p.display().to_string()),
            "entry": self.entry.as_ref().map(|p| p.display().to_string()),
        })
    }
}

/// 候选根：`home` 自身，以及 `home/<home_dir>`（调用方给父目录时）。
fn candidate_roots(home: &Path, h: &HostDef) -> Vec<PathBuf> {
    let mut roots = vec![home.to_path_buf()];
    let nested = home.join(h.home_dir);
    if nested.is_dir() {
        roots.push(nested);
    }
    roots
}

fn expand_alts(seg: &str) -> Vec<String> {
    let t = seg.trim();
    if t.starts_with('{') && t.ends_with('}') {
        t[1..t.len() - 1]
            .split(',')
            .map(|s| s.trim().to_string())
            .collect()
    } else {
        vec![seg.to_string()]
    }
}

/// 按模式（`*` 单层通配 + `{a,b}` 备选）在 `root` 下找文件。
fn match_pattern(root: &Path, pattern: &str) -> Option<PathBuf> {
    let segs: Vec<&str> = pattern.split('/').collect();
    fn walk(dir: &Path, segs: &[&str]) -> Option<PathBuf> {
        let Some((seg, rest)) = segs.split_first() else {
            return dir.is_file().then(|| dir.to_path_buf());
        };
        if *seg == "*" {
            let rd = std::fs::read_dir(dir).ok()?;
            let mut names: Vec<PathBuf> = rd
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.is_dir())
                .collect();
            names.sort();
            for n in names {
                if let Some(hit) = walk(&n, rest) {
                    return Some(hit);
                }
            }
            return None;
        }
        for alt in expand_alts(seg) {
            if let Some(hit) = walk(&dir.join(alt), rest) {
                return Some(hit);
            }
        }
        None
    }
    walk(root, &segs)
}

/// node 宿主的入口模板（`register.node_path`，`{home}` 相对宿主根）；python 宿主 ⇒ None。
fn entry_pattern(h: &HostDef) -> Option<&'static str> {
    match h.register {
        Register::NodeEntry { node_path, .. } => Some(node_path),
        _ => None,
    }
}

fn find_entry(root: &Path, h: &HostDef) -> Option<PathBuf> {
    let pat = entry_pattern(h)?;
    let rel = pat.strip_prefix("{home}/").unwrap_or(pat);
    match_pattern(root, rel)
}

fn markers_hit(root: &Path, h: &HostDef) -> bool {
    let det = h.detect;
    !det.markers.is_empty() && det.markers.iter().all(|m| root.join(m).exists())
}

/// 扫描 agent 键（可推导）：`<root>/profiles/*`（hermes/dsh）或 `<root>/agents/*`（openclaw）。
/// 单 agent 宿主扫不到 ⇒ 空集（定名归 SDK，CLI 不猜）。
fn scan_agent_keys(root: &Path, h: &HostDef) -> Vec<String> {
    if h.agents_container.is_empty() {
        return Vec::new();
    }
    let dir = root.join(h.agents_container);
    if !dir.is_dir() {
        return Vec::new();
    }
    let mut keys: Vec<String> = std::fs::read_dir(&dir)
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.path().is_dir())
                .filter_map(|e| e.file_name().to_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    keys.sort();
    keys
}

/// 探测静态信息。`explicit` 为 `--platform` 显式值：命中即用（并跑一次自检），
/// 未命中**响亮失败**、不静默回落（契约 §5）。
pub fn detect_static(home: &Path, explicit: Option<&str>) -> Result<StaticInfo, String> {
    let (h, root) = match explicit {
        Some(id) => match crate::hosts::get(id) {
            Some(h) => (
                h,
                candidate_roots(home, h)
                    .pop()
                    .unwrap_or_else(|| home.to_path_buf()),
            ),
            None => {
                return Err(format!(
                    "--platform '{id}' unknown; valid: {}",
                    order().join(", ")
                ))
            }
        },
        None => {
            // 已装组件（入口在位）优先于特征标记：入口是"装过"的硬证据。
            let mut by_entry: Option<(&'static HostDef, PathBuf)> = None;
            let mut by_marker: Option<(&'static HostDef, PathBuf)> = None;
            for p in all() {
                for r in candidate_roots(home, p) {
                    if by_entry.is_none() && find_entry(&r, p).is_some() {
                        by_entry = Some((*p, r));
                        break;
                    }
                    if by_marker.is_none() && markers_hit(&r, p) {
                        by_marker = Some((*p, r));
                    }
                }
            }
            match by_entry.or(by_marker) {
                Some((p, r)) => (p, r),
                None => {
                    return Err(format!(
                        "无法确定平台: {} 目录无特征且无 aimail 指针(可用 --platform 显式指定; valid: {})",
                        home.display(),
                        order().join(", ")
                    ))
                }
            }
        }
    };

    let entry = find_entry(&root, h);
    let transport = if entry.is_some() {
        "node"
    } else if matches!(h.lang(), Lang::Python) {
        "python"
    } else {
        ""
    };
    let pointer = {
        // 契约常量单一真源：文件名取 `contract::pointer_file()`
        let p = root.join(crate::core::contract::pointer_file());
        p.is_file().then_some(p)
    };
    Ok(StaticInfo {
        platform: h.id.to_string(),
        transport,
        home: root.clone(),
        agent_keys: scan_agent_keys(&root, h),
        pointer,
        entry,
    })
}

/// `aimail platform spec --json` 的载荷：宿主表的值视图 +（可选）探测结果。
/// 形状 = `order` + `platforms`{id→对象} + `detected`；高级门禁/夹具以本命令为唯一入口。
pub fn spec_json(home: Option<&Path>, explicit: Option<&str>) -> Value {
    let reg = crate::hosts::describe_all();
    let detected = match home {
        Some(h) => match detect_static(h, explicit) {
            Ok(info) => info.to_json(),
            Err(msg) => json!({ "error": msg }),
        },
        None => Value::Null,
    };
    json!({
        "order": reg.get("order").cloned().unwrap_or_else(|| json!([])),
        "platforms": reg.get("platforms").cloned().unwrap_or_else(|| json!({})),
        "detected": detected,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn touch(path: &Path) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, "{}").unwrap();
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aimail-detect-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn explicit_platform_unknown_fails_loudly() {
        let h = tmp("unknown");
        let err = detect_static(&h, Some("nope")).unwrap_err();
        assert!(err.contains("--platform 'nope' unknown"), "{err}");
        assert!(err.contains("hermes"), "valid 列表应列出平台: {err}");
    }

    #[test]
    fn explicit_platform_targets_nested_home_when_parent_given() {
        let h = tmp("explicit-nested");
        let nested = h.join(".hermes");
        std::fs::create_dir_all(nested.join("profiles")).unwrap();
        let info = detect_static(&h, Some("hermes")).unwrap();
        assert_eq!(info.platform, "hermes");
        assert_eq!(info.home, nested, "给父目录时应落到 <parent>/.hermes");
    }

    #[test]
    fn markers_probe_identifies_host_without_registry() {
        let h = tmp("markers");
        std::fs::create_dir_all(h.join("hermes-agent")).unwrap();
        let info = detect_static(&h, None).unwrap();
        assert_eq!(info.platform, "hermes");
        assert_eq!(info.transport, "python"); // 无 node 入口 ⇒ python 宿主走门
    }

    #[test]
    fn markers_all_required_not_any() {
        let h = tmp("markers-all");
        std::fs::create_dir_all(h.join("backend")).unwrap(); // 只命中 deerflow 标记的前半段
        let err = detect_static(&h, None).unwrap_err();
        assert!(err.contains("无法确定平台"), "{err}");
    }

    #[test]
    fn installed_node_entry_wins_over_markers_and_is_derived_by_layout_pattern() {
        let h = tmp("entry");
        std::fs::create_dir_all(h.join("openclaw.json")).ok();
        // dsh 布局: profiles/<profile>/node_modules/dsh-aimail/dist/register-cli.js
        touch(&h.join("profiles/web/node_modules/dsh-aimail/dist/register-cli.js"));
        let info = detect_static(&h, None).unwrap();
        assert_eq!(info.platform, "dsh", "已装组件优先于特征标记");
        assert_eq!(info.transport, "node");
        assert!(info
            .entry
            .unwrap()
            .ends_with("node_modules/dsh-aimail/dist/register-cli.js"));
        assert_eq!(info.agent_keys, vec!["web".to_string()]);
    }

    #[test]
    fn nested_foreign_home_is_not_claimed() {
        // 实测缺陷回归：给 /tmp(其下嵌套另一个宿主树)不得把 /tmp 当成 dsh。
        let outer = tmp("outer");
        touch(&outer.join("inner/profiles/web/node_modules/dsh-aimail/dist/register-cli.js"));
        let err = detect_static(&outer, None).unwrap_err();
        assert!(
            err.contains("无法确定平台"),
            "父目录下的陌生树不得被 claim: {err}"
        );
    }

    #[test]
    fn parent_dir_with_nested_own_home_is_detected() {
        // 调用方给父目录(~ 式)：候选根含 <parent>/.dsh ⇒ 命中
        let parent = tmp("parent");
        touch(&parent.join(".dsh/profiles/web/node_modules/dsh-aimail/dist/register-cli.js"));
        let info = detect_static(&parent, None).unwrap();
        assert_eq!(info.platform, "dsh");
        assert_eq!(info.home, parent.join(".dsh"));
        assert!(info.entry.unwrap().ends_with("dist/register-cli.js"));
    }

    #[test]
    fn single_agent_host_leaves_agent_keys_empty() {
        let h = tmp("single");
        touch(&h.join("agent/npm/node_modules/pi-aimail/dist/register-cli.js"));
        let info = detect_static(&h, None).unwrap();
        assert_eq!(info.platform, "pi");
        assert_eq!(info.transport, "node");
        assert!(
            info.agent_keys.is_empty(),
            "单 agent 宿主不猜名字（定名归 SDK）"
        );
    }

    #[test]
    fn spec_json_carries_full_registry_and_detection() {
        let h = tmp("specjson");
        std::fs::create_dir_all(h.join("profiles")).ok();
        let v = spec_json(Some(&h), Some("dsh"));
        // S3a 后形状 = 原平台注册表（order + platforms{id→对象}）+ detected。
        let order = v["order"].as_array().unwrap();
        let ids: Vec<&str> = order.iter().map(|p| p.as_str().unwrap()).collect();
        assert_eq!(ids, vec!["pi", "dsh", "hermes", "openclaw", "deerflow"]);
        let plats = v["platforms"].as_object().unwrap();
        assert_eq!(plats.len(), crate::hosts::ALL.len());
        // 全形状导出面：install_steps / health_checks / register / pointer 键在位。
        let dsh = &plats["dsh"];
        assert!(dsh["install_steps"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["kind"] == "register_default"));
        assert!(dsh["health_checks"].as_array().is_some());
        assert_eq!(dsh["register"]["kind"], "node_entry");
        // pointer.file 归契约单源（`contract::pointer_file()`），注册表只留 kind。
        assert_eq!(dsh["pointer"]["kind"], "root");
        assert!(dsh["pointer"].get("file").is_none());
        assert_eq!(v["detected"]["platform"], "dsh");
        assert_eq!(v["detected"]["transport"], ""); // 显式给平台但未装 ⇒ 无入口、非 python 宿主
    }
}
