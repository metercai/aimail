//! repair 引擎 —— `cli/repair.py` 的复刻（plan v3 S5，分切片落地）。
//!
//! 本切片（S5a）落**框架与判读**：
//! - `REPAIRABILITY` 表（检查维度 → 可修性分类：auto/hint + 阶梯步号 + needs）
//!   —— 与 `cli/repair.py:851-897` 逐条对齐，并有一条**表一致性守门**测试直接向
//!   Python 要真值比对（表漂移即红）；
//! - `classify_residual`：复检残余 → （本地可修的缺陷, 需要管理员/宿主的提示）；
//! - 阶梯（`ladder`）的描述与顺序（含 `--deep` 追加的 drain 步）；
//! - `run`：repair 的编排骨架（打印头 → 跑 check（进程内复用引擎，零逻辑重复）
//!   → 列失败项 → 建阶梯 → dry-run 只列计划 → 否则逐步执行 → 复检 → 残余分类 → rc）。
//!
//! **阶梯的十个写面动作在 S5b 落地**：`Step::run` 目前一律返回
//! `Step::NotPorted(...)`，由编排计入 `ladder_bad` 并打印未移植说明 —— 不是静默通过。
//! 也因此 `repair` 命令面**仍未接线**（写面残缺的 repair 会"报修好但没修"）。

use crate::core::check::{Check, Record};
use crate::core::style::{NC, RED, YELLOW};
use std::path::Path;

/// 可修性分类（`repair.py:903-905` 的返回结构）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Repairability {
    /// 本机可自动修（`kind=auto`），`step` = 阶梯步号（仅用于文档/排序参考）。
    Auto { step: u8 },
    /// 只能给提示：需要宿主/服务端/管理员动作。
    Hint { why: Option<&'static str> },
}

impl Repairability {
    pub fn is_auto(&self) -> bool {
        matches!(self, Repairability::Auto { .. })
    }
}

use Repairability::{Auto, Hint};

/// 表一致性守门：本表与 `cli/repair.py:851-897` 必须逐条一致
/// （`cli/tests/repair_parity.rs` 直接向 Python 要真值比对）。
pub const REPAIRABILITY: &[((&str, &str), Repairability)] = &[
    (("config", "complete"), Auto { step: 4 }),
    (("config", "gateway_json"), Auto { step: 4 }),
    (("config", "system_home"), Auto { step: 4 }),
    (("config", "pointer"), Auto { step: 5 }),
    (("gateway", "config"), Auto { step: 4 }),
    (("gateway", "smtp_port"), Auto { step: 4 }),
    (
        ("gateway", "health"),
        Hint {
            why: Some("the target gateway is down/unreachable -> start or fix it on the host side"),
        },
    ),
    (
        ("gateway", "api_key"),
        Hint {
            why: Some(
                "admin key missing or invalid -> re-install (aimail install) or have an administrator issue one",
            ),
        },
    ),
    (("bridge", "process"), Auto { step: 1 }),
    (("bridge", "config"), Auto { step: 1 }),
    (("bridge", "config-complete"), Auto { step: 1 }),
    (("bridge", "config_consistency"), Auto { step: 1 }),
    (("bridge", "config-mode"), Auto { step: 1 }),
    (("bridge", "activity"), Auto { step: 1 }),
    (("bridge", "self_health"), Auto { step: 1 }),
    (
        ("bridge", "pull_path"),
        Hint {
            why: Some(
                "bridge cannot poll the gateway pending API -> the gateway must be reachable (host side); a missing config entry is fixed by pull-entry",
            ),
        },
    ),
    (("bridge", "pull-entry"), Auto { step: 9 }),
    (("bridge", "routes-entry"), Auto { step: 8 }),
    (
        ("bridge", "routes-target"),
        Hint {
            why: Some(
                "the target agent endpoint is down / the address is unreachable -> start that agent on the host side",
            ),
        },
    ),
    (("agent", "config"), Auto { step: 7 }),
    (("agent", "config-json"), Auto { step: 7 }),
    (("agent", "config-consistency"), Auto { step: 7 }),
    (("agent", "name_apikey"), Auto { step: 7 }),
    (
        ("agent", "config-complete"),
        Hint {
            why: Some(
                "fields such as system_name are authoritative server-side (no local source) -> re-register with aimail install, or let an administrator handle it",
            ),
        },
    ),
    (("agent", "pointer"), Auto { step: 5 }),
    (
        ("agent", "hook"),
        Auto { step: 6 },
    ),
    (
        ("agent", "skill"),
        Auto { step: 6 },
    ),
    (
        ("agent", "toolset"),
        Auto { step: 6 },
    ),
    (("agent", "webhook"), Auto { step: 3 }),
    (
        ("agent", "discovery"),
        Hint {
            why: Some(
                "the platform has no record of this agent yet -> create/register it with the platform's own command",
            ),
        },
    ),
    (
        ("agent", "register"),
        Hint {
            why: Some(
                "the address is not registered in the cloud -> aimail install / the registration chain, or an administrator",
            ),
        },
    ),
    (
        ("agent", "session"),
        Hint {
            why: Some("the agent session/endpoint is down -> start that agent on the host side"),
        },
    ),
    (("runtime", "mcp-payload"), Auto { step: 6 }),
    (("runtime", "board-resources"), Auto { step: 6 }),
    (("runtime", "host-payload-refs"), Auto { step: 6 }),
    (("runtime", "platform-locatable"), Auto { step: 5 }),
    (("system", "id"), Auto { step: 4 }),
];

