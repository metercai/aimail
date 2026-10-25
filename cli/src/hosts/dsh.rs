//! dsh host (node / agent-aimail plugin, multi-profile).
//!
//! This file is the **complete data** for this host: identity features, pointer forms, registration/assembly steps, health check items.
//! Engine is in `hosts/{mod,identity,pointer,runtime}.rs`; new host = new file in this directory,
//! Then add a line in `ALL` in `mod.rs`.

use crate::hosts::exec::{EnvPairs, Register, Step, When};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::{HostDef, InboundMode};

pub static DSH: HostDef = HostDef {
    id: "dsh",
    detect: Detect {
            dir_name: Some(".dsh"),
            markers: &["profiles"],
        },
    pointer: PointerKind::Root,
    inbound_mode: InboundMode::SelfBind,
    register: Register::NodeEntry {
            node_path: "{home}/profiles/*/node_modules/agent-aimail/dist/dsh/register-cli.js",
            fail_hint:
                "agent-aimail plugin not ready — first `dsh plugin --profile web add agent-aimail` to install plugin then register",
        },
    home_dir: ".dsh",
    install_steps: &[Step::Spawn { when: None, argv: &["dsh", "plugin", "--profile", "web", "add", "agent-aimail", "--config.minimumReleaseAge=0"], env: Some(EnvPairs(&[("DSH_HOME","{home}")])), skip_if: None, on_missing: Some("fail"), fail_hint: Some("`dsh` CLI not found — build/install deepseek-harness first (e.g. cd ~/deepseek-harness && cargo build --release && ln -sf \"$PWD/target/release/dsh\" ~/.local/bin/dsh), then re-run aimail install"), on_error: Some("warn"), warn_hint: Some("dsh plugin add aimail failed (check aimail npm package publish and network)"), ok_text: Some("aimail plugin installed (mounted at the dsh bundle layer)"), skipped_text: None }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("dsh agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::Print { when: None, text: "  dsh: binding = auto auto-bind after creating a dsh session (mail preset);" }, Step::Print { when: None, text: "        CLI-side registration: `aimail address -s SID`" }],
    uninstall_steps: &[
            Step::RmPointer {
                path: "{home}/{pointer_file}",
                ok_text: Some("removed pointer {home}/{pointer_file}"),
            },
            Step::Print {
                when: None,
                text: "  dsh: shared config cleaned; on dsh side, uninstall mail plugin (mail/mail-inbound/tool-mail) + delete preset to complete",
            },
        ],
    health_checks: &[],
    register_all: None,
    agent_check_remote: false,
    uses_agent_home: false,
    repair_hints: &[],
    agents_container: "profiles",
};
