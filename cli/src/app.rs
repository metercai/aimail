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
/// `install` 的参数（Python `cli/aimail:3462-3490`：人面 9 个 + 隐藏机器面 6 个）。
/// 隐藏项的注册顺序与 Python 一致（usage 行只显示人面）。
fn ping_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .value_name("ID")
            .help("system id"),
    )
    .arg(
        Arg::new("agent-home")
            .long("agent-home")
            .value_name("DIR")
            .help("agent system home (Hermes=~/.hermes, OpenClaw=~/.openclaw)"),
    )
    .arg(
        Arg::new("agent")
            .long("agent")
            .value_name("ID")
            .help("agent identity (locates the mail dir)"),
    )
    .arg(
        Arg::new("manager")
            .long("manager")
            .value_name("ADDR")
            .help("sender (manager) address, default config.manager_address"),
    )
    .arg(
        Arg::new("timeout")
            .long("timeout")
            .value_name("SECS")
            .default_value("120")
            .help("how long to wait for the pong (seconds)"),
    )
    .arg(
        Arg::new("no-snapshot")
            .long("no-snapshot")
            .action(ArgAction::SetTrue)
            .help("skip the raw mail snapshot check"),
    )
}

fn renew_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .value_name("ID")
            .help("system id (unique local system is picked automatically when omitted)"),
    )
    .arg(
        Arg::new("code")
            .short('c')
            .long("code")
            .value_name("CODE")
            .help("product activation code (omit for read-only expiry view)"),
    )
    .arg(
        Arg::new("status")
            .short('t')
            .long("status")
            .action(ArgAction::SetTrue)
            .help("read-only expiry & quota view (no code consumed)"),
    )
    .arg(
        Arg::new("gateway-url")
            .short('g')
            .long("gateway-url")
            .value_name("URL")
            .help("gateway base URL (default: existing config or AIMAIL_URL)"),
    )
}

fn bridge_args(cmd: clap::Command) -> clap::Command {
    cmd.arg(
        Arg::new("home")
            .short('H')
            .long("home")
            .value_name("DIR")
            .help("system home / platform root (inferred from --system-id when omitted)"),
    )
    .arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .value_name("ID")
            .help("refresh forwarding routes for this system"),
    )
        .arg(
            Arg::new("restart")
                .short('r')
                .long("restart")
                .action(ArgAction::SetTrue)
                .help("restart the bridge (single instance)"),
        )
        .arg(
            Arg::new("upgrade")
                .short('u')
                .long("upgrade")
                .action(ArgAction::SetTrue)
                .help("upgrade the bridge binary from this repo's bridge/ zips (sha-compare \u{2192} contract stop \u{2192} atomic replace \u{2192} restart)"),
        )
        .arg(
            Arg::new("platform")
                .long("platform")
                .value_name("NAME")
                .default_value("")
                .help("override platform detection (value validated against the platform registry)"),
        )
}

fn reset_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("home")
            .short('H')
            .long("home")
            .value_name("DIR")
            .help("system home / platform root (inferred from --system-id when omitted)"),
    )
    .arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .value_name("ID")
            .help("system id (used to infer --home and the platform pointer when omitted)"),
    )
    .arg(
        Arg::new("gateway-url")
            .short('g')
            .long("gateway-url")
            .value_name("URL")
            .default_value("")
            .help("gateway base URL (default: existing config or AIMAIL_URL)"),
    )
    .arg(
        Arg::new("system-name")
            .short('n')
            .long("system-name")
            .value_name("NAME")
            .help("system display name (explicit only: identity field, never taken from .env)"),
    )
    .arg(
        Arg::new("manager")
            .short('m')
            .long("manager")
            .value_name("ADDR")
            .help("manager address for registrations (falls back to env/config)"),
    )
    .arg(
        Arg::new("all-agents")
            .long("all-agents")
            .action(ArgAction::SetTrue)
            .help("re-run registration for ALL platform agents (default: main agent only)"),
    )
    .arg(
        Arg::new("platform")
            .long("platform")
            .value_name("NAME")
            .default_value("")
            .help("override platform detection (value validated against the platform registry)"),
    )
}

fn uninstall_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help(SID_HELP),
    )
    .arg(Arg::new("home").short('H').long("home").help(HOME_HELP))
    .arg(
        Arg::new("gateway-url")
            .short('g')
            .long("gateway-url")
            .value_name("URL")
            .default_value("")
            .help("gateway to deregister from (default: the system cfg; use it when the cfg was rewritten — never falls back to a production default)"),
    )
    .arg(
        Arg::new("yes")
            .short('y')
            .long("yes")
            .action(ArgAction::SetTrue)
            .help("skip confirmation prompt"),
    )
    .arg(
        Arg::new("platform")
            .long("platform")
            .value_name("NAME")
            .default_value("")
            .help("override platform detection (value validated against the platform registry)"),
    )
}