/// 未登记维度的兜底（`_UNREGISTERED`）：当 HINT 处理，并**显式**说明这不是"已覆盖"。
pub const UNREGISTERED: Repairability = Hint {
    why: Some(
        "unregistered check dimension -> treated as needing admin action (please report it to the maintainer)",
    ),
};

/// `repairability(level, name)`。
pub fn repairability(level: &str, name: &str) -> Repairability {
    REPAIRABILITY
        .iter()
        .find(|((l, n), _)| *l == level && *n == name)
        .map(|(_, v)| *v)
        .unwrap_or(UNREGISTERED)
}

/// `classify_residual`：复检残余 → （本地可修的缺陷, 需要管理员/宿主的提示）。
/// 顺序保真（Python 是单次遍历，两列表各自保持原顺序）。
pub fn classify_residual(residual: &[Record]) -> (Vec<Record>, Vec<Record>) {
    let mut defects = Vec::new();
    let mut hints = Vec::new();
    for c in residual {
        if repairability(&c.level, &c.check).is_auto() {
            defects.push(c.clone());
        } else {
            hints.push(c.clone());
        }
    }
    (defects, hints)
}

/// 阶梯一步（描述 + 动作）。描述文本是 dry-run 计划的一部分 ⇒ 逐字照抄。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    BridgeAlive,
    RefreshRoutes,
    WebhookPairing,
    GatewayConfig,
    Pointer,
    RuntimeResources,
    McpPayload,
    AgentmailJson,
    RoutesEntries,
    PullEntryKey,
    DrainStuck,
}

impl Step {
    /// 该步的 dry-run 描述（`repair.py:980-1006` 逐字；含契约值的用常量拼，不抄字面量）。
    pub fn desc(&self) -> String {
        match self {
            Step::BridgeAlive => {
                "bridge alive (remote gateway only; start it idempotently when dead)".to_string()
            }
            Step::RefreshRoutes => {
                "refresh bridge routes (file + admin API hot reload)".to_string()
            }
            Step::WebhookPairing => {
                "repair the gateway webhook pairing (evidence-driven; --deep rewrites directly)"
                    .to_string()
            }
            Step::GatewayConfig => {
                "backfill the gateway config (system_home/webhook_host: fill gaps only)".to_string()
            }
            Step::Pointer => {
                "recreate the platform pointer (only when the platform root is certain and the pointer is missing)"
                    .to_string()
            }
            Step::RuntimeResources => {
                "redeploy runtime resources (patch marker/resources missing -> idempotent reinstall)"
                    .to_string()
            }
            Step::McpPayload => {
                "refresh the runtime payload (mcp missing/stale -> idempotent local bundle install)"
                    .to_string()
            }
            // 描述里含绑定文件名 —— 用**契约常量**拼（不抄字面量）
            Step::AgentmailJson => format!(
                "fill gaps in {} + align webhook_url with the live route",
                crate::core::contract::binding_file()
            ),
            Step::RoutesEntries => {
                "complete the bridge routes entries (missing -> refresh)".to_string()
            }
            Step::PullEntryKey => {
                "align the bridge pull entry admin_key with gateway.json".to_string()
            }
            Step::DrainStuck => "drain stuck pending (--deep)".to_string(),
        }
    }

