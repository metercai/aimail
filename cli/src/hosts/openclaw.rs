//! openclaw host (node / agent-aimail plugin).
//!
//! This file is the **entire data** for this host: identity features, pointer shape, registration/assembly steps, health check items.
//! The engine is in `hosts/{mod,identity,pointer,runtime}.rs`; adding a host = adding a file in this directory,
//! Then add a line in `ALL` in `mod.rs`.

use crate::hosts::exec::{Register, RegisterAll, SkipIf, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::HostDef;

pub static OPENCLAW: HostDef = HostDef {
    id: "openclaw",
    detect: Detect {
            dir_name: None,
            markers: &["openclaw.json"],
        },
    pointer: PointerKind::Root,
    register: Register::NodeEntry { node_path: "{home}/npm/projects/*/node_modules/agent-aimail/dist/openclaw/register-cli.js", fail_hint: "First install the agent-aimail plugin (`openclaw plugins install agent-aimail --force --accept-capabilities` → restart gateway); registration goes through the plugin's built-in node entry --op assemble (owner 2026-10-11: host_command shape retired)" },
    home_dir: ".openclaw",
    install_steps: &[Step::Print { when: None, text: "openclaw platform: ensure the agent-aimail plugin (auto; skipped when present) + register the agent (`aimail address -s SID`):" }, Step::Spawn { when: None, argv: &["openclaw", "plugins", "install", "agent-aimail@latest", "--force", "--accept-capabilities"], env: None, skip_if: Some(SkipIf { argv: &["openclaw", "plugins", "list"], match_: "aimail" }), on_missing: Some("fail"), fail_hint: Some("`openclaw` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("agent-aimail plugin install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`; keep `@latest` in the spec: a bare name is shadowed by a same-named local dir in cwd"), ok_text: Some("agent-aimail plugin installed (npm registry latest stable; takes effect after the gateway restarts)"), skipped_text: Some("agent-aimail plugin already present (install skipped)") }, Step::Print { when: None, text: "  restart the openclaw gateway to activate the plugin: `openclaw gateway install` (or `restart`)" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(true), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -s SID` (via plugin node entry --op assemble)" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(false), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -s SID` (once pointer/system ready)" }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("openclaw agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::RegisterAll { ok_text: Some("openclaw --all-agents registration complete"), warn_hint: Some("openclaw register-all failed (rerun `aimail reset -s {sid} --all-agents` once the plugin is in place)") }],
    uninstall_steps: &[
            Step::RmPointer {
                path: "{home}/{pointer_file}",
                ok_text: Some("removed pointer {home}/{pointer_file}"),
            },
            Step::Print {
                when: None,
                text: "  openclaw: All integration is in the TS plugin (agent-aimail) -- plugin removal = openclaw host-side uninstall + AIMail-side teardown (via plugin node entry --op teardown); shared system data has been cleaned in the previous section (gateway deregistration + local data)",
            },
        ],
    health_checks: &[Health { id: "plugin-installed", spec: HealthSpec::CommandMatch { argv: &["openclaw", "plugins", "list"], match_: "agent-aimail", fallback_glob: Some("{user_home}/.openclaw/npm/projects/agent-aimail*"), ok_text: "agent-aimail plugin present (openclaw plugins list)", fail_text: "agent-aimail plugin not installed", fix: Some("aimail install --home <openclaw home> (auto-install; skip if installed) or openclaw plugins install agent-aimail@latest --force --accept-capabilities") } }, Health { id: "skills", spec: HealthSpec::FileExists { path: "{user_home}/.openclaw/skills/agentmail/SKILL.md", ok_text: "skills/agentmail/SKILL.md present", fail_text: "skills/agentmail/SKILL.md missing", fix: Some("Plugin includes skill (agent-aimail resources) -- check plugin enabled: openclaw plugins list") } }],
    register_all: Some(RegisterAll::NodeEntry {
            node_path: "{home}/npm/projects/*/node_modules/agent-aimail/dist/openclaw/register-cli.js",
            fail_hint: "agent-aimail must contain an independent node entry (dist/register-cli.js, --op gate)",
        }),
    agent_check_remote: false,
    uses_agent_home: false,
    repair_hints: &[],
    agents_container: "agents",
};
