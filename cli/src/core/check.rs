//! check 引擎的记录模型与输出（`cli/check_status.py:131-171` 的复刻）。
//!
//! 下游（`repair`、`--json` 消费者）依赖的形状：
//!
//! ```text
//! 记录 = {"level","check","pass","detail","fix"}   ← 键集合精确、pass 必须是 bool
//! JSON = {"all_pass": bool, "timestamp": ISO8601Z, "checks": [记录…]}
//! ```
//!
//! 文本输出按**固定分组顺序**渲染（分组表见 [`GROUPS`]），空分组不打印；
//! 失败项在 `--verbose` 下追加 `→ fix` 行。
//!
//! 纪律：本模块只提供"装记录 + 渲染"。各层探针在各自步骤接入；
//! **`check` 子命令在 L0–L4 全部落完之前保持 not-ported**（半个 check =
//! 漏判级别 ⇒ 假绿，是安全类命令里最危险的失败模式）。

use crate::core::style::{BROWN, CHECK, CROSS, GREEN, NC, RED, YELLOW};
use crate::core::time;
use serde_json::{json, Map, Value};

/// `(level, 分组标题)` —— 顺序即文本输出的分组顺序（`check_status.py:146-155`）。
pub const GROUPS: &[(&str, &str)] = &[
    ("system", "system (check scope)"),
    ("config", "config (config files: gateway/bridge/agent)"),
    ("gateway", "aimail-gateway (external mail gateway)"),
    ("bridge", "aimail-bridge (local NAT traversal bridge)"),
    (
        "runtime",
        "runtime (platform resources: patches/skills/plugin)",
    ),
    ("agent", "agent (platform config + hook interface)"),
    ("agent-gw", "agent-gateway (Hermes gateway)"),
    ("profile", "agent-profile (agent entity)"),
];

/// 一条检查记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub level: String,
    pub check: String,
    pub pass: bool,
    pub detail: String,
    pub fix: String,
}

/// 检查集合。
#[derive(Debug, Default, Clone)]
pub struct Check {
    pub checks: Vec<Record>,
    pub verbose: bool,
}

impl Check {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, level: &str, name: &str, pass: bool, detail: &str, fix: &str) {
        self.checks.push(Record {
            level: level.to_string(),
            check: name.to_string(),
            pass,
            detail: detail.to_string(),
            fix: fix.to_string(),
        });
    }

    pub fn all_pass(&self) -> bool {
        self.checks.iter().all(|c| c.pass)
    }

    /// 文本视图（分组、颜色、可选 fix 行）。
    pub fn print_table(&self) {
        for (level, title) in GROUPS {
            let items: Vec<&Record> = self.checks.iter().filter(|c| c.level == *level).collect();
            if items.is_empty() {
                continue;
            }
            println!("\n  {BROWN}╓─ {title}{NC}");
            for chk in items {
                let icon = if chk.pass {
                    format!("{GREEN}{CHECK}{NC}")
                } else {
                    format!("{RED}{CROSS}{NC}")
                };
                println!("  {icon} {}: {}", chk.check, chk.detail);
                if self.verbose && !chk.pass && !chk.fix.is_empty() {
                    println!("     {YELLOW}→{NC} {}", chk.fix);
                }
            }
        }
    }

    /// JSON 视图（`all_pass` / `timestamp` / `checks`，2 空格缩进，非 ASCII 不转义）。
    pub fn print_json(&self) {
        println!("{}", self.json_string());
    }

    /// 便于测试与复用的 JSON 文本。
    pub fn json_string(&self) -> String {
        let mut root = Map::new();
        root.insert("all_pass".into(), Value::Bool(self.all_pass()));
        root.insert("timestamp".into(), Value::String(time::now_iso8601_z()));
        root.insert(
            "checks".into(),
            Value::Array(
                self.checks
                    .iter()
                    .map(|c| {
                        json!({
                            "level": c.level,
                            "check": c.check,
                            "pass": c.pass,
                            "detail": c.detail,
                            "fix": c.fix,
                        })
                    })
                    .collect(),
            ),
        );
        serde_json::to_string_pretty(&Value::Object(root)).expect("serialize check json")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    #[test]
    fn record_shape_matches_the_pinned_contract() {
        // 对应 Python tests/test_check_contract.py:test_check_record_shape
        let mut c = Check::new();
        c.add("config", "system_home", true, "/home/.hermes", "");
        c.add("agent", "hook", false, "404", "reinstall plugin");
        assert_eq!(c.checks.len(), 2);
        let rec = &c.checks[0];
        assert!(rec.pass);
        assert!(!c.all_pass(), "second record failed");
    }

    #[test]
    fn json_view_has_the_three_root_keys_and_record_keys() {
        // 对应 test_check_json_output
        let mut c = Check::new();
        c.add("bridge", "process", false, "not running", "start_bridge");
        let data: Value = serde_json::from_str(&c.json_string()).expect("valid json");
        assert_eq!(data.get("all_pass").and_then(Value::as_bool), Some(false));
        assert!(data.get("timestamp").and_then(Value::as_str).is_some());
        let rec = &data["checks"][0];
        assert_eq!(rec.get("level").and_then(Value::as_str), Some("bridge"));
        assert_eq!(rec.get("check").and_then(Value::as_str), Some("process"));
        assert_eq!(rec.get("pass").and_then(Value::as_bool), Some(false));
        assert_eq!(rec.get("fix").and_then(Value::as_str), Some("start_bridge"));
        // 键集合精确（多一个键都会破坏下游）
        let keys: Vec<&str> = rec
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(keys, vec!["level", "check", "pass", "detail", "fix"]);
    }

    #[test]
    fn every_production_level_has_a_group() {
        // 对应 test_known_levels_printable：生产用到的 level 都必须落在分组表里
        for lvl in [
            "system", "config", "gateway", "bridge", "runtime", "agent", "agent-gw", "profile",
        ] {
            assert!(
                GROUPS.iter().any(|(l, _)| *l == lvl),
                "level {lvl} 没有分组 ⇒ 文本视图会漏印"
            );
        }
    }
}
