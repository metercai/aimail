//! deer-flow host (python / manage.py registrar + skill/mcp assembly).
//!
//! This file is the **entire data** for this host: identity features, pointer shapes, registration/assembly steps, health check items.
//! Engine is in `hosts/{mod,identity,pointer,runtime}.rs`; adding a host = adding a file in this directory,
//! Then add a line in `ALL` in `mod.rs`.

use crate::hosts::exec::{EnvPairs, Register, RegisterAll, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::{HostDef, InboundMode};

pub static DEERFLOW: HostDef = HostDef {
    id: "deerflow",
    detect: Detect {
            dir_name: None,
            markers: &["backend/app/gateway"],
        },
    pointer: PointerKind::Root,
    inbound_mode: InboundMode::HostGateway,
    register: Register::PythonScript {
            python: "deer-flow/manage.py",
            args: &[
                "register",
                "--system-id",
                "{sid}",
                "--agent",
                "{agent}",
                "--manager",
                "{manager}",
                "--name",
                "{name}",
            ],
            fail_hint: "deer-flow manage.py registrar failed — check deer-flow module and system configuration",
        },
    home_dir: ".deer-flow",
    install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: Some("python3"), runtime: None, runtime_not: None }), argv: &["sh", "-c", "P=\"{python}\"; command -v \"$P\" >/dev/null 2>&1 || P=python3; if command -v uv >/dev/null 2>&1; then uv pip install --python \"$P\" -q agent-aimail && exit 0; fi; \"$P\" -m pip install -q --break-system-packages agent-aimail 2>/dev/null || \"$P\" -m pip install -q agent-aimail"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("agent-aimail install failed (host python self-adaptation uv→pip (incl. PEP668 --break-system-packages) all unavailable) exit {rc}"), ok_text: Some("agent-aimail installed into the SAME interpreter the CLI resolves (${AIMAIL_PYTHON:-python3})"), skipped_text: None }, Step::SdkInstall { target: "deerflow", fn_name: "install_deerflow", ok_text: Some("deerflow SDK install done"), fail_hint: None, on_error: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-skill.sh"], env: Some(EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")])), skip_if: None, on_missing: None, fail_hint: Some("deer-flow install-skill.sh exit {rc} (critical assembly step: skills landing spot; failure must be visible, never treat as success)"), on_error: Some("fail"), warn_hint: None, ok_text: None, skipped_text: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-mcp.sh"], env: Some(EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")])), skip_if: None, on_missing: None, fail_hint: None, on_error: Some("fail"), warn_hint: Some("deer-flow install-mcp.sh exit {rc} (critical assembly step: MCP toolset landing spot; failure must be visible, never treat as success)"), ok_text: None, skipped_text: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("deerflow main-agent registration failed (see above) — retry with reset later") }, Step::RegisterAll { ok_text: Some("deerflow --all-agents full registration done"), warn_hint: Some("deerflow full registration failed (can retry with reset --all-agents)") }],
    uninstall_steps: &[
            Step::SdkUninstall {
                target: "deerflow",
                fn_name: "uninstall_deerflow",
            },
            Step::Print {
                when: None,
                text: "  deerflow: skill/mcp host assembly residue can be cleaned up via host-side management",
            },
        ],
    health_checks: &[Health {
            id: "patch-app",
            spec: HealthSpec::FileContainsAlt {
                path: "{home}/backend/app/gateway/app.py",
                alt: "{home}/app/gateway/app.py",
                marker: "aimail_inbound",
                ok_text: "app.py patched (aimail_inbound router present)",
                fail_text: "app.py NOT patched or missing",
            },
        }],
    register_all: Some(RegisterAll::PythonScript {
            python: "deer-flow/manage.py",
            args: &["register", "--all", "--system-id", "{sid}"],
            env: EnvPairs(&[]),
            fail_hint: "manage.py register --all failed (see output above)",
        }),
    agent_check_remote: true,
    uses_agent_home: false,
    repair_hints: &[],
    agents_container: "",
};
