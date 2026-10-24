//! clap command tree + dispatch (S1: command surface registration + honest exits).
//!
//! The command surface is a **frozen surface** (boundary finalization §1; changes
//! require owner sign-off): 16 top-level subcommand names + `prompt`'s 5 nested
//! subcommands + install's only hidden machine-surface argument (`--system-only`).
//! The frozen criterion is a set comparison between `tests/test_cli_parity.py`
//! and the Python side's `--help`.
//!
//! S1 implements only `version`; everything else is `not_yet_ported` (rc 1), and
//! the Python CLI remains authoritative.
//! Help text is taken **verbatim** from the Python argparse
//! (`cli/aimail:3462-3653`); changing one side requires syncing both -- drift is
//! caught by the parity test's subcommand/option set assertions.

use clap::{Arg, ArgAction, Command};

/// Two help texts shared by all subcommands (Python side `common_sid` /
/// `common_home`, verbatim identical).
const SID_HELP: &str = "target system id (default: the only installed system)";
const HOME_HELP: &str = "platform root (auto-resolved on single-platform machines)";

const DESCRIPTION: &str =
    "aimail CLI - set up, operate and maintain the AIMail link between your agent platform and the aimail-gateway.";

const EPILOG: &str = "scenario groups:
  setup      machine prep and platform integration: install / uninstall / reset
  operate    daily status, lifecycle, exams and flows: stats / renew / version / check / repair / ping / welcome
  resources  system-scoped resources: domain / address / bridge";

/// The 16 top-level subcommands -- the first 15 match the Python side's
/// `add_parser` declaration order (help grouping order);
/// the trailing `platform` (added 2026-10-11, owner sign-off) is the machine
/// export surface, with no Python-side counterpart.
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
        "view/maintain system agent addresses (set-name / set-manager)",
    ),
    (
        "prompt",
        "maintain agent prompt rules: subject/body/sender/recipient → role file",
    ),
    (
        "bridge",
        "local bridge: status / refresh routes / restart / upgrade",
    ),
    (
        "platform",
        "platform static knowledge + detection (machine surface)",
    ),
    (
        "agent",
        "address-level bootstrap: host profile + SDK choice (control plane)",
    ),
];

/// Nested subcommands of `prompt` (Python `cli/aimail:3621-3647`).
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

/// Machine-surface hidden arguments (only `--system-only` belongs to the
/// reverse-call ABI; the rest are install hidden switches). Hidden = clap
/// `hide(true)`; must not appear in the human-surface help.
/// Arguments for `install` (Python `cli/aimail:3462-3490`: 9 human-surface +
/// 6 hidden machine-surface).
/// The registration order of hidden items matches Python (the usage line shows
/// only the human surface).
fn welcome_args(cmd: Command) -> Command {
    // Help text verbatim from Python (`cli/aimail`, common_sid/common_mgr)
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .value_name("ID")
            .help(SID_HELP),
    )
    .arg(
        Arg::new("manager")
            .short('m')
            .long("manager")
            .value_name("ADDR")
            .help("manager address (cc in API mode; From in SMTP mode)"),
    )
    .arg(
        Arg::new("no-wait")
            .short('w')
            .long("no-wait")
            .action(ArgAction::SetTrue)
            .help("do not wait for the draft reply"),
    )
    .arg(
        Arg::new("smtp")
            .long("smtp")
            .action(ArgAction::SetTrue)
            .help(
                "SMTP mode, direct 25-port send (default: API mode via the gateway system sender)",
            ),
    )
    .arg(
        Arg::new("timeout")
            .long("timeout")
            .value_name("SECS")
            .default_value("120")
            .help("how long to wait for the draft reply (seconds)"),
    )
}

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
            .help("gateway base URL (default: existing config or AIMAIL_GW_URL)"),
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
            .help("gateway base URL (default: existing config or AIMAIL_GW_URL)"),
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
                .help("gateway base URL (default: AIMAIL_GW_URL env, then the system cfg)"),
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
            Arg::new("agent")
                .short('a')
                .long("agent")
                .action(ArgAction::Append)
                .num_args(1..)
                .value_name("AGENT")
                .help("register only these platform agents (one -a, then the agent ids; unknown ones are ignored)"),
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
}

