//! pi 宿主（node / pi-aimail 扩展）。
//!
//! 本文件是该宿主的**全部数据**：身份特征、指针形态、注册/装配步骤、体检项。
//! 引擎在 `hosts/{mod,identity,pointer,runtime}.rs`；新增宿主 = 新增本目录一个文件，
//! 再在 `mod.rs` 的 `ALL` 里加一行。

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
            node_path: "{home}/agent/npm/node_modules/pi-aimail/dist/register-cli.js",
            fail_hint: "pi-aimail 扩展未就绪——先 `pi install npm:pi-aimail` 装扩展再注册",
        },
    home_dir: ".pi",
    install_steps: &[Step::Print { when: None, text: "pi platform: ensure the pi-aimail extension (auto; skipped when present) + register the agent:" }, Step::Spawn { when: None, argv: &["pi", "install", "npm:pi-aimail"], env: None, skip_if: Some(SkipIf { argv: &["pi", "list"], match_: "pi-aimail" }), on_missing: Some("fail"), fail_hint: Some("`pi` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("pi-aimail extension install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`"), ok_text: Some("pi-aimail extension installed (npm registry latest stable; takes effect after the pi session restarts)"), skipped_text: Some("pi-aimail extension already present (install skipped)") }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("pi agent registration failed (see above) — rerun `aimail reset -s {sid}` once the extension is in place (地址名由 SDK 决定，CLI 不涉及)") }, Step::Warn { when: Some(When { cfg_complete: Some(false), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "pi registration skipped (config incomplete — retry after activation/install, or run `aimail address -s SID`)" }, Step::Print { when: None, text: "  pi: inbound listener lives with the pi session lifecycle (restart the session to apply)" }],
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
                text: "  pi: 扩展移除 = pi 宿主侧移除 pi-aimail 扩展",
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
