//! clap 命令树 + 分发（S1：命令面注册 + 诚实出口）。
//!
//! 命令面是**冻结面**（边界定稿 §1）：15 个顶层子命令名 + `prompt` 的 5 个嵌套
//! 子命令 + install 的两个隐藏机器面参数（`--system-only` / `--payload …`）。
//! 冻结判据由 `tests/test_cli_parity.py` 与 Python 侧 `--help` 做集合比对。
//!
//! S1 只实现 `version`；其余一律 `not_yet_ported`（rc 1），Python CLI 仍是权威。
//! 帮助文本**逐字**取自 Python 侧 argparse（`cli/aimail:3462-3653`），改一处要
//! 两边同步 —— 差异由 parity 测试的子命令/选项集合断言兜住。

use clap::{Arg, ArgAction, Command};

/// 各子命令共用的两条帮助文案（Python 侧 `common_sid` / `common_home`，逐字一致）。
const SID_HELP: &str = "target system id (default: the only installed system)";
const HOME_HELP: &str = "platform root (auto-resolved on single-platform machines)";

const DESCRIPTION: &str =
    "aimail CLI - set up, operate and maintain the AIMail link between your agent platform and the aimail-gateway.";

const EPILOG: &str = "scenario groups:
  setup      machine prep and platform integration: install / uninstall / reset
  operate    daily status, lifecycle, exams and flows: stats / renew / version / check / repair / ping / welcome
  resources  system-scoped resources: domain / address / bridge";

/// 顶层 15 个子命令 —— 顺序与 Python 侧 `add_parser` 声明顺序一致（帮助分组顺序）。
const SUBCOMMANDS: &[(&str, &str)] = &[
    (
        "install",
        "integrate an agent platform with an AIMail system (activate or reuse)",
    ),
    (
        "uninstall",
        "remove aimail integration from the agent platform",
    ),
    (
        "reset",
        "re-reset connection config for an activated system (no re-activation)",
    ),
    (
        "stats",
        "local integration status: systems, agents, mail stats",
    ),
    (
        "renew",
        "renew a system with a product code, or show expiry read-only",
    ),
    ("version", "show CLI/bootstrap version (upgrade detection)"),
    (
        "check",
        "full health exam: config files, runtime resources, delivery links",
    ),
    (
        "repair",
        "auto-fix per check findings, then re-check (idempotent)",
    ),
    (
        "ping",
        "end-to-end ping-pong delivery test (manager <-> agent)",
    ),
    (
        "welcome",
        "welcome-email end-to-end test (API mode by default)",
    ),
    (
        "persona",
        "[merged into 'welcome' 2026-09-22] prints a pointer and exits 2",
    ),
    ("domain", "list or create domains owned by a system"),
    (
        "address",
        "view/maintain system agent addresses (default name / set-name / set-manager)",
    ),
    (
        "prompt",
        "maintain agent prompt rules: subject/body/sender/recipient → role file",
    ),
    (
        "bridge",
        "local bridge: status / refresh routes / restart / upgrade",
    ),
];

/// `prompt` 的嵌套子命令（Python `cli/aimail:3621-3647`）。
const PROMPT_SUBCOMMANDS: &[(&str, &str)] = &[
    (
        "add",
        "add one rule (name = {serial}_{filename}, serial 10-99)",
    ),
    ("list", "list this agent's rules in evaluation order"),
    ("rm", "remove one rule by -n name"),
    (
        "test",
        "dry-run the chain against a sample mail (never sends)",
    ),
    ("create-file", "scaffold role_prompt/<filename>.md for -n"),
];

/// 机器面隐藏参数（边界定稿 §1：仅 `--system-only` 与 `--payload …` 属反调 ABI；
/// 其余是 install 的隐藏开关）。隐藏 = clap `hide(true)`，人面 help 不得出现。
fn install_hidden_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-only")
            .long("system-only")
            .action(ArgAction::SetTrue)
            .hide(true),
    )
    .arg(
        Arg::new("payload")
            .long("payload")
            .value_parser(["install", "dir", "resource", "source"])
            .hide(true),
    )
    .arg(Arg::new("payload_name").hide(true))
    .arg(Arg::new("dest").long("dest").hide(true))
    .arg(Arg::new("source-root").long("source-root").hide(true))
    .arg(
        Arg::new("force")
            .long("force")
            .action(ArgAction::SetTrue)
            .hide(true),
    )
}

/// `address` 上的隐藏开关（2026-09-28 入站 live/down 通知，Python `:3601/:3603`）。
fn address_hidden_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("inbound-live")
            .long("inbound-live")
            .action(ArgAction::SetTrue)
            .hide(true),
    )
    .arg(
        Arg::new("inbound-down")
            .long("inbound-down")
            .action(ArgAction::SetTrue)
            .hide(true),
    )
}

/// `stats` 的参数（Python `cli/aimail:3531-3534`）。
fn stats_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("all")
            .short('a')
            .long("all")
            .action(ArgAction::SetTrue)
            .help("full view: per-system health tags + broken systems + local platforms"),
    )
}