/// Hidden switches on `address` (2026-09-28 inbound live/down notifications, Python `:3601/:3603`;
/// 2026-10-13 added `--inbound-url` carrying the "definitive" inbound endpoint).
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
    .arg(
        Arg::new("inbound-url")
            .long("inbound-url")
            .value_name("URL")
            .help(
                "confirmed inbound endpoint (owner 2026-10-13): the URL the inbound \
                 server actually listens on; S1/S2 upsert the bridge route to it, \
                 S3 re-registers the address to push it to the gateway",
            )
            .hide(true),
    )
}

/// Arguments for `stats` (Python `cli/aimail:3531-3534`).
fn stats_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("all")
            .short('a')
            .long("all")
            .action(ArgAction::SetTrue)
            .help("full view: per-system health tags + broken systems + local platforms"),
    )
}

/// `persona` parameters (Python `cli/aimail:3577-3581`) —— parameters are for command surface fidelity only,
/// this command is a "pointer shell" (rc 2), does not read any arguments.
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

/// Arguments for `check` (Python `cli/aimail:3547-3552`; `--json` is an internal switch, not on the command surface).
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

/// Arguments for `domain` (Python `cli/aimail:3584-3589`).
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

/// Arguments for `address` (Python `cli/aimail:3591-3606`); see [`address_hidden_args`] for hidden switches.
fn address_view_args(cmd: Command) -> Command {
    cmd.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help(SID_HELP),
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
            .help("new address name (set-name)"),
    )
    .arg(
        Arg::new("manager")
            .short('m')
            .long("manager")
            .value_name("EMAIL")
            .help("new manager address for the target agent (set-manager)"),
    )
}

/// Construct the full command tree (integration tests also use it, avoiding duplicating the manifest in tests).
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
            "repair" => sub = repair_args(sub),

            "reset" => sub = reset_args(sub),
            "bridge" => sub = bridge_args(sub),
            "renew" => sub = renew_args(sub),
            "ping" => sub = ping_args(sub),
            "welcome" => sub = welcome_args(sub),
            "uninstall" => sub = uninstall_args(sub),
            "stats" => sub = stats_args(sub),
            "platform" => sub = platform_args(sub),
            "agent" => sub = agent_args(sub),
            "check" => sub = check_args(sub),
            "persona" => sub = persona_args(sub),
            "domain" => sub = domain_args(sub),
            "prompt" => {
                // Same as argparse `add_subparsers(required=True)`: bare `prompt` ⇒ rc=2 + usage
                sub = sub.subcommand_required(true);
                for (pn, ph) in PROMPT_SUBCOMMANDS {
                    let mut c = Command::new(*pn).about(*ph);
                    // `_pr_tgt`: shared by three surfaces (Python `cli/aimail:3618-3621`)
                    c = c
                        .arg(
                            Arg::new("system-id")
                                .short('s')
                                .long("system-id")
                                .help(SID_HELP),
                        )
                        .arg(
                            Arg::new("agent")
                                .short('a')
                                .long("agent")
                                .help("target agent name"),
                        )
                        .arg(
                            Arg::new("email")
                                .short('e')
                                .long("email")
                                .help("target address (full email)"),
                        );
                    match *pn {
                        "add" => {
                            c = c
                                .arg(Arg::new("name").short('n').long("name").required(true).help(
                                    "rule name = {10-99 serial}_{filename}; serial sets the order",
                                ))
                                .arg(Arg::new("file").long("file").help(
                                    "prompt file stem (default: the filename part of -n)",
                                ))
                                .arg(Arg::new("subject").long("subject").action(ArgAction::Append).help("keyword to match against the subject"))
                                .arg(Arg::new("body").long("body").action(ArgAction::Append).help("keyword to match against the body"))
                                .arg(Arg::new("sender").long("sender").action(ArgAction::Append).help("keyword to match against the sender"))
                                .arg(Arg::new("recipient").long("recipient").action(ArgAction::Append).help("keyword to match against the recipients"))
                                .arg(Arg::new("disable").long("disable").action(ArgAction::SetTrue).help("store the rule disabled"));
                        }
                        "rm" => {
                            c = c.arg(
                                Arg::new("name")
                                    .short('n')
                                    .long("name")
                                    .required(true)
                                    .help("rule name to remove"),
                            );
                        }
                        "test" => {
                            c = c
                                .arg(Arg::new("subject").long("subject").help("sample subject"))
                                .arg(Arg::new("body").long("body").help("sample body"))
                                .arg(Arg::new("sender").long("sender").help("sample sender"))
                                .arg(
                                    Arg::new("to")
                                        .long("to")
                                        .action(ArgAction::Append)
                                        .value_name("ADDR")
                                        .help("recipient (repeatable; joined for containment)"),
                                )
                                .arg(Arg::new("header").long("header").value_name("VALUE").help(
                                    "simulate the gateway X-AIMail-Prompt header value (file stem)",
                                ))
                                .arg(
                                    Arg::new("board-role")
                                        .long("board-role")
                                        .value_name("ROLE")
                                        .help("simulate the gateway-injected board role (L3)"),
                                );
                        }
                        "create-file" => {
                            c = c
                                .arg(
                                    Arg::new("name")
                                        .short('n')
                                        .long("name")
                                        .required(true)
                                        .help("rule name"),
                                )
                                .arg(
                                    Arg::new("file").long("file").help(
                                        "prompt file stem (default: the filename part of -n)",
                                    ),
                                );
                        }
                        _ => {}
                    }
                    sub = sub.subcommand(c);
                }
            }
            _ => {}
        }
        app = app.subcommand(sub);
    }
    app
}

