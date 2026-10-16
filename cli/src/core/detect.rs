//! CLI 检测面 —— 契约 v1.0 §5（owner 2026-10-11 增列）。
//!
//! 平台静态知识**无跨平台数据文件**，按来源三分落位：
//!   · 可推导（本模块的扫描/glob）：指针、agents 容器与多 profile 名字、插件入口的**命中**；
//!   · 常量（本模块的 [`PLATFORMS`]）：home 目录名、宿主特征标记、宿主包管理器安装命令、
//!     **宿主布局模式**（插件入口的宿主相对路径）；
//!   · 宿主自报 / 内部（SDK 门内，本模块不持有）：固定 agent 名、别名、平台私有注册参数。
//!
//! 边界（契约 §1 不变）：探测属"检测/自检"职责；本模块**不实现**平台适配与 SDK 逻辑
//! —— 它只回答"这是哪个宿主 / 东西在哪 / 装了什么"，不回答"怎么适配"。
//!
//! 优先级（契约 §5）：显式 `--platform`（校验，不静默回落）→ 已装组件（入口在位）
//! → 宿主特征标记（未装态兜底）→ `unknown` ⇒ 响亮失败。未装态**只允许**前两法。
//!
//! 候选根 = `[home, home/<平台的 home_dir>]`：调用方按约定给**宿主根**（如 `~/.hermes`），
//! 也可能给它们的父目录（如 `~`）。**只在候选根下按该平台自己的布局模式找入口**
//! ——不做无界深度遍历：否则父目录下任意嵌套的另一个宿主树都会被误claim（实测缺陷）。

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

/// 一个平台的静态知识（常量部分）。`python_host` 为真时 `node_pkg` / `entry_patterns`
/// 为空：python 宿主的 SDK 由 `pip install aimailsdk` 装入，入口是门（零路径）。
pub struct PlatformSpec {
    pub id: &'static str,
    pub home_dir: &'static str,
    pub markers: &'static [&'static str],
    pub install_argv: &'static [&'static str],
    pub node_pkg: &'static str,
    /// 入口在**平台 home 内**的相对路径模式：`*` = 单层任意目录名，`{lib,dist}` = 二者之一。
    pub entry_patterns: &'static [&'static str],
    /// agent 键的**容器目录名**（平台 home 内的相对名）：多 agent 宿主由此派生键；
    /// 空 = 单 agent / 名字在 SDK 门内，CLI 不猜（owner 2026-10-02 C1）。
    pub agents_container: &'static str,
    pub python_host: bool,
}

/// 平台常量表（唯一真源；顺序 = 枚举/帮助顺序）。
pub const PLATFORMS: &[PlatformSpec] = &[
    PlatformSpec {
        id: "pi",
        home_dir: ".pi",
        markers: &["agent"],
        install_argv: &["pi", "install", "npm:pi-aimail"],
        node_pkg: "pi-aimail",
        agents_container: "",
        entry_patterns: &["agent/npm/node_modules/pi-aimail/dist/register-cli.js"],
        python_host: false,
    },
    PlatformSpec {
        id: "dsh",
        home_dir: ".dsh",
        markers: &["profiles"],
        install_argv: &[
            "dsh",
            "plugin",
            "--profile",
            "web",
            "add",
            "dsh-aimail",
            "--config.minimumReleaseAge=0",
        ],
        node_pkg: "dsh-aimail",
        agents_container: "profiles",
        entry_patterns: &["profiles/*/node_modules/dsh-aimail/dist/register-cli.js"],
        python_host: false,
    },
    PlatformSpec {
        id: "hermes",
        home_dir: ".hermes",
        markers: &["hermes-agent"],
        agents_container: "profiles",
        install_argv: &[],
        node_pkg: "",
        entry_patterns: &[],
        python_host: true,
    },
    PlatformSpec {
        id: "openclaw",
        home_dir: ".openclaw",
        markers: &["openclaw.json"],
        install_argv: &[
            "openclaw",
            "plugins",
            "install",
            "openclaw-aimail@latest",
            "--force",
            "--accept-capabilities",
        ],
        node_pkg: "openclaw-aimail",
        agents_container: "agents",
        entry_patterns: &["npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js"],
        python_host: false,
    },
    PlatformSpec {
        id: "deerflow",
        home_dir: ".deer-flow",
        markers: &["backend/app/gateway"],
        agents_container: "",
        install_argv: &[],
        node_pkg: "",
        entry_patterns: &[],
        python_host: true,
    },
];

pub fn spec(id: &str) -> Option<&'static PlatformSpec> {
    PLATFORMS.iter().find(|p| p.id == id)
}

pub fn ids() -> Vec<&'static str> {
    PLATFORMS.iter().map(|p| p.id).collect()
}

/// 由 `home_dir` 推导出的静态信息。
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