/// `persona` 的参数（Python `cli/aimail:3577-3581`）—— 参数只为命令面保真，
/// 该命令是"指路壳"（rc 2），不读任何参数。
fn persona_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help(SID_HELP),
    )
    .arg(
        Arg::new("manager")
            .short('m')
            .long("manager")
            .help("manager address (approvals contact); default: config/env"),
    )
    .arg(
        Arg::new("no-wait")
            .short('w')
            .long("no-wait")
            .action(ArgAction::SetTrue)
            .help("do not wait for the draft reply"),
    )
}

/// `check` 的参数（Python `cli/aimail:3547-3552`；`--json` 是内部开关，不在命令面）。
fn check_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help(SID_HELP),
    )
    .arg(Arg::new("home").short('H').long("home").help(HOME_HELP))
    .arg(
        Arg::new("verbose")
            .short('v')
            .long("verbose")
            .action(ArgAction::SetTrue)
            .help("show fix suggestions for failing checks"),
    )
}

/// `domain` 的参数（Python `cli/aimail:3584-3589`）。
fn domain_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help(SID_HELP),
    )
    .arg(
        Arg::new("add")
            .short('a')
            .long("add")
            .value_name("DOMAIN")
            .help("create a domain (bare domain, lowercase; default: list only)"),
    )
    .arg(
        Arg::new("id")
            .long("id")
            .help("domain record id (default: auto-generated)"),
    )
    .arg(
        Arg::new("webhook-url")
            .short('w')
            .long("webhook-url")
            .help("webhook receiver URL for the new domain"),
    )
}

/// `address` 的参数（Python `cli/aimail:3591-3606`）；隐藏开关见 [`address_hidden_args`]。
fn address_view_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help(SID_HELP),
    )
    .arg(
        Arg::new("default")
            .short('d')
            .long("default")
            .value_name("NAME")
            .help("set the default main-agent name (was: mailname)"),
    )
    .arg(
        Arg::new("agent")
            .short('a')
            .long("agent")
            .help("target agent (platform session/profile id) for set-name/set-manager"),
    )
    .arg(
        Arg::new("email")
            .short('e')
            .long("email")
            .help("target by exact registered email instead of -a"),
    )
    .arg(
        Arg::new("name")
            .short('n')
            .long("name")
            .value_name("NAME")
            .help("new address name (set-name) or with -d the default name"),
    )
    .arg(
        Arg::new("manager")
            .short('m')
            .long("manager")
            .value_name("EMAIL")
            .help("new manager address for the target agent (set-manager)"),
    )
}

/// 构造完整命令树（集成测试也用它，避免测试复制一份清单）。
pub fn build_cli() -> Command {
    let mut app = Command::new("aimail")
        .about(DESCRIPTION)
        .after_help(EPILOG)
        .subcommand_required(true)
        .disable_version_flag(true)
        .disable_help_subcommand(true);

    for (name, help) in SUBCOMMANDS {
        let mut sub = Command::new(*name).about(*help);
        match *name {
            "install" => sub = install_hidden_args(sub),
            "address" => sub = address_hidden_args(address_view_args(sub)),
            "stats" => sub = stats_args(sub),
            "check" => sub = check_args(sub),
            "persona" => sub = persona_args(sub),
            "domain" => sub = domain_args(sub),
            "prompt" => {
                for (pn, ph) in PROMPT_SUBCOMMANDS {
                    sub = sub.subcommand(Command::new(*pn).about(*ph));
                }
            }
            _ => {}
        }
        app = app.subcommand(sub);
    }
    app
}

/// 进程入口：解析 argv 并分发，返回退出码。
pub fn run() -> i32 {
    let matches = build_cli().get_matches();
    match matches.subcommand() {
        Some(("version", _)) => crate::cmd::version::run(),
        Some(("stats", m)) => crate::cmd::stats::run(m.get_flag("all")),
        Some(("persona", _)) => crate::cmd::persona::run(),
        Some(("check", m)) => crate::cmd::check::run(crate::cmd::check::Args {
            system_id: m
                .get_one::<String>("system-id")
                .cloned()
                .unwrap_or_default(),
            home: m.get_one::<String>("home").cloned(),
            verbose: m.get_flag("verbose"),
        }),
        Some(("domain", m)) => crate::cmd::domain::run(crate::cmd::domain::Args {
            system_id: arg_str(m, "system-id"),
            add: arg_opt(m, "add"),
        }),
        Some(("address", m)) => crate::cmd::address::run(crate::cmd::address::Args {
            system_id: arg_str(m, "system-id"),
            default: arg_opt(m, "default"),
            agent: arg_opt(m, "agent"),
            email: arg_opt(m, "email"),
            name: arg_opt(m, "name"),
            manager: arg_opt(m, "manager"),
            inbound_live: m.get_flag("inbound-live"),
            inbound_down: m.get_flag("inbound-down"),
        }),
        Some((name, _)) => crate::cmd::stub::not_yet_ported(name),
        // subcommand_required(true) ⇒ 到不了这里；留非零兜底而不是伪装成 0。
        None => crate::cmd::stub::not_yet_ported("<none>"),
    }
}

fn arg_str(matches: &clap::ArgMatches, key: &str) -> String {
    matches.get_one::<String>(key).cloned().unwrap_or_default()
}

fn arg_opt(matches: &clap::ArgMatches, key: &str) -> Option<String> {
    matches.get_one::<String>(key).cloned()
}
