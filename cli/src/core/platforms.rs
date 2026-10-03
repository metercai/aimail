//! 平台注册表 —— `cli/platforms.json` **内嵌**（owner 2026-10-03 裁决）。
//!
//! 它是 CLI↔SDK 的**映射层**（平台注册表 + 安装动作表，见边界定稿 §1.4 D）；CLI 侧
//! 的平台知识单真源，代码里不得出现平台字面量分支（L0.c 判据同精神）。
//!
//! 内嵌的取舍（owner 已定）：单一二进制自包含、`include_str!` 编译期取源 ⇒ 文件与
//! 二进制不可能漂移；代价是加平台/改动作需要重编译重发版（分发本就如此）。
//! 门禁 `tests/cli/check-registry.py` 校验的仍是仓里那份文件 —— 而
//! `tests/cli/check-registry.py` 校验的就是编译期取源的那一份（见本模块测试）。
//!
//! S2 只暴露目前用得到的访问面（order / 定义 / home_dir / install_steps / raw），
//! 其余键（uninstall_steps / health_checks / register …）留给各自的消费步骤，
//! 避免过早把 schema 冻成结构体。

use crate::core::contract;
use serde_json::Value;
use std::sync::OnceLock;

/// 编译期内嵌的平台注册表（路径相对本文件：`cli/src/core/` → `cli/`）。
const REGISTRY_JSON: &str = include_str!("../../platforms.json");

fn registry() -> &'static Value {
    static R: OnceLock<Value> = OnceLock::new();
    R.get_or_init(|| {
        serde_json::from_str(REGISTRY_JSON).expect("embedded platform registry must be valid JSON")
    })
}

/// 平台枚举顺序（唯一平台知识源；帮助分组/指针扫描序都用它）。
pub fn order() -> Vec<&'static str> {
    registry()
        .get("order")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default()
}

/// 某平台的注册表定义（不存在 ⇒ None）。
pub fn platform(name: &str) -> Option<&'static Value> {
    registry().get("platforms").and_then(|m| m.get(name))
}

/// 平台根目录名（如 `.hermes`）；缺失 ⇒ None（不猜）。
pub fn home_dir(name: &str) -> Option<&'static str> {
    platform(name)
        .and_then(|p| p.get("home_dir"))
        .and_then(Value::as_str)
}

/// 平台安装动作表（`install_steps`）；缺失 ⇒ 空表（= 该平台无 CLI 侧安装动作）。
pub fn install_steps(name: &str) -> &'static [Value] {
    platform(name)
        .and_then(|p| p.get("install_steps"))
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or(&[])
}

/// 整份注册表（未定型的键用它的逃生口，不要在这里加半成品的强类型）。
pub fn raw() -> &'static Value {
    registry()
}

// ── 检测 / 指针（均为注册表表驱动；`cli/aimail:178-243,3055-3075` 的复刻） ──────

/// 按 home 目录特征判平台（注册表 `order` × `detect`；顺序语义保真：pi/dsh 特征先行，
/// 防 `profiles/` 撞车）。识别不出 ⇒ `"unknown"`（不猜）。
pub fn detect_platform_from_home(system_home: &std::path::Path) -> &'static str {
    hits_dir(system_home).unwrap_or("unknown")
}

/// 某个目录本身是否命中某平台（注册表 `detect`：`dir_name` **可选** + `markers` 必需）。
///
/// 这是"目录命中判定"的共享单点：`runtime_core.normalize_platform_home` 的内层 `hits`
/// 与 `check_status._check_l2_runtime` 的内联循环都同构于它。与
/// [`crate::core::checks::adapters::detect_agent_type`] 的判定**不同**（那里 `dir_name`
/// 缺省会回落到 `home_dir`）—— 两边都照抄，不合并。
pub fn hits_dir(d: &std::path::Path) -> Option<&'static str> {
    let name_of = d.file_name().map(|s| s.to_string_lossy().into_owned());
    for name in order() {
        let Some(detect) = platform(name).and_then(|p| p.get("detect")) else {
            continue;
        };
        let want_dir = detect.get("dir_name").and_then(Value::as_str).unwrap_or("");
        let markers: Vec<&str> = detect
            .get("markers")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        if markers.is_empty() {
            continue;
        }
        if !want_dir.is_empty() && name_of.as_deref() != Some(want_dir) {
            continue;
        }
        if markers.iter().all(|m| d.join(m).exists()) {
            return Some(name);
        }
    }
    None
}

