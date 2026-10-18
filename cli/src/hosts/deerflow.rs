//! deer-flow 宿主（python / manage.py 注册器 + skill/mcp 装配）。
//!
//! 本文件是该宿主的**全部数据**：身份特征、指针形态、注册/装配步骤、体检项。
//! 引擎在 `hosts/{mod,identity,pointer,runtime}.rs`；新增宿主 = 新增本目录一个文件，
//! 再在 `mod.rs` 的 `ALL` 里加一行。

use crate::hosts::exec::{EnvPairs, Register, RegisterAll, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::HostDef;

pub static DEERFLOW: HostDef = HostDef {
    id: "deerflow",
    detect: Detect {
            dir_name: None,
            markers: &["backend/app/gateway"],
        },
    pointer: PointerKind::Root,
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
            fail_hint: "deer-flow manage.py 注册器失败——检查 deer-flow 模块与系统配置",
        },
    home_dir: ".deer-flow",
    install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: Some("python3"), runtime: None, runtime_not: None }), argv: &["sh", "-c", "P=\"{python}\"; command -v \"$P\" >/dev/null 2>&1 || P=python3; if command -v uv >/dev/null 2>&1; then uv pip install --python \"$P\" -q aimailsdk && exit 0; fi; \"$P\" -m pip install -q --break-system-packages aimailsdk 2>/dev/null || \"$P\" -m pip install -q aimailsdk"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(宿主 python 自适配 uv→pip(含 PEP668 --break-system-packages) 均不可用) exit {rc}"), ok_text: Some("aimailsdk installed into the SAME interpreter the CLI resolves (${AIMAIL_PYTHON:-python3})"), skipped_text: None }, Step::SdkInstall { target: "deerflow", fn_name: "install_deerflow", ok_text: Some("deerflow SDK install done"), fail_hint: None, on_error: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-skill.sh"], env: Some(EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")])), skip_if: None, on_missing: None, fail_hint: Some("deer-flow install-skill.sh exit {rc}(关键装配步: skills 落点; 失败必须可见, 不许当成功)"), on_error: Some("fail"), warn_hint: None, ok_text: None, skipped_text: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-mcp.sh"], env: Some(EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")])), skip_if: None, on_missing: None, fail_hint: None, on_error: Some("fail"), warn_hint: Some("deer-flow install-mcp.sh exit {rc}(关键装配步: MCP toolset 落点; 失败必须可见, 不许当成功)"), ok_text: None, skipped_text: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("deerflow 主 agent 注册失败(见上)——稍后 reset 重试") }, Step::RegisterAll { ok_text: Some("deerflow --all-agents 全量注册完成"), warn_hint: Some("deerflow 全量注册失败(可 reset --all-agents 重试)") }],
    uninstall_steps: &[
            Step::SdkUninstall {
                target: "deerflow",
                fn_name: "uninstall_deerflow",
            },
            Step::Print {
                when: None,
                text: "  deerflow: skill/mcp 宿主装配残留可用宿主侧管理清理",
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
    payload_refs: &["{home}/extensions_config.json"],
    register_all: Some(RegisterAll::PythonScript {
            python: "deer-flow/manage.py",
            args: &["register", "--all", "--system-id", "{sid}"],
            env: EnvPairs(&[]),
            fail_hint: "manage.py register --all 失败(见上输出)",
        }),
    agent_check_remote: true,
    uses_agent_home: false,
    repair_hints: &[],
    agents_container: "",
};