/// 候选根：`home` 自身，以及 `home/<平台的 home_dir>`（调用方给父目录时）。
fn candidate_roots(home: &Path, p: &PlatformSpec) -> Vec<PathBuf> {
    let mut roots = vec![home.to_path_buf()];
    let nested = home.join(p.home_dir);
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

fn find_entry(root: &Path, p: &PlatformSpec) -> Option<PathBuf> {
    p.entry_patterns
        .iter()
        .find_map(|pat| match_pattern(root, pat))
}

fn markers_hit(root: &Path, p: &PlatformSpec) -> bool {
    !p.markers.is_empty() && p.markers.iter().all(|m| root.join(m).exists())
}

/// 扫描 agent 键（可推导）：`<root>/profiles/*`（hermes 式）或 `<root>/agents/*`（openclaw 式）。
/// 单 agent 宿主扫不到 ⇒ 空集（定名归 SDK，CLI 不猜）。
fn scan_agent_keys(root: &Path, p: &PlatformSpec) -> Vec<String> {
    if p.agents_container.is_empty() {
        return Vec::new(); // 单 agent 宿主：名字定在 SDK 门内，CLI 不猜
    }
    {
        let dir = root.join(p.agents_container);
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
        if !keys.is_empty() {
            return keys;
        }
    }
    Vec::new()
}

/// 探测静态信息。`explicit` 为 `--platform` 显式值：命中即用（并跑一次自检），
/// 未命中**响亮失败**、不静默回落（契约 §5）。
pub fn detect_static(home: &Path, explicit: Option<&str>) -> Result<StaticInfo, String> {
    let (spec, root) = match explicit {
        Some(id) => match spec(id) {
            Some(s) => (
                s,
                candidate_roots(home, s)
                    .pop()
                    .unwrap_or_else(|| home.to_path_buf()),
            ),
            None => {
                return Err(format!(
                    "--platform '{id}' unknown; valid: {}",
                    ids().join(", ")
                ))
            }
        },
        None => {
            // 已装组件（入口在位）优先于特征标记：入口是"装过"的硬证据。
            let mut by_entry: Option<(&'static PlatformSpec, PathBuf)> = None;
            let mut by_marker: Option<(&'static PlatformSpec, PathBuf)> = None;
            for p in PLATFORMS {
                for r in candidate_roots(home, p) {
                    if by_entry.is_none() && find_entry(&r, p).is_some() {
                        by_entry = Some((p, r));
                        break;
                    }
                    if by_marker.is_none() && markers_hit(&r, p) {
                        by_marker = Some((p, r));
                    }
                }
            }
            match by_entry.or(by_marker) {
                Some((s, r)) => (s, r),
                None => {
                    return Err(format!(
                        "无法确定平台: {} 目录无特征且无 aimail 指针(可用 --platform 显式指定; valid: {})",
                        home.display(),
                        ids().join(", ")
                    ))
                }
            }
        }
    };

    let entry = if spec.entry_patterns.is_empty() {
        None
    } else {
        find_entry(&root, spec)
    };
    let transport = if entry.is_some() {
        "node"
    } else if spec.python_host {
        "python"
    } else {
        ""
    };
    let pointer = {
        // 契约常量单一真源（5 平台同值：`core/contract.rs`）
        let p = root.join(crate::core::contract::pointer_file());
        p.is_file().then_some(p)
    };
    Ok(StaticInfo {
        platform: spec.id.to_string(),
        transport,
        home: root.clone(),
        agent_keys: scan_agent_keys(&root, spec),
        pointer,
        entry,
    })
}

/// `aimail platform spec --json` 的载荷：完整平台注册表（类型化常量的值视图）+（可选的）探测结果。
///
/// P2c S3a（2026-10-16）：导出面从"8 字段检测表"扩为**全形状**（`install_steps`/
/// `uninstall_steps`/`health_checks`/`pointer`/`register`/`aliases`/`ops`/`agents`/
/// `detect` 全键），使本命令成为 advanced 门禁/夹具的**唯一平台知识入口**（取代直读
/// 平台注册表 JSON，S3b 删除）。形状对齐原注册表（`order` +
/// `platforms`{id→对象} + `detected`），改锚面最小。数据 = `platforms::raw()`（由
/// `platforms_data` 类型化常量重建，语义等价由往返棘轮证明）。
pub fn spec_json(home: Option<&Path>, explicit: Option<&str>) -> Value {
    let reg = crate::core::platforms::raw();
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
        assert_eq!(plats.len(), PLATFORMS.len());
        // 全形状导出面：install_steps / health_checks / register / pointer 键在位。
        let dsh = &plats["dsh"];
        assert!(dsh["install_steps"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["kind"] == "register_default"));
        assert!(dsh["health_checks"].as_array().is_some());
        assert_eq!(dsh["register"]["kind"], "node_entry");
        assert_eq!(
            dsh["pointer"]["file"],
            crate::core::contract::pointer_file()
        );
        assert_eq!(v["detected"]["platform"], "dsh");
        assert_eq!(v["detected"]["transport"], ""); // 显式给平台但未装 ⇒ 无入口、非 python 宿主
    }
}
