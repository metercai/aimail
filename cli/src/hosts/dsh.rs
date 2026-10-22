//! dsh 宿主（node / agent-aimail 插件，多 profile）。
//!
//! 本文件是该宿主的**全部数据**：身份特征、指针形态、注册/装配步骤、体检项。
//! 引擎在 `hosts/{mod,identity,pointer,runtime}.rs`；新增宿主 = 新增本目录一个文件，
//! 再在 `mod.rs` 的 `ALL` 里加一行。

use crate::hosts::exec::{EnvPairs, Register, Step, When};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::HostDef;

pub static DSH: HostDef = HostDef {
    id: "dsh",
    detect: Detect {
            dir_name: Some(".dsh"),
            markers: &["profiles"],
        },
    pointer: PointerKind::Root,
    register: Register::NodeEntry {
            node_path: "{home}/profiles/*/node_modules/agent-aimail/dist/dsh/register-cli.js",
            fail_hint:
                "agent-aimail 插件未就绪——先 `dsh plugin --profile web add agent-aimail` 装插件再注册",
        },
    home_dir: ".dsh",
    install_steps: &[Step::Spawn { when: None, argv: &["dsh", "plugin", "--profile", "web", "add", "agent-aimail", "--config.minimumReleaseAge=0"], env: Some(EnvPairs(&[("DSH_HOME","{home}")])), skip_if: None, on_missing: Some("fail"), fail_hint: Some("`dsh` CLI not found — build/install deepseek-harness first (e.g. cd ~/deepseek-harness && cargo build --release && ln -sf \"$PWD/target/release/dsh\" ~/.local/bin/dsh), then re-run aimail install"), on_error: Some("warn"), warn_hint: Some("dsh plugin add aimail 失败(检查 aimail npm 包发布与网络)"), ok_text: Some("aimail 插件已安装(dsh bundle 层挂载)"), skipped_text: None }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("dsh agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::Print { when: None, text: "  dsh: 绑定 = 创建 dsh session(mail preset)后自动 auto-bind;" }, Step::Print { when: None, text: "        CLI 侧注册:`aimail address -s SID`" }],
    uninstall_steps: &[
            Step::RmPointer {
                path: "{home}/{pointer_file}",
                ok_text: Some("removed pointer {home}/{pointer_file}"),
            },
            Step::Print {
                when: None,
                text: "  dsh: 共享配置已清理;dsh 侧卸载 mail 插件(mail/mail-inbound/tool-mail)+ 删 preset 即可",
            },
        ],
    health_checks: &[],
    register_all: None,
    agent_check_remote: false,
    uses_agent_home: false,
    repair_hints: &[],
    agents_container: "profiles",
};