fn agent_args(cmd: Command) -> Command {
    cmd.subcommand_required(true)
        .subcommand(
            Command::new("doctor")
                .about("verify the agent's self-description and pick the SDK (read-only)")
                .arg(
                    Arg::new("questions")
                        .long("questions")
                        .action(ArgAction::SetTrue)
                        .help("print the one-shot question block for the agent"),
                )
                .arg(
                    Arg::new("answers")
                        .long("answers")
                        .value_name("JSON")
                        .help("the agent's answer table as JSON"),
                )
                .arg(
                    Arg::new("answers-file")
                        .long("answers-file")
                        .value_name("PATH")
                        .help("read the answers JSON from a file"),
                )
                .arg(
                    Arg::new("json")
                        .long("json")
                        .action(ArgAction::SetTrue)
                        .help("print the host profile as JSON"),
                ),
        )
        .subcommand(
            Command::new("setup")
                .about("render the adapter + skill from a ready profile (dry-run by default)")
                .arg(
                    Arg::new("answers")
                        .long("answers")
                        .value_name("JSON")
                        .help("the agent's answer table as JSON"),
                )
                .arg(
                    Arg::new("answers-file")
                        .long("answers-file")
                        .value_name("PATH")
                        .help("read the answers JSON from a file"),
                )
                .arg(
                    Arg::new("apply")
                        .long("apply")
                        .action(ArgAction::SetTrue)
                        .help("actually write the files (default: dry-run)"),
                )
                .arg(Arg::new("host").long("host").value_name("NAME").help(
                    "adapted host (openclaw/pi/dsh/hermes/deerflow): install its adapter PACKAGE",
                ))
                .arg(
                    Arg::new("no-reload")
                        .long("no-reload")
                        .action(ArgAction::SetTrue)
                        .help("do not call the host reload hook"),
                ),
        )
        .subcommand(
            Command::new("uninstall")
                .about("remove what setup wrote (only files whose sha256 is unchanged)")
                .arg(
                    Arg::new("host")
                        .long("host")
                        .value_name("NAME")
                        .required(true)
                        .help("host name used at setup time"),
                ),
        )
}