    /// 执行该步。S5b 之前一律"未移植"（**绝不**静默当成功）。
    pub fn run(&self, _sid: &str, _home: &str, _deep: bool) -> StepResult {
        StepResult::NotPorted
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepResult {
    /// 有实际动作（或幂等确认）。
    Fixed,
    /// 明确未做（不计为失败，但必须打印）。
    NothingToDo,
    /// 本切片尚未移植（计为 ladder_bad，打印原因）。
    NotPorted,
}

/// 阶梯（`--deep` 追加 drain 步）。
pub fn ladder(deep: bool) -> Vec<Step> {
    let mut plan = vec![
        Step::BridgeAlive,
        Step::RefreshRoutes,
        Step::WebhookPairing,
        Step::GatewayConfig,
        Step::Pointer,
        Step::RuntimeResources,
        Step::McpPayload,
        Step::AgentmailJson,
        Step::RoutesEntries,
        Step::PullEntryKey,
    ];
    if deep {
        plan.push(Step::DrainStuck);
    }
    plan
}

fn warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}
fn fail(msg: &str) {
    println!("  {RED}✗{NC} {msg}");
}
fn ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}
use crate::core::style::GREEN;

/// 复检/首检的输出尾部（Python `_check_output_tail`：head 240 / tail 600）。
/// 进程内调用没有子进程 stdout；这里用记录摘要代替，保证"不可判读"时**有证据可看**。
fn checks_tail(records: &[Record]) -> String {
    if records.is_empty() {
        return "(no check output)".to_string();
    }
    let line = |c: &Record| format!("{}:{}", c.level, c.check);
    let head: Vec<String> = records.iter().take(6).map(line).collect();
    if records.len() <= 12 {
        head.join(", ")
    } else {
        let tail: Vec<String> = records
            .iter()
            .rev()
            .take(6)
            .map(line)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        format!("{}, … , {}", head.join(", "), tail.join(", "))
    }
}

/// repair 的表头行（dry-run 计划与测试都按它比对）。
pub fn header_line(sid: &str, deep: bool, dry_run: bool) -> String {
    let mut header = format!("  repair system={sid}");
    if dry_run {
        header.push_str(" [dry-run]");
    }
    if deep {
        header.push_str(" [deep]");
    }
    header
}

/// dry-run 计划行（`    - {desc}`）—— 单点保证"打印的"与"比对的"是同一份数据。
pub fn dry_run_plan_lines(deep: bool) -> Vec<String> {
    ladder(deep)
        .iter()
        .map(|s| format!("    - {}", s.desc()))
        .collect()
}

