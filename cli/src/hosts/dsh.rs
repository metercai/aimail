//! dsh 宿主（node / dsh-aimail 插件，多 profile）。
//!
//! 本文件是该宿主的**全部知识**：身份特征、语言类型（由注册入口派生）、注册/装配步骤、体检项。
//! 新增宿主 = 新增本目录下一个模块 + 在 `mod.rs` 注册。

use crate::hosts::exec::{EnvPairs, Register, RegisterAll, Step, When};
use crate::hosts::health::Health;
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::Host;

pub struct Dsh;
pub static DSH: Dsh = Dsh;

impl Host for Dsh {
    fn id(&self) -> &'static str {
        "dsh"
    }
    fn home_dir(&self) -> &'static str {
        ".dsh"
    }
    fn detect(&self) -> Detect {
        Detect {
            dir_name: Some(".dsh"),
            markers: &["profiles"],
        }
    }
    fn pointer_kind(&self) -> PointerKind {
        PointerKind::Root
    }
    fn register(&self) -> Register {
        Register::NodeEntry {
            node_path: "{home}/profiles/*/node_modules/dsh-aimail/dist/register-cli.js",
            fail_hint:
                "dsh-aimail 插件未就绪——先 `dsh plugin --profile web add dsh-aimail` 装插件再注册",
        }
    }
    fn register_all(&self) -> Option<RegisterAll> {
        None
    }
    fn install_plan(&self) -> &'static [Step] {
        &[Step::Spawn { when: None, argv: &["dsh", "plugin", "--profile", "web", "add", "dsh-aimail", "--config.minimumReleaseAge=0"], env: Some(EnvPairs(&[("DSH_HOME","{home}")])), skip_if: None, on_missing: Some("fail"), fail_hint: Some("`dsh` CLI not found — build/install deepseek-harness first (e.g. cd ~/deepseek-harness && cargo build --release && ln -sf \"$PWD/target/release/dsh\" ~/.local/bin/dsh), then re-run aimail install"), on_error: Some("warn"), warn_hint: Some("dsh plugin add aimail 失败(检查 aimail npm 包发布与网络)"), ok_text: Some("aimail 插件已安装(dsh bundle 层挂载)"), skipped_text: None }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("dsh agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::Print { when: None, text: "  dsh: 绑定 = 创建 dsh session(mail preset)后自动 auto-bind;" }, Step::Print { when: None, text: "        CLI 侧注册:`aimail address -s SID`" }]
    }
    fn uninstall_plan(&self) -> &'static [Step] {
        &[Step::Print { when: None, text: "  dsh: 共享配置已清理;dsh 侧卸载 mail 插件(mail/mail-inbound/tool-mail)+ 删 preset 即可" }]
    }
    fn health_checks(&self) -> &'static [Health] {
        &[]
    }
    fn payload_refs(&self) -> &'static [&'static str] {
        &[]
    }
    fn agent_check_remote(&self) -> bool {
        false
    }
}
