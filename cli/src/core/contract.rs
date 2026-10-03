//! agent 侧契约常量 —— Rust 侧**编译期内嵌单真源**。
//!
//! 真源 = 仓根 `contract/aimail-contract.json`；`include_str!` 在**编译期**把它嵌进
//! 二进制，运行时不再读文件。
//!
//! 为什么是这个形态（与 `pysdk/aimail_contract.py` 的手写常量不同）：
//! - 发布物自包含：pip/捆绑形态运行时没有仓根 `contract/` 目录 ⇒ 两侧都必须"值在
//!   产物里"。Python 侧靠手写常量 + 门禁断言；Rust 侧可以让**编译器**做这件事；
//! - 于是 Rust 源码里**零契约字面量** ⇒ `tests/contract/check-contract-single-source.py`
//!   的字面量棘轮 rule (b)（"新文件里出现契约字面量 = 新增位置 = 违约"）天然不受影响，
//!   也无需给该检查器增开 Rust 消费方条目（rule (a) 只比对 py/ts 手写常量）。
//!
//! 用法：只经下面的访问器取值，不要在别处再写契约字面量。

use serde_json::Value;
use std::sync::OnceLock;

/// 编译期内嵌的契约清单（路径相对本文件：`cli/src/core/` → 仓根）。
const MANIFEST_JSON: &str = include_str!("../../../contract/aimail-contract.json");

fn manifest() -> &'static Value {
    static M: OnceLock<Value> = OnceLock::new();
    M.get_or_init(|| {
        serde_json::from_str(MANIFEST_JSON).expect("embedded contract manifest must be valid JSON")
    })
}

fn str_key(key: &str) -> &'static str {
    manifest()
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("contract manifest missing string key: {key}"))
}

/// 四个平台（openclaw / dsh / pi / deer-flow）的固定入站路径。
pub fn inbound_path() -> &'static str {
    str_key("inbound_path")
}

/// hermes 的入站路径（网关自身 webhook 路由，唯一例外）。
pub fn hermes_inbound_path() -> &'static str {
    str_key("hermes_inbound_path")
}

/// hermes 网关路由名（webhook_subscriptions.json 的 route 键）。
pub fn hermes_route_name() -> &'static str {
    str_key("hermes_route_name")
}

/// agent 侧 skill 注册名（宿主按 `/<name>` 查表）。
pub fn agent_skill_name() -> &'static str {
    str_key("agent_skill_name")
}

/// agent 侧 toolset 注册名。
pub fn agent_toolset_name() -> &'static str {
    str_key("agent_toolset_name")
}

/// per-agent 绑定文件名（写权在 SDK；CLI 只读 —— 见 [`crate::core::config`]）。
pub fn binding_file() -> &'static str {
    str_key("binding_file")
}

/// 平台指针文件名。
pub fn pointer_file() -> &'static str {
    str_key("pointer_file")
}

/// 平台固定入站端口（dsh / pi / deer-flow）。
pub fn inbound_port(platform: &str) -> Option<u64> {
    manifest()
        .get("inbound_ports")
        .and_then(|m| m.get(platform))
        .and_then(Value::as_u64)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 必需键必须齐、且为非空字符串/非空对象（故意**不**断言具体字面量值：
    /// 值来自清单本身，测试里写死就等于制造第二份真相）。
    #[test]
    fn manifest_keys_exist_and_are_usable() {
        for (name, v) in [
            ("inbound_path", inbound_path()),
            ("hermes_inbound_path", hermes_inbound_path()),
            ("hermes_route_name", hermes_route_name()),
            ("agent_skill_name", agent_skill_name()),
            ("agent_toolset_name", agent_toolset_name()),
            ("binding_file", binding_file()),
            ("pointer_file", pointer_file()),
        ] {
            assert!(!v.trim().is_empty(), "{name} must be non-empty");
        }
        for p in ["dsh", "pi", "deerflow"] {
            let port = inbound_port(p).unwrap_or_else(|| panic!("missing port for {p}"));
            assert!(port > 0 && port < 65536, "{p} port out of range: {port}");
        }
    }

    #[test]
    fn manifest_is_a_single_compiled_copy() {
        // 内嵌值 == 直接从磁盘读同一文件（编译期取源 ⇒ 二者只能相等；
        // 这条断言的价值是"编译期真的取了仓根那份"，路径写错时立刻红）。
        let on_disk = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../contract/aimail-contract.json"),
        )
        .expect("repo-root contract manifest readable from cli/");
        assert_eq!(
            on_disk, MANIFEST_JSON,
            "embedded copy drifted from repo manifest"
        );
    }
}
