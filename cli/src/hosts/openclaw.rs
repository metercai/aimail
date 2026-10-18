//! openclaw 宿主（node / openclaw-aimail 插件）。
//!
//! 本文件是该宿主的**全部知识**：身份特征、语言类型（由注册入口派生）、注册/装配步骤、体检项。
//! 新增宿主 = 新增本目录下一个模块 + 在 `mod.rs` 注册。

use crate::hosts::exec::{Register, RegisterAll, SkipIf, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::Host;

pub struct Openclaw;
pub static OPENCLAW: Openclaw = Openclaw;

impl Host for Openclaw {
    fn id(&self) -> &'static str {
        "openclaw"
    }
    fn home_dir(&self) -> &'static str {
        ".openclaw"
    }
    fn detect(&self) -> Detect {
        Detect {
            dir_name: None,
            markers: &["openclaw.json"],
        }
    }
    fn pointer_kind(&self) -> PointerKind {
        PointerKind::Root
    }
    fn register(&self) -> Register {
        Register::NodeEntry { node_path: "{home}/npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js", fail_hint: "先安装 openclaw-aimail 插件(`openclaw plugins install openclaw-aimail --force --accept-capabilities` → 重启 gateway);注册经插件自带的 node 入口 --op assemble(owner 2026-10-11:host_command 形态退役)" }
    }
    fn register_all(&self) -> Option<RegisterAll> {
        Some(RegisterAll::NodeEntry {
            node_path: "{home}/npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js",
            fail_hint: "openclaw-aimail 需含独立 node 入口(dist/register-cli.js, --op 门)",
        })
    }
    fn install_plan(&self) -> &'static [Step] {
        &[Step::Print { when: None, text: "openclaw platform: ensure the openclaw-aimail plugin (auto; skipped when present) + register the agent (`aimail address -s SID`):" }, Step::Spawn { when: None, argv: &["openclaw", "plugins", "install", "openclaw-aimail@latest", "--force", "--accept-capabilities"], env: None, skip_if: Some(SkipIf { argv: &["openclaw", "plugins", "list"], match_: "aimail" }), on_missing: Some("fail"), fail_hint: Some("`openclaw` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("openclaw-aimail plugin install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`; keep `@latest` in the spec: a bare name is shadowed by a same-named local dir in cwd"), ok_text: Some("openclaw-aimail plugin installed (npm registry latest stable; takes effect after the gateway restarts)"), skipped_text: Some("openclaw-aimail plugin already present (install skipped)") }, Step::Print { when: None, text: "  restart the openclaw gateway to activate the plugin: `openclaw gateway install` (or `restart`)" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(true), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -s SID`（经插件 node 入口 --op assemble）" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(false), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -s SID` (once pointer/system ready)" }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("openclaw agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::RegisterAll { ok_text: Some("openclaw --all-agents registration complete"), warn_hint: Some("openclaw register-all failed (rerun `aimail reset -s {sid} --all-agents` once the plugin is in place)") }]
    }
    fn uninstall_plan(&self) -> &'static [Step] {
        &[Step::Print { when: None, text: "  openclaw: 对接全在 TS 插件(openclaw-aimail)——插件移除 = openclaw 宿主侧卸载 + AIMail 侧 teardown（经插件 node 入口 --op teardown）;共享系统数据已在上段(网关注销+本机数据)清理" }]
    }
    fn health_checks(&self) -> &'static [Health] {
        &[Health { id: "plugin-installed", spec: HealthSpec::CommandMatch { argv: &["openclaw", "plugins", "list"], match_: "openclaw-aimail", fallback_glob: Some("{user_home}/.openclaw/npm/projects/openclaw-aimail*"), ok_text: "openclaw-aimail 插件在位(openclaw plugins list)", fail_text: "openclaw-aimail 插件未安装", fix: Some("aimail install --home <openclaw home>(自动装;已装则跳过)或 openclaw plugins install openclaw-aimail@latest --force --accept-capabilities") } }, Health { id: "skills", spec: HealthSpec::FileExists { path: "{user_home}/.openclaw/skills/agentmail/SKILL.md", ok_text: "skills/agentmail/SKILL.md present", fail_text: "skills/agentmail/SKILL.md missing", fix: Some("插件自带 skill(openclaw-aimail resources)——检查插件 enabled:openclaw plugins list") } }]
    }
    fn payload_refs(&self) -> &'static [&'static str] {
        &[]
    }
    fn agent_check_remote(&self) -> bool {
        false
    }
}