/// Process entry point: parse argv and dispatch, return exit code.
pub fn run() -> i32 {
    // Same order as Python `main():3442`: first inject machine-level/repo .env into process environment (never overwrite existing values)
    crate::core::config::load_env();
    let matches = build_cli().get_matches();
    match matches.subcommand() {
        Some(("agent", m)) => {
            let Some((action, am)) = m.subcommand() else {
                return 2;
            };
            match action {
                "doctor" => crate::cmd::agent::run(&crate::cmd::agent::Args {
                    questions: am.get_flag("questions"),
                    answers: am.get_one::<String>("answers").cloned(),
                    answers_file: am.get_one::<String>("answers-file").cloned(),
                    json: am.get_flag("json"),
                }),
                "setup" if am.get_one::<String>("host").is_some() => {
                    crate::cmd::agent::package_setup(&crate::cmd::agent::PackageArgs {
                        host: am.get_one::<String>("host").cloned().unwrap_or_default(),
                        apply: am.get_flag("apply"),
                    })
                }
                "setup" => crate::cmd::agent::setup(&crate::cmd::agent::SetupArgs {
                    answers: am.get_one::<String>("answers").cloned(),
                    answers_file: am.get_one::<String>("answers-file").cloned(),
                    apply: am.get_flag("apply"),
                    reload: !am.get_flag("no-reload"),
                }),
                "uninstall" => crate::cmd::agent::uninstall(&crate::cmd::agent::UninstallArgs {
                    host: am.get_one::<String>("host").cloned(),
                }),
                _ => 2,
            }
        }
        Some(("version", _)) => crate::cmd::version::run(),
        Some(("stats", m)) => crate::cmd::stats::run(m.get_flag("all")),
        Some(("persona", _)) => crate::cmd::persona::run(),
        Some(("prompt", m)) => {
            let Some((action, am)) = m.subcommand() else {
                return 2;
            };
            let many = |key: &str| -> Vec<String> {
                // `try_get_many`: five surfaces have different switches, undefined keys must not panic
                am.try_get_many::<String>(key)
                    .ok()
                    .flatten()
                    .map(|v| v.cloned().collect())
                    .unwrap_or_default()
            };
            // The switches for the five aspects are different ⇒ **fault-tolerant reading** (clap panics on accessing undefined keys)
            let sstr = |key: &str| -> String {
                am.try_get_one::<String>(key)
                    .ok()
                    .flatten()
                    .cloned()
                    .unwrap_or_default()
            };
            let sopt = |key: &str| -> Option<String> {
                am.try_get_one::<String>(key).ok().flatten().cloned()
            };
            crate::cmd::prompt::run(&crate::cmd::prompt::Args {
                system_id: sstr("system-id"),
                agent: sopt("agent"),
                email: sopt("email"),
                paction: action.to_string(),
                name: sstr("name"),
                file: sstr("file"),
                disable: am
                    .try_get_one::<bool>("disable")
                    .ok()
                    .flatten()
                    .copied()
                    .unwrap_or(false),
                k_subject: many("subject"),
                k_body: many("body"),
                k_sender: many("sender"),
                k_recipient: many("recipient"),
                subject: sstr("subject"),
                body: sstr("body"),
                sender: sstr("sender"),
                to: many("to"),
                header: sstr("header"),
                board_role: sstr("board-role"),
            })
        }
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
            add: m.get_one::<String>("add").cloned(),
            id: arg_str(m, "id"),
            webhook_url: arg_str(m, "webhook-url"),
        }),
        Some(("address", m)) => crate::cmd::address::run(crate::cmd::address::Args {
            system_id: arg_str(m, "system-id"),
            agent: arg_opt(m, "agent"),
            email: arg_opt(m, "email"),
            name: arg_opt(m, "name"),
            manager: arg_opt(m, "manager"),
            inbound_live: m.get_flag("inbound-live"),
            inbound_down: m.get_flag("inbound-down"),
            inbound_url: arg_opt(m, "inbound-url"),
        }),
        Some(("uninstall", m)) => crate::cmd::uninstall::run(&crate::cmd::uninstall::Args {
            system_id: arg_str(m, "system-id"),
            home: arg_str(m, "home"),
            gateway_url: arg_str(m, "gateway-url"),
            yes: m.get_flag("yes"),
            platform: arg_str(m, "platform"),
        }),
        Some(("repair", m)) => crate::cmd::repair::run(&crate::cmd::repair::Args {
            system_id: arg_str(m, "system-id"),
            home: arg_opt(m, "home"),
            deep: m.get_flag("deep"),
            dry_run: m.get_flag("dry-run"),
        }),

        Some(("welcome", m)) => crate::cmd::welcome::run(&crate::cmd::welcome::Args {
            system_id: arg_str(m, "system-id"),
            manager: arg_str(m, "manager"),
            no_wait: m.get_flag("no-wait"),
            smtp: m.get_flag("smtp"),
            reply_timeout: arg_str(m, "timeout").parse().unwrap_or(120),
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
            agents: m
                .get_many::<String>("agent")
                .map(|v| v.cloned().collect())
                .unwrap_or_default(),
            system_only: m.get_flag("system-only"),
            container: arg_str(m, "container"),
            container_home: arg_str(m, "container-home"),
            platform: arg_str(m, "platform"),
        }),
        Some(("platform", m)) => {
            let Some(("spec", sm)) = m.subcommand() else {
                return 2;
            };
            crate::cmd::platform::run(&crate::cmd::platform::Args {
                home: sm.get_one::<String>("home").cloned(),
            })
        }
        Some((name, _)) => crate::cmd::stub::not_yet_ported(name),
        // subcommand_required(true) ⇒ cannot reach here; leave non-zero fallback instead of faking 0.
        None => crate::cmd::stub::not_yet_ported("<none>"),
    }
}

