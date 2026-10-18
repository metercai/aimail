//! pi 宿主（node / pi-aimail 扩展）。
//!
//! 本文件是该宿主的**全部知识**：身份特征、语言类型（由注册入口派生）、注册/装配步骤、体检项。
//! 新增宿主 = 新增本目录下一个模块 + 在 `mod.rs` 注册。

use crate::hosts::exec::{Register, RegisterAll, SkipIf, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::Host;

pub struct Pi;
pub static PI: Pi = Pi;

impl Host for Pi {
    fn id(&self) -> &'static str {
        "pi"
    }
    fn home_dir(&self) -> &'static str {
        ".pi"
    }
    fn detect(&self) -> Detect {
        Detect {
            dir_name: Some(".pi"),
            markers: &["agent"],
        }
    }
    fn pointer_kind(&self) -> PointerKind {
        PointerKind::Root
    }
    fn register(&self) -> Register {
        Register::NodeEntry {
            node_path: "{home}/agent/npm/node_modules/pi-aimail/dist/register-cli.js",
            fail_hint: "pi-aimail 扩展未就绪——先 `pi install npm:pi-aimail` 装扩展再注册",
        }
    }
    fn register_all(&self) -> Option<RegisterAll> {
        None
    }
    fn install_plan(&self) -> &'static [Step] {
        &[Step::Print { when: None, text: "pi platform: ensure the pi-aimail extension (auto; skipped when present) + register the agent:" }, Step::Spawn { when: None, argv: &["pi", "install", "npm:pi-aimail"], env: None, skip_if: Some(SkipIf { argv: &["pi", "list"], match_: "pi-aimail" }), on_missing: Some("fail"), fail_hint: Some("`pi` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("pi-aimail extension install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`"), ok_text: Some("pi-aimail extension installed (npm registry latest stable; takes effect after the pi session restarts)"), skipped_text: Some("pi-aimail extension already present (install skipped)") }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("pi agent registration failed (see above) — rerun `aimail reset -s {sid}` once the extension is in place (地址名由 SDK 决定，CLI 不涉及)") }, Step::Warn { when: Some(When { cfg_complete: Some(false), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "pi registration skipped (config incomplete — retry after activation/install, or run `aimail address -s SID`)" }, Step::Print { when: None, text: "  pi: inbound listener lives with the pi session lifecycle (restart the session to apply)" }]
    }
    fn uninstall_plan(&self) -> &'static [Step] {
        &[
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
        ]
    }
    fn health_checks(&self) -> &'static [Health] {
        &[Health {
            id: "pointer",
            spec: HealthSpec::PointerMatch {
                path: "{user_home}/.pi/.agentmail",
                ok_text: ".pi pointer matches system",
                fail_text: ".pi pointer missing/mismatch",
            },
        }]
    }
    fn payload_refs(&self) -> &'static [&'static str] {
        &[]
    }
    fn agent_check_remote(&self) -> bool {
        false
    }
}
