//! pi host (node / agent-aimail extension).
//!
//! this file is the **complete data** of that host: identity features, pointer form, register/assemble steps, health items.
//! the engine is in `hosts/{mod,identity,pointer,runtime}.rs`; adding a host = adding one file in this directory,
//! Then add a line in `ALL` in `mod.rs`.

use crate::hosts::exec::{Register, SkipIf, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::HostDef;

pub static PI: HostDef = HostDef {
    id: "pi",
    detect: Detect {
            dir_name: Some(".pi"),
            markers: &["agent"],
        },
    pointer: PointerKind::Root,
    register: Register::NodeEntry {
            node_path: "{home}/agent/npm/node_modules/agent-aimail/dist/pi/register-cli.js",
            fail_hint: "agent-aimail extension not ready -- first `pi install npm:agent-aimail` to install the extension, then register",
        },
    home_dir: ".pi",
    install_steps: &[Step::Print { when: None, text: "pi platform: ensure the agent-aimail extension (auto; skipped when present) + register the agent:" }, Step::Spawn { when: None, argv: &["pi", "install", "npm:agent-aimail"], env: None, skip_if: Some(SkipIf { argv: &["pi", "list"], match_: "agent-aimail" }), on_missing: Some("fail"), fail_hint: Some("`pi` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("agent-aimail extension install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`"), ok_text: Some("agent-aimail extension installed (npm registry latest stable; takes effect after the pi session restarts)"), skipped_text: Some("agent-aimail extension already present (install skipped)") }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("pi agent registration failed (see above) — rerun `aimail reset -s {sid}` once the extension is in place (address name is determined by the SDK, CLI is not involved)") }, Step::Warn { when: Some(When { cfg_complete: Some(false), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "pi registration skipped (config incomplete — retry after activation/install, or run `aimail address -s SID`)" }, Step::Print { when: None, text: "  pi: inbound listener lives with the pi session lifecycle (restart the session to apply)" }],
    uninstall_steps: &[
            Step::RmPointer {
                path: "{home}/{pointer_file}",
                ok_text: Some("removed pointer {home}/{pointer_file}"),
            },
            Step::RmDir {
                path: "{home}/skills/agentmail",
                ok_text: Some("removed skill {home}/skills/agentmail"),
            },
            Step::Print {
                when: None,
                text: "  pi: Extension removal = pi host-side removal of the agent-aimail extension",
            },
        ],
    health_checks: &[Health {
            id: "pointer",
            spec: HealthSpec::PointerMatch {
                path: "{user_home}/.pi/.agentmail",
                ok_text: ".pi pointer matches system",
                fail_text: ".pi pointer missing/mismatch",
            },
        }],
    register_all: None,
    agent_check_remote: false,
    uses_agent_home: false,
    repair_hints: &[],
    agents_container: "",
};