fn arg_str(matches: &clap::ArgMatches, key: &str) -> String {
    matches.get_one::<String>(key).cloned().unwrap_or_default()
}

fn arg_opt(matches: &clap::ArgMatches, key: &str) -> Option<String> {
    matches.get_one::<String>(key).cloned()
}

/// Argument surface for `repair` (= Python `p_repair`: `-s/-H/-D/-n`, no machine surface).
fn repair_args(sub: Command) -> Command {
    sub.arg(
        Arg::new("system-id")
            .short('s')
            .long("system-id")
            .help("target system id"),
    )
    .arg(
        Arg::new("home")
            .short('H')
            .long("home")
            .help("platform root (auto-resolved on single-platform machines)"),
    )
    .arg(
        Arg::new("deep")
            .short('D')
            .long("deep")
            .action(clap::ArgAction::SetTrue)
            .help(format!(
                "deep: rewrite webhook pairing from {} + drain stuck pending",
                crate::core::contract::binding_file()
            )),
    )
    .arg(
        Arg::new("dry-run")
            .short('n')
            .long("dry-run")
            .action(clap::ArgAction::SetTrue)
            .help("print the fix plan without executing"),
    )
}

/// Argument surface for `platform` (contract §5 detection surface; **no** Python counterpart).
/// `spec` is the only subcommand: `-H/--home` includes probe results when given; `--json` is an explicit declaration
/// (JSON is the only output form, the switch exists only to match the call form in the contract text).
fn platform_args(sub: Command) -> Command {
    let spec = Command::new("spec")
        .about("print the platform static knowledge table (+ detection when --home is given)")
        .arg(
            Arg::new("home")
                .short('H')
                .long("home")
                .help("platform root to probe (adds a detection result for it)"),
        )
        .arg(
            Arg::new("json")
                .long("json")
                .action(clap::ArgAction::SetTrue)
                .help("emit JSON (the only output form; accepted for explicitness)"),
        );
    sub.subcommand_required(true).subcommand(spec)
}