fn install_args(cmd: Command) -> Command {
    let cmd = cmd
        .arg(Arg::new("home").short('H').long("home").help(HOME_HELP))
        .arg(Arg::new("system-id").short('s').long("system-id").help(SID_HELP))
        .arg(
            Arg::new("product-code")
                .short('c')
                .long("product-code")
                .help("product activation code (new system path)"),
        )
        .arg(
            Arg::new("admin-key")
                .short('k')
                .long("admin-key")
                .help("agent-side system key of an already-activated system (reuse path; the system key file the gateway prints at start — the gateway's own admin key is refused)"),
        )
        .arg(
            Arg::new("gateway-url")
                .short('g')
                .long("gateway-url")
                .help("gateway base URL (default: AIMAIL_GATEWAY_URL env, then the system cfg)"),
        )
        .arg(
            Arg::new("system-name")
                .short('n')
                .long("system-name")
                .help("system name (shared-domain identifier)"),
        )
        .arg(
            Arg::new("manager")
                .short('m')
                .long("manager")
                .help("manager address (default: AIMAIL_MANAGER_ADDRESS env)"),
        )
        .arg(
            Arg::new("domain")
                .short('d')
                .long("domain")
                .help("domain to pre-create (default: AIMAIL_DOMAIN env)"),
        )
        .arg(
            Arg::new("all-agents")
                .long("all-agents")
                .action(ArgAction::SetTrue)
                .help("register ALL platform agents (default: main agent only)"),
        )
        .arg(
            Arg::new("platform")
                .long("platform")
                .value_name("NAME")
                .default_value("")
                .help("override platform detection (value validated against the platform registry)"),
        )
        .arg(
            Arg::new("container")
                .long("container")
                .value_name("NAME")
                .default_value("")
                .help("docker container name of this agent system (recorded as runtime=docker in system config)"),
        )
        .arg(
            Arg::new("container-home")
                .long("container-home")
                .value_name("PATH")
                .default_value("")
                .help("platform home path INSIDE the container (default: same as --home; SOP = same-path bind mount)"),
        );
    install_hidden_args(cmd)
}

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
            "install" => sub = install_args(sub),
            "address" => sub = address_hidden_args(address_view_args(sub)),
            "reset" => sub = reset_args(sub),
            "bridge" => sub = bridge_args(sub),
            "renew" => sub = renew_args(sub),
            "ping" => sub = ping_args(sub),
            "uninstall" => sub = uninstall_args(sub),
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
    // 与 Python `main():3442` 同序：先把机器级/仓库 .env 灌进进程环境（绝不覆盖已有值）
    crate::core::config::load_env();
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
        Some(("uninstall", m)) => crate::cmd::uninstall::run(&crate::cmd::uninstall::Args {
            system_id: arg_str(m, "system-id"),
            home: arg_str(m, "home"),
            gateway_url: arg_str(m, "gateway-url"),
            yes: m.get_flag("yes"),
            platform: arg_str(m, "platform"),
        }),
        Some(("ping", m)) => crate::cmd::ping::run(&crate::cmd::ping::Args {
            system_id: arg_str(m, "system-id"),
            agent_home: arg_str(m, "agent-home"),
            agent: arg_str(m, "agent"),
            manager: arg_str(m, "manager"),
            timeout: arg_str(m, "timeout").parse().unwrap_or(120),
            no_snapshot: m.get_flag("no-snapshot"),
        }),
        Some(("renew", m)) => crate::cmd::renew::run(&crate::cmd::renew::Args {
            system_id: arg_str(m, "system-id"),
            code: m.get_one::<String>("code").cloned(),
            status: m.get_flag("status"),
            gateway_url: m.get_one::<String>("gateway-url").cloned(),
        }),
        Some(("bridge", m)) => crate::cmd::bridge::run(&crate::cmd::bridge::Args {
            system_id: arg_str(m, "system-id"),
            home: arg_str(m, "home"),
            restart: m.get_flag("restart"),
            upgrade: m.get_flag("upgrade"),
            platform: arg_str(m, "platform"),
        }),
        Some(("reset", m)) => crate::cmd::reset::run(&crate::cmd::reset::Args {
            home: arg_str(m, "home"),
            system_id: arg_str(m, "system-id"),
            platform: arg_str(m, "platform"),
            gateway_url: arg_str(m, "gateway-url"),
            system_name: arg_str(m, "system-name"),
            manager: arg_str(m, "manager"),
            all_agents: m.get_flag("all-agents"),
        }),
        Some(("install", m)) => crate::cmd::install::run(crate::cmd::install::Args {
            home: arg_str(m, "home"),
            system_id: arg_str(m, "system-id"),
            product_code: arg_str(m, "product-code"),
            admin_key: arg_str(m, "admin-key"),
            gateway_url: arg_str(m, "gateway-url"),
            system_name: arg_str(m, "system-name"),
            manager: arg_str(m, "manager"),
            domain: arg_str(m, "domain"),
            all_agents: m.get_flag("all-agents"),
            system_only: m.get_flag("system-only"),
            payload: arg_str(m, "payload"),
            payload_name: arg_str(m, "payload_name"),
            dest: arg_str(m, "dest"),
            source_root: arg_str(m, "source-root"),
            force: m.get_flag("force"),
            container: arg_str(m, "container"),
            container_home: arg_str(m, "container-home"),
            platform: arg_str(m, "platform"),
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