/// `runtime_core.normalize_platform_home`：把 `--home` 归一成**平台根**。
///
/// 1. 给定目录本身命中 → 原样返回；
/// 2. 否则其下恰有一个子目录命中 → 返回该子目录；
/// 3. 都不中 → 原样返回（让下游按原逻辑报错，不静默改语义）。
///
/// 返回值一律绝对路径（2026-09-25 G2：相对 `--home` 会被原样写进 `system_home`
/// ⇒ 之后换 cwd 跑命令时平台 home 解析漂移）。此处不用 `std::path::absolute`
/// （1.79 才稳定，本 crate 的 MSRV 更低），改用 `current_dir` 手工绝对化。
pub fn normalize_platform_home(home: &std::path::Path) -> std::path::PathBuf {
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
    let mut subs: Vec<std::path::PathBuf> = std::fs::read_dir(&p)
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

/// 平台根目录（`<user_home>/<home_dir>`）；注册表缺 `home_dir` ⇒ `.<name>`（Python 同默认）。
pub fn platform_root(user_home: &std::path::Path, name: &str) -> std::path::PathBuf {
    let dir = home_dir(name)
        .map(str::to_string)
        .unwrap_or_else(|| format!(".{name}"));
    user_home.join(dir)
}

/// 平台指针文件名（注册表 `pointer.file`；缺省 = 契约常量，不在本处复制字面量）。
pub fn pointer_file_for(name: &str) -> &'static str {
    platform(name)
        .and_then(|p| p.get("pointer"))
        .and_then(|p| p.get("file"))
        .and_then(Value::as_str)
        .unwrap_or_else(|| contract::pointer_file())
}

/// 平台指针形态（注册表 `pointer.kind`；缺省 `root`）。
pub fn pointer_kind(name: &str) -> &'static str {
    platform(name)
        .and_then(|p| p.get("pointer"))
        .and_then(|p| p.get("kind"))
        .and_then(Value::as_str)
        .unwrap_or("root")
}

/// 平台指针路径。`kind=root_or_profiles`（hermes）时：根指针存在用根，否则取
/// `profiles/*/<指针文件>` 的第一个；都不存在返回根路径（Python 同语义）。
pub fn pointer_path(user_home: &std::path::Path, name: &str) -> std::path::PathBuf {
    let kind = pointer_kind(name);
    let root_ptr = platform_root(user_home, name).join(pointer_file_for(name));
    if kind != "root_or_profiles" {
        return root_ptr;
    }
    if root_ptr.is_file() {
        return root_ptr;
    }
    let profiles = platform_root(user_home, name).join("profiles");
    let mut candidates: Vec<std::path::PathBuf> = std::fs::read_dir(&profiles)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path().join(pointer_file_for(name)))
        .filter(|p| p.is_file())
        .collect();
    candidates.sort();
    candidates.into_iter().next().unwrap_or(root_ptr)
}

