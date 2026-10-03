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
    let Some(dir_name) = system_home
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
    else {
        return "unknown";
    };
    for name in order() {
        let Some(def) = platform(name) else { continue };
        let detect = def.get("detect");
        let want_dir = detect
            .and_then(|d| d.get("dir_name"))
            .and_then(Value::as_str)
            .unwrap_or("");
        if !want_dir.is_empty() && dir_name != want_dir {
            continue;
        }
        let markers: Vec<&str> = detect
            .and_then(|d| d.get("markers"))
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        if markers.iter().all(|m| system_home.join(m).exists()) {
            return name;
        }
    }
    "unknown"
}

/// 平台根目录（`<user_home>/<home_dir>`）；注册表缺 `home_dir` ⇒ `.<name>`（Python 同默认）。
pub fn platform_root(user_home: &std::path::Path, name: &str) -> std::path::PathBuf {
    let dir = home_dir(name)
        .map(str::to_string)
        .unwrap_or_else(|| format!(".{name}"));
    user_home.join(dir)
}

/// 平台指针文件名（注册表 `pointer.file`；缺省 = 契约常量，不在本处复制字面量）。
fn pointer_file_for(name: &str) -> &'static str {
    platform(name)
        .and_then(|p| p.get("pointer"))
        .and_then(|p| p.get("file"))
        .and_then(Value::as_str)
        .unwrap_or_else(|| contract::pointer_file())
}

/// 平台指针路径。`kind=root_or_profiles`（hermes）时：根指针存在用根，否则取
/// `profiles/*/<指针文件>` 的第一个；都不存在返回根路径（Python 同语义）。
pub fn pointer_path(user_home: &std::path::Path, name: &str) -> std::path::PathBuf {
    let kind = platform(name)
        .and_then(|p| p.get("pointer"))
        .and_then(|p| p.get("kind"))
        .and_then(Value::as_str)
        .unwrap_or("root");
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
