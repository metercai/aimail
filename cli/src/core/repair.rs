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
use crate::core::{config, contract, platforms, sdk};
use serde_json::Value;
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

    /// 执行该步。**仍未移植的步一律 `NotPorted`**（绝不静默当成功）；已落地的走真实现。
    pub fn run(&self, sid: &str, home: &str, _deep: bool) -> StepResult {
        match self {
            // 第 7 步：绑定文件补空 + webhook_url 对齐（写回经 SDK 门）
            Step::AgentmailJson => {
                let ah = crate::core::home::aimail_home();
                if agentmail_backfill(sid, &ah) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // 第 6 步：运行时资源缺失 → 经该平台**自足 SDK 安装入口**幂等重铺
            Step::RuntimeResources => {
                if runtime_resources(sid, home, &crate::core::home::program_root()) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // 第 11 步（--deep）：把卡住的 pending 全部 ack（兜底清理）
            Step::DrainStuck => {
                let client = gateway_client(sid);
                if drain_stuck(&client) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            _ => StepResult::NotPorted,
        }
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

// ════════════════════════════════════════════════════════════════════════════
// S5b-i：CLI 自有权写入步（系统级文件 / 平台指针）—— 归属铁律：CLI 不写绑定文件
// 现状码：`cli/repair.py:415-553`（_auto_platform_home / _sid_has_pointer /
// _repair_gateway_config / _repair_pointer）
// ════════════════════════════════════════════════════════════════════════════

/// `_auto_platform_home`：单平台机器自动定平台根；多平台回退 cfg 的 system_home；否则空
/// （**不猜**）。
pub fn auto_platform_home(user_home: &Path, stored_system_home: Option<&str>) -> String {
    let mut hits: Vec<String> = Vec::new();
    for name in platforms::order() {
        let root = platforms::platform_root(user_home, name);
        if root.exists() && platforms::detect_platform_from_home(&root) == name {
            hits.push(root.to_string_lossy().into_owned());
        }
    }
    if hits.len() == 1 {
        return hits.remove(0);
    }
    if hits.len() > 1 {
        let sh = stored_system_home.unwrap_or("");
        if !sh.is_empty() && Path::new(sh).is_dir() {
            return sh.to_string();
        }
    }
    String::new()
}

/// `_sid_has_pointer(sid)`：任一平台的任一指针（root / root_or_profiles）指向该 sid。
pub fn sid_has_pointer(user_home: &Path, sid: &str) -> bool {
    for name in platforms::order() {
        for ptr in platforms::pointer_paths(user_home, name) {
            if ptr.is_file() {
                if let Ok(text) = std::fs::read_to_string(&ptr) {
                    if let Ok(v) = serde_json::from_str::<Value>(&text) {
                        if v.get("system_id").and_then(Value::as_str) == Some(sid) {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

/// `judge_deliverable(url)` —— 云端能不能 POST 到这个 URL（**话说明白**的判据）。
///
/// 关键：`_detect_webhook_host` 返回的是**裸 host**（如 `127.0.0.1`）⇒ 这里必然判
/// 不可达 ⇒ `repair` 实际**从不写** `webhook_host`（只打告警）。文案逐字照抄
/// `cli/deploy_bridge.py:328-350`。
pub fn judge_deliverable(url: &str) -> (bool, String) {
    let raw = url.trim();
    if raw.is_empty() {
        return (
            false,
            "the URL is empty (pull mode: nothing is registered)".to_string(),
        );
    }
    let bare = || {
        format!(
            "'{raw}' is not an absolute http(s) URL — a bare host or host:port cannot be POSTed to (reqwest: builder error)"
        )
    };
    let Some((scheme, rest)) = raw.split_once("://") else {
        return (false, bare());
    };
    let scheme = scheme.to_lowercase();
    if scheme != "http" && scheme != "https" {
        return (false, bare());
    }
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.contains('@') {
        return (false, format!("'{raw}' carries userinfo"));
    }
    let host = host_of(authority);
    if host.is_empty() {
        return (false, format!("'{raw}' carries no host"));
    }
    if host == "0.0.0.0" || host == "::" {
        return (
            false,
            format!(
                "'{raw}' points at {host} — a bind/unspecified address, not a destination (U2: nothing can deliver there)"
            ),
        );
    }
    if let Some(p) = port_of(authority) {
        if !(1..=65535).contains(&p) {
            return (false, format!("'{raw}' carries an invalid port"));
        }
    }
    (true, String::new())
}

fn host_of(authority: &str) -> String {
    let a = authority.rsplit('@').next().unwrap_or(authority);
    if let Some(rest) = a.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_lowercase();
    }
    a.split(':').next().unwrap_or("").to_lowercase()
}

fn port_of(authority: &str) -> Option<i64> {
    let a = authority.rsplit('@').next().unwrap_or(authority);
    if a.starts_with('[') {
        return a
            .split(']')
            .nth(1)
            .and_then(|r| r.strip_prefix(':'))
            .and_then(|p| p.parse::<i64>().ok());
    }
    a.split(':').nth(1).and_then(|p| p.parse::<i64>().ok())
}

fn url_host(url: &str) -> String {
    let after = url.split("://").nth(1).unwrap_or("");
    let authority = after.split(['/', '?', '#']).next().unwrap_or("");
    host_of(authority)
}

fn is_loopback_host(host: &str) -> bool {
    matches!(host, "127.0.0.1" | "localhost" | "::1" | "ip6-localhost")
}

/// 私网判定（`ipaddress.ip_address(h).is_private` 的常用等价）。
fn is_private_host(host: &str) -> bool {
    if is_loopback_host(host) {
        return true;
    }
    match host.parse::<std::net::IpAddr>() {
        Ok(std::net::IpAddr::V4(v4)) => v4.is_private() || v4.is_link_local(),
        // fc00::/7（唯一本地）与 fe80::/10（链路本地）—— 不用 is_unique_local(1.84 才稳)
        Ok(std::net::IpAddr::V6(v6)) => {
            let head = v6.segments()[0];
            (head & 0xfe00) == 0xfc00 || (head & 0xffc0) == 0xfe80
        }
        Err(_) => false,
    }
}

/// 本机主 LAN IP（UDP connect 取本地端；失败再退 `ip -4 -brief addr`）。
pub fn detect_lan_ip() -> String {
    if let Ok(s) = std::net::UdpSocket::bind("0.0.0.0:0") {
        if s.connect("8.8.8.8:80").is_ok() {
            if let Ok(addr) = s.local_addr() {
                let ip = addr.ip().to_string();
                if !ip.starts_with("0.0.0.0") {
                    return ip;
                }
            }
        }
    }
    if let Ok(out) = std::process::Command::new("ip")
        .args(["-4", "-brief", "addr", "show", "scope", "global"])
        .output()
    {
        if out.status.success() {
            for line in String::from_utf8_lossy(&out.stdout).lines() {
                for p in line.split_whitespace() {
                    if p.contains('/') && p.chars().next().is_some_and(|c| c.is_ascii_digit()) {
                        let ip = p.split('/').next().unwrap_or("");
                        if !ip.starts_with("127.") {
                            return ip.to_string();
                        }
                    }
                }
            }
        }
    }
    String::new()
}

/// `_detect_webhook_host(gateway_url)`：网关回调本机时该用哪个 host
/// （`cli/setup_system.py:209-320`）。返回**裸 host**（外网兜底探测同 Python）。
pub fn detect_webhook_host(gateway_url: &str) -> String {
    let gw_host = url_host(gateway_url);
    if gw_host.is_empty() {
        return "127.0.0.1".to_string();
    }
    let lan_ip = detect_lan_ip();
    if is_loopback_host(&gw_host) {
        return "127.0.0.1".to_string();
    }
    if !lan_ip.is_empty() && gw_host == lan_ip {
        return lan_ip;
    }
    if is_private_host(&gw_host) {
        return if lan_ip.is_empty() {
            "127.0.0.1".to_string()
        } else {
            lan_ip
        };
    }
    use std::net::ToSocketAddrs;
    if let Ok(mut it) = (gw_host.as_str(), 0u16).to_socket_addrs() {
        if let Some(addr) = it.next() {
            let resolved = addr.ip().to_string();
            if is_loopback_host(&resolved) {
                return "127.0.0.1".to_string();
            }
            if !lan_ip.is_empty() && resolved == lan_ip {
                return lan_ip;
            }
            if is_private_host(&resolved) {
                return if lan_ip.is_empty() {
                    "127.0.0.1".to_string()
                } else {
                    lan_ip
                };
            }
        }
    }
    // 公网：外网探测（与 Python 同：https://ifconfig.me, UA curl/7.0）
    if let Ok((code, body)) = crate::core::http::raw_req("https://ifconfig.me", None, None, 5) {
        if code == 200 {
            let ext = body.trim();
            if !ext.is_empty() && !is_private_host(ext) {
                return ext.to_string();
            }
        }
    }
    "127.0.0.1".to_string()
}

fn step_ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}
fn step_warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}
fn step_fail(msg: &str) {
    println!("  {RED}✗{NC} {msg}");
}

/// `_repair_gateway_config`：只**补空**（system_home / webhook_host），绝不覆盖既有值。
///
/// 在**原始 JSON 映射**上改（不经过 typed 结构）：Python 是 dict 变更加 append，
/// 新键落在**末尾**、既有键序不变 ⇒ 这样落盘才能与 Python **字节等价**。
/// 写入面 = CLI 自有权（系统级文件），pretty(2 空格) 无尾换行 + 0600 原子写。
pub fn repair_gateway_config(
    aimail_home: &Path,
    user_home: &Path,
    sid: &str,
    args_home: &str,
) -> bool {
    let gw_path = crate::core::config::gateway_config_path_in(aimail_home, sid);
    if !gw_path.is_file() {
        step_fail(&format!(
            "gateway config does not exist: {}",
            gw_path.display()
        ));
        return false;
    }
    let Ok(text) = std::fs::read_to_string(&gw_path) else {
        return false;
    };
    let Ok(Value::Object(mut cfg)) = serde_json::from_str::<Value>(&text) else {
        return false;
    };
    let get_str = |m: &serde_json::Map<String, Value>, k: &str| -> String {
        m.get(k).and_then(Value::as_str).unwrap_or("").to_string()
    };
    let mut changed = false;
    let stored = {
        let v = get_str(&cfg, "system_home");
        if v.is_empty() {
            None
        } else {
            Some(v)
        }
    };
    let root = if args_home.is_empty() {
        auto_platform_home(user_home, stored.as_deref())
    } else {
        args_home.to_string()
    };
    if get_str(&cfg, "system_home").is_empty() {
        if !root.is_empty() && platforms::detect_platform_from_home(Path::new(&root)) != "unknown" {
            cfg.insert("system_home".to_string(), Value::String(root.clone()));
            step_ok(&format!("system_home backfilled: {root}"));
            changed = true;
        } else {
            step_warn("system_home missing and the platform root cannot be determined (on a multi-platform machine pass --home)");
        }
    }
    if get_str(&cfg, "webhook_host").is_empty() {
        let wh = detect_webhook_host(&get_str(&cfg, "gateway_url"));
        let (deliverable, why) = judge_deliverable(&wh);
        if !wh.is_empty() && deliverable {
            cfg.insert("webhook_host".to_string(), Value::String(wh.clone()));
            step_ok(&format!("webhook_host backfilled: {wh}"));
            changed = true;
        } else if !wh.is_empty() {
            step_warn(&format!(
                "detected callback address '{wh}' is not a deliverable http(s) URL ({why}) — leaving webhook_host unset (no bridge entry; registration keeps the local endpoint)"
            ));
        }
    }
    if changed && crate::core::config::write_private_json(&gw_path, &Value::Object(cfg)).is_err() {
        step_fail(&format!(
            "gateway config write failed: {}",
            gw_path.display()
        ));
        return false;
    }
    changed
}

/// `_repair_pointer`：平台根确定 + 本 sid 无指针 + 目标指针文件不存在 ⇒ 建指针。
pub fn repair_pointer(
    aimail_home: &Path,
    user_home: &Path,
    sid: &str,
    platform_home: &str,
) -> bool {
    if sid_has_pointer(user_home, sid) {
        return false;
    }
    if platform_home.is_empty() {
        return false;
    }
    let plat = platforms::detect_platform_from_home(Path::new(platform_home));
    if plat == "unknown" {
        return false;
    }
    // 绑定枚举（**只读**）：取第一个有 email 的
    let sysdir = crate::core::config::system_dir_in(aimail_home, sid);
    let mut email = String::new();
    let mut subs: Vec<std::path::PathBuf> = std::fs::read_dir(&sysdir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    for sub in subs {
        let aj = sub.join(crate::core::contract::binding_file());
        if !aj.is_file() {
            continue;
        }
        if let Ok(text) = std::fs::read_to_string(&aj) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                if let Some(e) = v.get("email").and_then(Value::as_str) {
                    if !e.is_empty() {
                        email = e.to_string();
                        break;
                    }
                }
            }
        }
    }
    if email.is_empty() {
        return false;
    }
    let ptr = platforms::platform_root(user_home, plat).join(crate::core::contract::pointer_file());
    if ptr.exists() {
        return false; // 已有指针（可能是别的系统）—— 不覆盖
    }
    if let Some(parent) = ptr.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let body = serde_json::json!({"system_id": sid, "email": email});
    let Ok(text) = serde_json::to_string_pretty(&body) else {
        return false;
    };
    if std::fs::write(&ptr, text).is_err() {
        return false;
    }
    let _ = crate::core::perms::set_user_only(&ptr);
    step_ok(&format!("pointer created: {} → {sid}", ptr.display()));
    true
}

/// 与 `aimail_gateway.json` 同义的网关客户端（`repair.py:72-80` 的 `_gateway_client`）。
/// 缺 `gateway_url`/`admin_key` ⇒ None（调用方据此打印"客户端不可用"的提示，而不是抛错）。
pub fn gateway_client(sid: &str) -> Option<crate::core::gateway::GatewayClient> {
    let gw = config::load_gateway_config_in(&crate::core::home::aimail_home(), sid)?;
    let obj = gw.to_json();
    let url = obj.get("gateway_url").and_then(Value::as_str).unwrap_or("");
    let key = obj.get("admin_key").and_then(Value::as_str).unwrap_or("");
    if url.is_empty() || key.is_empty() {
        return None;
    }
    Some(crate::core::gateway::GatewayClient::with_defaults(url, key))
}

/// pending 查询返回体 → `batches` 列表（`repair.py:281`：`pend["batches"]`，退回顶层数组/`data`）。
pub fn pending_batches(pend: &Value) -> Vec<Value> {
    if let Some(Value::Array(a)) = pend.get("batches") {
        return a.clone();
    }
    if let Value::Array(a) = pend {
        return a.clone();
    }
    if let Some(Value::Array(a)) = pend.get("data") {
        return a.clone();
    }
    Vec::new()
}

/// 空签名证据：headers 里既无 `X-Webhook-Signature` 也无 `-V2` 的投递 ⇒ `(id, email)`
/// （`repair.py:282-290`；`id` 可能是 None = --deep 的人造目标）。
pub fn pending_empties(pend: &Value) -> Vec<(Option<String>, String)> {
    let mut out = Vec::new();
    for b in pending_batches(pend) {
        let Some(deliveries) = b.get("deliveries").and_then(Value::as_array) else {
            continue;
        };
        for d in deliveries {
            let hs = d
                .get("headers")
                .and_then(Value::as_str)
                .and_then(|t| serde_json::from_str::<Value>(t).ok())
                .unwrap_or_else(|| serde_json::json!({}));
            let has_sig = hs
                .get("X-Webhook-Signature")
                .map(|v| !v.is_null())
                .unwrap_or(false)
                || hs
                    .get("X-Webhook-Signature-V2")
                    .map(|v| !v.is_null())
                    .unwrap_or(false);
            if !has_sig {
                out.push((
                    d.get("id").map(|v| {
                        v.as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| v.to_string())
                    }),
                    d.get("email")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                ));
            }
        }
    }
    out
}

/// 卡住判定（`repair.py:355-366`）：`created_at` 龄期 > 600s ⇒ 收集 id；时间不可解析 ⇒ 跳过该条。
pub fn stuck_pending_ids(pend: &Value, now: i64) -> Vec<String> {
    let mut stuck = Vec::new();
    for b in pending_batches(pend) {
        let Some(deliveries) = b.get("deliveries").and_then(Value::as_array) else {
            continue;
        };
        for d in deliveries {
            let created = d.get("created_at").and_then(Value::as_str).unwrap_or("");
            let Some(ts) = crate::core::time::parse_rfc3339_secs(created) else {
                continue;
            };
            if now - ts > 600 {
                if let Some(id) = d.get("id") {
                    stuck.push(
                        id.as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| id.to_string()),
                    );
                }
            }
        }
    }
    stuck
}

/// `_drain_stuck`（`repair.py:344-373`，`--deep` 的第 11 步）：把卡住的 pending 全部 ack 兜底。
pub fn drain_stuck(client: &Option<crate::core::gateway::GatewayClient>) -> bool {
    let Some(c) = client else {
        return false;
    };
    let pend = c.post(
        "/api/v1/admin/pending",
        &serde_json::json!({"filter": [], "emails": []}),
    );
    if let Some(err) = pend.get("error") {
        warn(&format!("pending query failed: {err}"));
        return false;
    }
    let stuck = stuck_pending_ids(&pend, crate::core::time::now_secs());
    for id in &stuck {
        let res = c.post(
            "/api/v1/admin/pending/ack",
            &serde_json::json!({"ids": [id]}),
        );
        match res.get("error") {
            Some(e) => warn(&format!("ack {id} failed: {e}")),
            None => ok(&format!("ack stuck pending id={id}")),
        }
    }
    !stuck.is_empty()
}

/// `_detect_platform_from_home`（`repair.py:394-407`）：**repaired 侧的手写判定变体**。
///
/// 与另两处平台判定**不同**（各自照抄，不合并）：hermes 用 `markers 任一` 的 OR；openclaw 看
/// `openclaw.json` 文件；deerflow 看 `backend/app/gateway` 目录。名字是平台语义（注册表只描述
/// 特征，表达不了这里的 OR/文件/目录三种形状）。
fn detect_platform_for_repair(dir: &Path) -> &'static str {
    let name = dir
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    if name == ".pi" && dir.join("agent").is_dir() {
        return "pi";
    }
    if name == ".dsh" && dir.join("profiles").is_dir() && dir.join("storages").is_dir() {
        return "dsh";
    }
    if dir.join("hermes-agent").exists() || dir.join("profiles").is_dir() {
        return "hermes";
    }
    if dir.join("openclaw.json").is_file() {
        return "openclaw";
    }
    if dir.join("backend").join("app").join("gateway").is_dir() {
        return "deerflow";
    }
    "unknown"
}

/// `_failing_file_checks`（`repair.py:570-596`）：注册表里**文件类** health_checks 中"没有命中"的项。
///
/// 三个定位键都必须认（`path` / `alt` / `glob` —— openclaw 的插件目录检查用 glob，漏认它会把
/// "资源在位"误读成"缺失"）。`glob_dir_any` = 目录类，命中即算在位（不做内容匹配）。
fn failing_file_checks(plat: &str, sh: &str, user_home: &Path) -> Vec<Value> {
    const FILE_KINDS: &[&str] = &[
        "file_contains",
        "file_contains_alt",
        "file_exists",
        "file_exists_any",
        "glob_dir_any",
    ];
    let mut fails = Vec::new();
    for ch in platforms::health_checks(plat) {
        let kind = ch.get("kind").and_then(Value::as_str).unwrap_or("");
        if !FILE_KINDS.contains(&kind) {
            continue;
        }
        let pat = ["path", "alt", "glob"]
            .iter()
            .find_map(|k| ch.get(*k).and_then(Value::as_str))
            .unwrap_or("")
            .replace("{home}", sh)
            .replace("{user_home}", &user_home.to_string_lossy());
        let mut ok = false;
        if let Ok(paths) = glob::glob(&pat) {
            for cand in paths.flatten() {
                if kind == "glob_dir_any" {
                    ok = true;
                    break;
                }
                if kind == "file_exists" || kind == "file_exists_any" {
                    if cand.is_file() {
                        ok = true;
                        break;
                    }
                    continue;
                }
                let marker = ch.get("marker").and_then(Value::as_str).unwrap_or("");
                if let Ok(text) = std::fs::read_to_string(&cand) {
                    if text.contains(marker) {
                        ok = true;
                        break;
                    }
                }
            }
        }
        if !ok {
            fails.push(ch);
        }
    }
    fails
}

/// `_repair_runtime_resources`（`repair.py:612-651`）：L2 运行时资源缺失 → 经该平台的**自足 SDK 安装入口**
/// 幂等重铺（`install.py install --type <tgt> --home <sh> --system-id <sid>`）。
///
/// 关键照抄点：① 平台根解析不出 ⇒ 跳过并说明（远端平台如 deerflow 要在宿主上跑）；
/// ② 该平台**没有** sdk_install 入口 ⇒ 只打印注册表自带的 fix 提示，**不**spawn 一个注定失败的安装；
/// ③ 安装输出取尾部 600 字符原样转发；④ 一律**幂等**（资源齐全时返回 false 不动作）。
pub fn runtime_resources(sid: &str, platform_home: &str, prog_root: &Path) -> bool {
    let ah = crate::core::home::aimail_home();
    let gw = config::load_gateway_config_in(&ah, sid)
        .map(|c| c.to_json())
        .unwrap_or_default();
    let sh = if platform_home.is_empty() {
        gw.get("system_home")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        platform_home.to_string()
    };
    if sh.is_empty() || !Path::new(&sh).is_dir() {
        warn("platform root not resolvable, skipping the runtime-resource redeploy (for remote platforms such as deerflow run it on the host)");
        return false;
    }
    let plat = detect_platform_for_repair(Path::new(&sh));
    if plat == "unknown" {
        warn("platform type not recognised (the root is neither a known platform nor a self-built one) -> skipping the runtime-resource redeploy");
        return false;
    }
    let fails = failing_file_checks(plat, &sh, &crate::core::home::user_home());
    if fails.is_empty() {
        ok(&format!("{plat} runtime resources ok"));
        return false;
    }
    let tgt = platforms::sdk_install_target(plat);
    if tgt.is_empty() {
        warn(&format!(
            "{plat} runtime resources missing; this platform has no SDK install entry (its own installer manages them) -> follow the hint:"
        ));
        for ch in &fails {
            let id = ch.get("id").and_then(Value::as_str).unwrap_or("?");
            let txt = ch
                .get("fix")
                .and_then(Value::as_str)
                .or_else(|| ch.get("fail_text").and_then(Value::as_str))
                .unwrap_or("");
            warn(&format!("  [{id}] {txt}"));
        }
        return false;
    }
    warn(&format!(
        "{plat} runtime resources missing -> idempotent reinstall (install.py install --type {tgt} --home {sh})"
    ));
    // 按**文件路径**调用（与每一步一致），不依赖解释器里 `import aimail`；install.py 自己会做 sys.path 自举，
    // 因而仓库(pysdk/) 与 pip(site-packages/aimail/) 两种布局都成立。
    // 按**文件路径**调用（与每一步一致），不依赖解释器里 `import aimail`；install.py 自己会做
    // sys.path 自举，因而仓库(pysdk/) 与 pip(site-packages/aimail/) 两种布局都成立。
    let py = std::env::var("AIMAIL_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let install_py = prog_root
        .join("aimail-src")
        .join("pysdk")
        .join("install.py");
    let mut args: Vec<String> = Vec::new();
    if install_py.is_file() {
        args.push(install_py.to_string_lossy().to_string());
    } else {
        args.push("-m".to_string());
        args.push("aimail.install".to_string());
    }
    args.extend([
        "install".to_string(),
        "--type".to_string(),
        tgt.clone(),
        "--home".to_string(),
        sh.clone(),
        "--system-id".to_string(),
        sid.to_string(),
    ]);
    match sdk::run_program(
        Path::new(&py),
        &args,
        std::time::Duration::from_secs(300),
        &[],
    ) {
        Ok((out, _err, 0)) => {
            let text = String::from_utf8_lossy(&out);
            let tail: String = text
                .chars()
                .rev()
                .take(600)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            print!("{tail}");
            ok("runtime resources reinstalled");
            true
        }
        Ok((_, err, code)) => {
            let text = String::from_utf8_lossy(&err);
            let tail: String = text
                .chars()
                .rev()
                .take(200)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            fail(&format!("reinstall failed (exit {code}): {tail}"));
            false
        }
        Err(e) => {
            fail(&format!("reinstall failed: {}", e.display_like_python()));
            false
        }
    }
}

/// `_repair_agentmail_json`（`repair.py:655-717`）：补绑定文件的可重建字段 + 与**活**路由对齐
/// `webhook_url`；写回**经 SDK 门** `backfill_binding`（per-agent 绑定只由 SDK 写 —— 归属铁律）。
///
/// 照抄点：① 字段补齐以网关配置为准，只补**空**的；② 路由表逐行解析（`k = v`，去引号/逗号，后写覆盖先写）；
/// ③ 对齐条件 = 路由目标存活 + 目标是本机地址 + 声明值与目标不同（存活的声明值不动）；
/// ④ 比对原字典决定是否写（值相等即不写，幂等）；⑤ 单个文件失败只跳过该文件。
pub fn agentmail_backfill(sid: &str, aimail_home: &Path) -> bool {
    agentmail_backfill_with(sid, aimail_home, &crate::core::home::program_root(), &[])
}

/// 同上，但门的**程序根**与**子进程环境**可注入（测试用夹具根 + 夹具 AIMAIL_HOME，
/// 既走"同源"分支又不改本进程环境 ⇒ 并行安全）。
pub fn agentmail_backfill_with(
    sid: &str,
    aimail_home: &Path,
    prog_root: &Path,
    door_env: &[(String, String)],
) -> bool {
    let gw = config::load_gateway_config_in(aimail_home, sid)
        .map(|c| c.to_json())
        .unwrap_or_default();
    let sysdir = config::system_dir_in(aimail_home, sid);
    if !sysdir.is_dir() {
        return false;
    }
    // 路由表（target url）—— k = v 逐行；去引号、去尾逗号；后写覆盖先写
    let mut routes: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    if let Ok(text) = std::fs::read_to_string(aimail_home.join("bridge").join("aimail_routes.toml"))
    {
        for raw in text.lines() {
            let line = raw.trim();
            if line.contains('=') && !line.starts_with('#') {
                let (k, v) = line.split_once('=').unwrap();
                let key = unquote(k.trim());
                let val = unquote(v.trim().trim_end_matches(','));
                routes.insert(key, val);
            }
        }
    }

    let mut dirs: Vec<std::path::PathBuf> = std::fs::read_dir(&sysdir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    let binding_name = contract::binding_file();
    let mut changed = false;
    for dir in dirs {
        let binding = dir.join(binding_name);
        let Ok(text) = std::fs::read_to_string(&binding) else {
            continue;
        };
        let Ok(Value::Object(mut d)) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let orig = d.clone();
        let dir_name = dir
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        // ① 补空字段（网关配置为准）
        for k in [
            "gateway_url",
            "domain",
            "system_id",
            "system_name",
            "manager_address",
        ] {
            let cur_empty = d
                .get(k)
                .and_then(Value::as_str)
                .map(|v| v.is_empty())
                .unwrap_or(true);
            if !cur_empty {
                continue;
            }
            if let Some(v) = gw.get(k) {
                let v_empty = v.as_str().map(|x| x.is_empty()).unwrap_or(v.is_null());
                if !v_empty {
                    d.insert(k.to_string(), v.clone());
                }
            }
        }
        // ② 与活路由对齐 webhook_url
        let email = d
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let target = routes.get(&email).cloned().unwrap_or_default();
        let declared = d
            .get("webhook_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let host = url_host(&target);
        let local = matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1");
        if !target.is_empty() && route_alive(&target) && local {
            if !declared.is_empty() && !route_alive(&declared) {
                d.insert("webhook_url".to_string(), Value::String(target.clone()));
                ok(&format!(
                    "{dir_name}: webhook_url {declared} -> {target} (declared dead, route alive)"
                ));
            } else if !declared.is_empty() && trim_slash(&declared) != trim_slash(&target) {
                d.insert("webhook_url".to_string(), Value::String(target.clone()));
                ok(&format!(
                    "{dir_name}: webhook_url aligned to route target {target}"
                ));
            }
        }
        if d != orig {
            // 写回经 SDK 门（原子 tmp+rename+0600 语义在 SDK 内）；单文件失败只跳过该文件
            let args = serde_json::json!({"binding": Value::Object(d), "system_id": sid});
            match sdk::sdk_ops_call(
                "backfill_binding",
                &args,
                prog_root,
                std::time::Duration::from_secs(120),
                door_env,
            ) {
                Ok(_) => changed = true,
                Err(e) => warn(&format!(
                    "{dir_name}: {binding_name} write failed ({}) -> skipping that file",
                    e.display_like_python()
                )),
            }
        }
    }
    if !changed {
        ok(&format!(
            "{binding_name} ok (fields complete and webhook_url aligned)"
        ));
    }
    changed
}

/// 探测端点是否存活（`repair.py:671-680`）：POST 空体、3s 超时；HTTP 404 = 死，其余状态码 = 活。
fn route_alive(url: &str) -> bool {
    if url.is_empty() || !url.starts_with("http") {
        return false;
    }
    match crate::core::http::raw_req(url, Some(b""), None, 3) {
        Ok((code, _)) => code != 404,
        Err(_) => false,
    }
}

/// 去一层引号（`k = "v",` 这类 toml 简式解析用）。
fn unquote(s: &str) -> String {
    let t = s.trim();
    let t = t.strip_prefix('"').unwrap_or(t);
    let t = t.strip_suffix('"').unwrap_or(t);
    t.trim().to_string()
}

fn trim_slash(url: &str) -> &str {
    url.trim_end_matches('/')
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

    #[test]
    fn sdk_install_target_matches_registry() {
        // 注册表口径：dsh/hermes/deerflow 有自足安装入口；pi/openclaw 资源自己管（无入口）
        assert_eq!(platforms::sdk_install_target("hermes"), "hermes");
        assert_eq!(platforms::sdk_install_target("dsh"), "dsh");
        assert_eq!(platforms::sdk_install_target("deerflow"), "deerflow");
        assert_eq!(platforms::sdk_install_target("pi"), "");
        assert_eq!(platforms::sdk_install_target("openclaw"), "");
        assert_eq!(platforms::sdk_install_target("nope"), "");
    }

    #[test]
    fn detect_platform_for_repair_mirrors_python_variant() {
        let d = tempfile::tempdir().unwrap();
        // hermes 的 OR 语义：只要 profiles/ 在（无 hermes-agent）也算 hermes —— 与注册表驱动那两处不同
        let h = d.path().join(".hermes");
        std::fs::create_dir_all(h.join("profiles")).unwrap();
        assert_eq!(detect_platform_for_repair(&h), "hermes");
        // pi 需 名字 .pi + agent/；dsh 需 名字 .dsh + profiles/ + storages/
        let pi = d.path().join(".pi");
        std::fs::create_dir_all(pi.join("agent")).unwrap();
        assert_eq!(detect_platform_for_repair(&pi), "pi");
        let dsh = d.path().join(".dsh");
        std::fs::create_dir_all(dsh.join("profiles")).unwrap();
        std::fs::create_dir_all(dsh.join("storages")).unwrap();
        assert_eq!(detect_platform_for_repair(&dsh), "dsh");
        // openclaw 看文件；deerflow 看目录
        let oc = d.path().join(".openclaw");
        std::fs::create_dir_all(&oc).unwrap();
        std::fs::write(oc.join("openclaw.json"), "{}").unwrap();
        assert_eq!(detect_platform_for_repair(&oc), "openclaw");
        let df = d.path().join("deer-flow");
        std::fs::create_dir_all(df.join("backend/app/gateway")).unwrap();
        assert_eq!(detect_platform_for_repair(&df), "deerflow");
        assert_eq!(
            detect_platform_for_repair(&d.path().join("nope")),
            "unknown"
        );
    }

    #[test]
    fn failing_file_checks_honours_glob_and_marker() {
        let d = tempfile::tempdir().unwrap();
        let home = d.path().join(".hermes");
        std::fs::create_dir_all(&home).unwrap();
        // 资源全缺 ⇒ hermes 的文件类检查应有命中失败项
        let missing = failing_file_checks("hermes", &home.to_string_lossy(), d.path());
        assert!(!missing.is_empty(), "缺资源时应报出失败项");
        // 注册表自带的 fix 提示必须原样可读（供 repair 打印）
        assert!(missing
            .iter()
            .all(|ch| ch.get("id").is_some() || ch.get("path").is_some()));
    }

    #[test]
    fn runtime_resources_skips_when_root_unresolvable() {
        // 平台根解析不出 ⇒ 明确跳过（不 spawn 安装、不报成功）
        let d = tempfile::tempdir().unwrap();
        let ah = d.path().join("aimail");
        std::fs::create_dir_all(ah.join("systems/s1")).unwrap();
        std::env::set_var("AIMAIL_HOME", &ah);
        let r = runtime_resources("s1", "/nonexistent-platform-root", &ah);
        std::env::remove_var("AIMAIL_HOME");
        assert!(!r);
    }

    #[test]
    fn pending_batches_and_empties_match_python_reads() {
        let pend = serde_json::json!({"batches": [{"deliveries": [
            {"id": "d1", "email": "a@example.test", "headers": "{\"X-Webhook-Signature\": \"sig\"}"},
            {"id": "d2", "email": "b@example.test", "headers": "{}"},
            {"id": "d3", "email": "c@example.test", "headers": "not-json"},
            {"id": "d4", "email": "d@example.test", "headers": "{\"X-Webhook-Signature-V2\": \"v2\"}"}
        ]}]});
        assert_eq!(pending_batches(&pend).len(), 1);
        // 空签名证据 = d2（无签名头）+ d3（headers 非 JSON ⇒ 视作 {} ⇒ 也无签名），d1/d4 有签名
        let empties = pending_empties(&pend);
        let ids: Vec<String> = empties.iter().filter_map(|(i, _)| i.clone()).collect();
        assert_eq!(ids, vec!["d2".to_string(), "d3".to_string()]);
        assert_eq!(empties[0].1, "b@example.test");
        // 顶层数组 / data 数组两种返回形状都要认
        assert_eq!(
            pending_batches(&serde_json::json!([{"deliveries": []}])).len(),
            1
        );
        assert_eq!(
            pending_batches(&serde_json::json!({"data": [{"deliveries": []}]})).len(),
            1
        );
        assert!(pending_batches(&serde_json::json!({"ok": true})).is_empty());
    }

    #[test]
    fn stuck_pending_ids_uses_600s_age_and_skips_unparseable() {
        let now = crate::core::time::parse_rfc3339_secs("2026-10-04T12:00:00Z").unwrap();
        let pend = serde_json::json!({"batches": [{"deliveries": [
            {"id": "old", "created_at": "2026-10-04T11:00:00Z"},   // 3600s 前 ⇒ 卡住
            {"id": "fresh", "created_at": "2026-10-04T11:59:30Z"},  // 30s 前 ⇒ 不卡
            {"id": "bad", "created_at": "not-a-time"}               // 不可解析 ⇒ 跳过（Python 同判）
        ]}]});
        assert_eq!(stuck_pending_ids(&pend, now), vec!["old".to_string()]);
    }

    #[test]
    fn drain_stuck_posts_pending_then_acks_only_stuck() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        listener.set_nonblocking(true).unwrap();
        let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let seen2 = seen.clone();
        let h = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
            let mut served = 0;
            while std::time::Instant::now() < deadline && served < 4 {
                match listener.accept() {
                    Ok((mut sock, _)) => {
                        let mut buf = [0u8; 4096];
                        let n = sock.read(&mut buf).unwrap_or(0);
                        let req = String::from_utf8_lossy(&buf[..n]).to_string();
                        let first = req.lines().next().unwrap_or("").to_string();
                        let body = req.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
                        seen2.lock().unwrap().push(format!("{first} | {body}"));
                        let payload = if first.contains("/pending/ack") {
                            "{\"ok\":true}"
                        } else {
                            "{\"batches\":[{\"deliveries\":[
                                {\"id\":\"old\",\"email\":\"a@example.test\",\"created_at\":\"2000-01-01T00:00:00Z\"},
                                {\"id\":\"fresh\",\"email\":\"b@example.test\",\"created_at\":\"2999-01-01T00:00:00Z\"}
                            ]}]}"
                        };
                        let resp = format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                            payload.len(),
                            payload
                        );
                        let _ = sock.write_all(resp.as_bytes());
                        let _ = sock.flush();
                        served += 1;
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(20));
                    }
                    Err(_) => break,
                }
            }
        });
        let client = crate::core::gateway::GatewayClient::with_defaults(
            &format!("http://127.0.0.1:{port}"),
            "k",
        );
        let changed = drain_stuck(&Some(client));
        h.join().ok();
        assert!(changed, "有卡住的 pending ⇒ 应报告已处理");
        let log = seen.lock().unwrap().clone();
        assert!(
            log[0].starts_with("POST /api/v1/admin/pending ") && log[0].contains("\"filter\":[]"),
            "首个请求应是 pending 查询: {log:?}"
        );
        assert!(
            log.iter()
                .any(|l| l.contains("/pending/ack") && l.contains("\"old\"")),
            "应只 ack 卡住的那条: {log:?}"
        );
        assert!(
            !log.iter().any(|l| l.contains("\"fresh\"")),
            "新鲜的投递不该被 ack: {log:?}"
        );
    }
}