/// `repair(sid, deep, dry_run, home)` 的编排（check 走**进程内**引擎，零逻辑重复）。
///
/// `check_engine` 由命令层传入（cmd::check 的编排函数），这样 repair 与 check 用的是
/// 同一份引擎实现 —— Python 侧用"子进程 + 解析 JSON"达到同样目的。
pub fn run(
    sid: &str,
    deep: bool,
    dry_run: bool,
    home: &str,
    check_engine: impl Fn(&str, Option<&Path>) -> Check,
) -> i32 {
    println!("{}", header_line(sid, deep, dry_run));

    let agent_home = if home.is_empty() {
        None
    } else {
        Some(Path::new(home))
    };
    let first = check_engine(sid, agent_home);
    let records: Vec<Record> = first.checks.clone();
    // 空 check = GAP（不可判读），不是"通过"（repair 方案A 2026-09-29）
    let passed = !records.is_empty() && records.iter().all(|r| r.pass);
    if records.is_empty() {
        warn("check produced no output -- the check step is unjudgeable in this form (NOT a system_id verdict); repair continues with its unconditional ladder");
        warn(&format!(
            "    check subprocess output: {}",
            checks_tail(&records)
        ));
    }
    if passed {
        ok("check is all green, nothing to repair");
        return 0;
    }
    for c in records.iter().filter(|r| !r.pass) {
        let detail: String = c.detail.chars().take(90).collect();
        warn(&format!("check ✗ {}/{}: {}", c.level, c.check, detail));
    }

    let plan = ladder(deep);
    if dry_run {
        println!("  [dry-run] plan:");
        for line in dry_run_plan_lines(deep) {
            println!("{line}");
        }
        return 0;
    }

    let mut ladder_bad = 0;
    for step in &plan {
        println!("\n  ── {} ──", step.desc());
        match step.run(sid, home, deep) {
            StepResult::Fixed | StepResult::NothingToDo => {}
            StepResult::NotPorted => {
                fail(&format!(
                    "repair action not ported yet: {} (plan v3 S5b)",
                    step.desc()
                ));
                ladder_bad += 1;
            }
        }
    }

    println!("\n  -- re-check --");
    let second = check_engine(sid, agent_home);
    let still: Vec<Record> = second.checks.iter().filter(|r| !r.pass).cloned().collect();
    let passed2 = !second.checks.is_empty() && second.checks.iter().all(|r| r.pass);
    if second.checks.is_empty() {
        warn("check produced no output on re-check -- re-check is UNJUDGEABLE (empty output is not all green)");
        warn(&format!(
            "    check subprocess output: {}",
            checks_tail(&second.checks)
        ));
        if ladder_bad > 0 {
            fail(&format!(
                "-> re-check unjudgeable and the ladder reported {ladder_bad} failed step(s): rc=1"
            ));
            return 1;
        }
        warn(
            "-> re-check unjudgeable: rc is the ladder's own result only, NOT an all-green verdict",
        );
        return 0;
    }
    if passed2 {
        ok("re-check is all green");
        return 0;
    }
    let (defects, hints) = classify_residual(&still);
    for c in &defects {
        let detail: String = c.detail.chars().take(90).collect();
        warn(&format!(
            "[D locally fixable, still failing] {}/{}: {}",
            c.level, c.check, detail
        ));
    }
    for c in &hints {
        let detail: String = c.detail.chars().take(80).collect();
        warn(&format!(
            "[H needs admin/host action] {}/{}: {}",
            c.level, c.check, detail
        ));
        if let Repairability::Hint { why: Some(why) } = repairability(&c.level, &c.check) {
            warn(&format!("    → {why}"));
        }
    }
    warn(&format!(
        "re-check not all green: {} locally-fixable defect(s) / {} needing admin action",
        defects.len(),
        hints.len()
    ));
    if !defects.is_empty() {
        warn("-> locally-fixable items are still failing (defects): report them to the maintainer with the log above");
    } else {
        warn("-> the remaining items are not reliably self-fixable (repair only hints; a system administrator acts)");
    }
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(level: &str, check: &str, pass: bool) -> Record {
        Record {
            level: level.to_string(),
            check: check.to_string(),
            pass,
            detail: String::new(),
            fix: String::new(),
        }
    }

    #[test]
    fn unregistered_dimension_is_hint_with_explicit_note() {
        let r = repairability("nope", "nope");
        assert_eq!(r, UNREGISTERED);
        assert!(!r.is_auto());
    }

    #[test]
    fn residual_splits_auto_from_hint() {
        let residual = vec![
            rec("gateway", "health", false), // hint
            rec("config", "pointer", false), // auto
            rec("agent", "hook", false),     // auto
            rec("agent", "register", false), // hint
        ];
        let (defects, hints) = classify_residual(&residual);
        let names = |v: &Vec<Record>| v.iter().map(|c| c.check.clone()).collect::<Vec<_>>();
        assert_eq!(names(&defects), vec!["pointer", "hook"]);
        assert_eq!(names(&hints), vec!["health", "register"]);
    }

    #[test]
    fn ladder_order_matches_python_and_deep_appends_drain() {
        let base = ladder(false);
        assert_eq!(base.len(), 10);
        assert_eq!(base[0], Step::BridgeAlive);
        assert_eq!(base[9], Step::PullEntryKey);
        let deep = ladder(true);
        assert_eq!(deep.len(), 11);
        assert_eq!(deep[10], Step::DrainStuck);
    }
}