/// 读指针文件里的 `system_id`（读不到/解析不了 ⇒ `""`）。
pub fn pointer_sid(ptr: &std::path::Path) -> String {
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

/// 该平台指针指向的 sid（无指针 ⇒ `""`）。
pub fn pointer_sid_for(user_home: &std::path::Path, name: &str) -> String {
    let ptr = pointer_path(user_home, name);
    if ptr.is_file() {
        pointer_sid(&ptr)
    } else {
        String::new()
    }
}

/// `sid -> [平台名]`（按注册表 order 保序；同一 sid 被多平台指向时按序累积）。
pub fn all_pointer_sids(user_home: &std::path::Path) -> Vec<(String, Vec<&'static str>)> {
    let mut out: Vec<(String, Vec<&'static str>)> = Vec::new();
    for name in order() {
        let sid = pointer_sid_for(user_home, name);
        if sid.is_empty() {
            continue;
        }
        match out.iter_mut().find(|(s, _)| *s == sid) {
            Some((_, v)) => v.push(name),
            None => out.push((sid, vec![name])),
        }
    }
    out
}

/// 平台运行时健康检查项（注册表 `health_checks`，空 ⇒ 空表）—— L2r 表驱动执行。
pub fn health_checks(name: &str) -> Vec<Value> {
    platform(name)
        .and_then(|p| p.get("health_checks"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

/// 宿主配置里可能引用运行时载荷的文件模板（注册表 `payload_refs`，空 ⇒ 无需检查）。
pub fn payload_refs(name: &str) -> Vec<String> {
    platform(name)
        .and_then(|p| p.get("payload_refs"))
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// 平台别名表（注册表 `aliases`；空 ⇒ 空表，不猜）。
pub fn aliases(name: &str) -> Vec<String> {
    platform(name)
        .and_then(|p| p.get("aliases"))
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// 全部平台的别名（按注册表 `order` 保序）—— 仅供地址表头的"默认映射"行。
pub fn all_aliases() -> Vec<(String, Vec<String>)> {
    order()
        .into_iter()
        .map(|n| (n.to_string(), aliases(n)))
        .collect()
}

/// 平台 agent 标识全集（注册表 `agents.kind` 驱动：
/// `fixed` / `fixed_with_pointer` / `glob_dir` / `profiles_plus_default`）。
/// `cli/aimail:1355-1381` 的复刻。
pub fn agent_names(root: &std::path::Path, name: &str) -> Vec<String> {
    let mut out = Vec::new();
    if !root.is_dir() {
        return out;
    }
    let Some(def) = platform(name) else {
        return out;
    };
    let block = def.get("agents");
    let kind = block
        .and_then(|a| a.get("kind"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let list_of = |key: &str| -> Vec<String> {
        block
            .and_then(|a| a.get(key))
            .and_then(Value::as_array)
            .map(|v| {
                v.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default()
    };
    let ptr_present = root.join(pointer_file_for(name)).exists();
    match kind {
        "fixed" => out.extend(list_of("names")),
        "fixed_with_pointer" => {
            if ptr_present {
                out.extend(list_of("names"));
            }
        }
        "glob_dir" => {
            let dir = root.join(
                block
                    .and_then(|a| a.get("dir"))
                    .and_then(Value::as_str)
                    .unwrap_or("agents"),
            );
            if dir.is_dir() {
                out.extend(sorted_subdir_names(&dir));
            } else if ptr_present {
                let al = aliases(name);
                out.extend(if al.is_empty() {
                    vec!["main".to_string()]
                } else {
                    al
                });
            }
        }
        "profiles_plus_default" => {
            let dir = root.join(
                block
                    .and_then(|a| a.get("profiles"))
                    .and_then(Value::as_str)
                    .unwrap_or("profiles"),
            );
            if dir.is_dir() {
                out.extend(sorted_subdir_names(&dir));
            }
            if ptr_present {
                out.extend(aliases(name));
            }
        }
        _ => {}
    }
    out
}

fn sorted_subdir_names(dir: &std::path::Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .flatten()
        .filter(|e| e.path().is_dir())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// 平台根下扫到的注册指针：`(agent_name, email, system_id)`。
///
/// `cli/aimail:1480-1514` 的复刻（递归、跳过 `.git`/`node_modules`/`.venv`；
/// 根级指针对应的 agent 名取注册表别名首个、无名则 `agent`，命名目录用目录名）。
///
/// **有意差异**：Python 还认历史名 `.aimail`（改名前的指针名）。按 owner 2026-09-07
/// 裁决「开发态零安装存量 ⇒ 兼容/迁移代码都是多余适配」，Rust 侧只认契约常量
/// （`contract::pointer_file()`）与绑定文件名；不为想象中的旧机器写路径。
pub fn scan_pointers(root: &std::path::Path) -> Vec<(String, String, String)> {
    let mut found = Vec::new();
    if !root.is_dir() {
        return found;
    }
    let names = [contract::pointer_file(), contract::binding_file()];
    walk_pointers(root, root, &names, &mut found);
    found.sort();
    found
}

fn walk_pointers(
    dir: &std::path::Path,
    root: &std::path::Path,
    names: &[&str],
    out: &mut Vec<(String, String, String)>,
) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut dirs: Vec<std::path::PathBuf> = Vec::new();
    let mut files: Vec<std::path::PathBuf> = Vec::new();
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
        let Ok(value) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let email = value
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let sid = value
            .get("system_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if email.is_empty() || sid.is_empty() {
            continue;
        }
        let agent_name = if dir == root {
            let plat = detect_platform_from_home(root);
            let al = aliases(plat);
            al.first().cloned().unwrap_or_else(|| "agent".to_string())
        } else {
            dir.file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default()
        };
        out.push((agent_name, email, sid));
    }
    for d in dirs {
        walk_pointers(&d, root, names, out);
    }
}

/// 平台根存在**且**特征识别为该平台（`cli/aimail:3071-3075` 同判据）。
pub fn platform_root_exists(user_home: &std::path::Path, name: &str) -> bool {
    let root = platform_root(user_home, name);
    if !root.exists() {
        return false;
    }
    detect_platform_from_home(&root) == name
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_order_is_consistent_with_definitions() {
        let order = order();
        assert!(!order.is_empty(), "registry order must not be empty");
        let defs = raw()
            .get("platforms")
            .and_then(Value::as_object)
            .expect("platforms map");
        assert_eq!(
            order.len(),
            defs.len(),
            "order and platforms disagree: {order:?} vs {defs:?}"
        );
        for name in &order {
            assert!(
                defs.contains_key(*name),
                "order entry without definition: {name}"
            );
            assert!(
                home_dir(name).is_some_and(|h| h.starts_with('.')),
                "platform {name} lacks a home_dir"
            );
        }
    }

    #[test]
    fn registry_step_tables_are_arrays() {
        for name in order() {
            let steps = install_steps(name);
            for step in steps {
                assert!(
                    step.is_object(),
                    "install step of {name} is not an object: {step}"
                );
                assert!(
                    step.get("kind").is_some(),
                    "install step of {name} lacks kind"
                );
            }
        }
    }

    #[test]
    fn embedded_registry_equals_repo_file() {
        // 内嵌 = 编译期取源；这条断言锁"取的就是仓里那份文件"（路径漂了就红）。
        // 门禁 tests/cli/check-registry.py 校验的同一份文件 ⇒ 门禁与二进制不会各说各话。
        let on_disk = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("platforms.json"),
        )
        .expect("cli/platforms.json readable");
        assert_eq!(
            on_disk, REGISTRY_JSON,
            "embedded registry drifted from cli/platforms.json"
        );
    }

    #[test]
    fn missing_platform_is_none_not_a_guess() {
        assert!(platform("definitely-not-a-platform").is_none());
        assert!(home_dir("definitely-not-a-platform").is_none());
        assert!(install_steps("definitely-not-a-platform").is_empty());
    }
}
