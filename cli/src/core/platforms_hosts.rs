//! 平台宿主表 —— 五个宿主是谁 / 装在哪 / 怎么装 / 怎么体检：CLI 侧唯一平台知识源。
//!
//! 边界：**协议适配不在本表**（预设、别名、定名、注册参数全在 SDK 内部）。本表只承载
//! 「宿主世界知识 + 装配步骤」，供 CLI 在 SDK 尚未落地的阶段独立决策：
//! install（认平台 → 装插件 → 落 SDK）、check/repair（体检表驱动）、
//! uninstall（清理动作）、address/stats（指针归属）。依赖方向是 CLI → 本表 → 值视图；
//! SDK 不读本表，也不需要知道它的存在。
//!
//! 序列化：`#[derive(Serialize)]` 直出值视图（`platforms::registry()`）。字段声明顺序
//! 即 JSON 键序（serde_json `preserve_order`），`skip_serializing_if` 复刻「仅非空才写」。
//! 形状门禁 = `aimail platform spec --json` 黑盒（tests/cli/test_platforms_registry.py
//! + aimail-advanced tests/cli/check-registry.py）。
//!
//! 形状（穷举）：Step 9 kind · When 6 谓词 · SkipIf · Health 7 kind ·
//! Register 3 kind · RegisterAll 2 kind。

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// `env` 对 → JSON 对象（serde 默认把 `[(k, v)]` 序列化成嵌套数组，这里保持对象形状）。
pub struct EnvPairs(pub &'static [(&'static str, &'static str)]);

impl EnvPairs {
    fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl Serialize for EnvPairs {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(self.0.len()))?;
        for (k, v) in self.0 {
            m.serialize_entry(k, v)?;
        }
        m.end()
    }
}

fn is_false(b: &bool) -> bool {
    !*b
}

fn no_payload_refs(v: &&'static [&'static str]) -> bool {
    v.is_empty()
}

/// `when` 前置条件（谓词并集，AND 语义）。
#[derive(Serialize)]
pub struct When {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cfg_complete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sid_set: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path_exists: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command_exists: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runtime_not: Option<&'static str>,
}

/// `skip_if` 探针：跑 argv，输出含 `match`（忽略大小写）⇒「已在场」。
#[derive(Serialize)]
pub struct SkipIf {
    pub argv: &'static [&'static str],
    #[serde(rename = "match")]
    pub match_: &'static str,
}

/// 安装/卸载动作（9 kind）。
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Step {
    Print {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        text: &'static str,
    },
    Warn {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        text: &'static str,
    },
    Spawn {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        argv: &'static [&'static str],
        #[serde(skip_serializing_if = "Option::is_none")]
        env: Option<EnvPairs>,
        #[serde(skip_serializing_if = "Option::is_none")]
        skip_if: Option<SkipIf>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_missing: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        fail_hint: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_error: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warn_hint: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        skipped_text: Option<&'static str>,
    },
    RegisterDefault {
        #[serde(skip_serializing_if = "Option::is_none")]
        when: Option<When>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_error: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warn_hint: Option<&'static str>,
    },
    RegisterAll {
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        warn_hint: Option<&'static str>,
    },
    SdkInstall {
        target: &'static str,
        #[serde(rename = "fn")]
        fn_name: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        fail_hint: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        on_error: Option<&'static str>,
    },
    SdkUninstall {
        target: &'static str,
        #[serde(rename = "fn")]
        fn_name: &'static str,
    },
    RmPointer {
        path: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
    },
    RmDir {
        path: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        ok_text: Option<&'static str>,
    },
}

/// 运行时健康检查项：`id` 在外层，`kind` + 变体字段由 [`HealthSpec`] 平铺展开。
#[derive(Serialize)]
pub struct Health {
    pub id: &'static str,
    #[serde(flatten)]
    pub spec: HealthSpec,
}

/// 健康检查的 7 种 kind（`ok_text`/`fail_text` 恒在尾部，保持既有键序）。
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum HealthSpec {
    PointerMatch {
        path: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileContains {
        path: &'static str,
        marker: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileContainsAlt {
        path: &'static str,
        alt: &'static str,
        marker: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileExists {
        path: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        fix: Option<&'static str>,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileExistsAny {
        paths: &'static [&'static str],
        ok_text: &'static str,
        fail_text: &'static str,
    },
    CommandMatch {
        argv: &'static [&'static str],
        #[serde(rename = "match")]
        match_: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        fallback_glob: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        fix: Option<&'static str>,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    YamlToolsets {
        configs: &'static [&'static str],
        ok_text: &'static str,
        fail_text: &'static str,
    },
}

/// hermes 的 profile 判定表（`root_when` 命中 ⇒ 根 profile）。
#[derive(Serialize)]
pub struct Profile {
    pub kind: &'static str,
    pub root_when: &'static [&'static str],
}

/// 注册器入口（3 kind：node 宿主 node_entry / python 宿主 python_module|python_script）。
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Register {
    NodeEntry {
        node_path: &'static str,
        fail_hint: &'static str,
    },
    PythonModule {
        module: &'static str,
        #[serde(rename = "fn")]
        fn_name: &'static str,
        profile: Profile,
        pythonpath: &'static [&'static str],
    },
    PythonScript {
        python: &'static str,
        args: &'static [&'static str],
        fail_hint: &'static str,
    },
}

/// 全量注册入口（2 kind；未定义 ⇒ 单 agent 平台）。
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum RegisterAll {
    NodeEntry {
        node_path: &'static str,
        fail_hint: &'static str,
    },
    PythonScript {
        python: &'static str,
        args: &'static [&'static str],
        #[serde(skip_serializing_if = "EnvPairs::is_empty")]
        env: EnvPairs,
        fail_hint: &'static str,
    },
}

/// 平台识别特征：`dir_name`（可选目录名闸）+ `markers`（必命中）。
#[derive(Serialize)]
pub struct Detect {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dir_name: Option<&'static str>,
    pub markers: &'static [&'static str],
}

/// 指针形态（文件名归契约 `contract::pointer_file()`，不在本表）。
#[derive(Serialize)]
pub struct Pointer {
    pub kind: &'static str,
}

/// 一个平台的完整定义（`id` 只作映射键，不进 JSON）。
#[derive(Serialize)]
pub struct PlatformDef {
    #[serde(skip)]
    pub id: &'static str,
    pub detect: Detect,
    pub pointer: Pointer,
    pub register: Register,
    pub home_dir: &'static str,
    pub install_steps: &'static [Step],
    pub uninstall_steps: &'static [Step],
    pub health_checks: &'static [Health],
    #[serde(skip_serializing_if = "no_payload_refs")]
    pub payload_refs: &'static [&'static str],
    #[serde(skip_serializing_if = "Option::is_none")]
    pub register_all: Option<RegisterAll>,
    #[serde(skip_serializing_if = "is_false")]
    pub agent_check_remote: bool,
}

pub const ORDER: &[&str] = &["pi", "dsh", "hermes", "openclaw", "deerflow"];

/// node 宿主 op 入口 argv 模板（owner 2026-10-11 三来源裁决「op 入口模板两类型各一条
/// 常量」的 node 型；python 型 = `sdk.rs::python_door_command` 函数）。三 node 宿主
/// （pi/dsh/openclaw）共用，不再逐平台复制。占位符：`{op}`/`{args}`/`{node_path}`。
pub const NODE_OP_TEMPLATE: &[&str] = &["node", "{node_path}", "--op", "{op}", "--args", "{args}"];

/// 注册表头注（原 JSON `_comment`；保真棘轮逐字比对）。
pub const COMMENT: &str = "AIMail CLI 平台注册表 — CLI 侧唯一平台知识源(检测/指针/枚举/注册委托/安装步骤)。适配实现全在 SDK:pysdk(hermes/deer-flow)+ tssdk 包(openclaw 宿主命令/dsh/pi node 入口)。CLI 读本表调度,零平台协议。";

/// 五平台全部静态知识（S3 后为单真源）。
pub const PLATFORMS: &[PlatformDef] = &[
PlatformDef {
        id: "pi",
        home_dir: ".pi",
        detect: Detect { dir_name: Some(".pi"), markers: &["agent"] },
        pointer: Pointer { kind: "root" },
        register: Register::NodeEntry { node_path: "{home}/agent/npm/node_modules/pi-aimail/dist/register-cli.js", fail_hint: "pi-aimail 扩展未就绪——先 `pi install npm:pi-aimail` 装扩展再注册" },
        register_all: None,
        install_steps: &[Step::Print { when: None, text: "pi platform: ensure the pi-aimail extension (auto; skipped when present) + register the agent:" }, Step::Spawn { when: None, argv: &["pi", "install", "npm:pi-aimail"], env: None, skip_if: Some(SkipIf { argv: &["pi", "list"], match_: "pi-aimail" }), on_missing: Some("fail"), fail_hint: Some("`pi` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("pi-aimail extension install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`"), ok_text: Some("pi-aimail extension installed (npm registry latest stable; takes effect after the pi session restarts)"), skipped_text: Some("pi-aimail extension already present (install skipped)") }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("pi agent registration failed (see above) — rerun `aimail reset -s {sid}` once the extension is in place (地址名由 SDK 决定，CLI 不涉及)") }, Step::Warn { when: Some(When { cfg_complete: Some(false), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "pi registration skipped (config incomplete — retry after activation/install, or run `aimail address -s SID`)" }, Step::Print { when: None, text: "  pi: inbound listener lives with the pi session lifecycle (restart the session to apply)" }],
        uninstall_steps: &[Step::RmPointer { path: "{home}/.agentmail", ok_text: Some("removed pointer {home}/.agentmail") }, Step::RmDir { path: "{home}/skills/agentmail", ok_text: Some("removed skill {home}/skills/agentmail") }, Step::Print { when: None, text: "  pi: 扩展移除 = pi 宿主侧移除 pi-aimail 扩展" }],
        health_checks: &[Health { id: "pointer", spec: HealthSpec::PointerMatch { path: "{user_home}/.pi/.agentmail", ok_text: ".pi pointer matches system", fail_text: ".pi pointer missing/mismatch" } }],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "dsh",
        home_dir: ".dsh",
        detect: Detect { dir_name: Some(".dsh"), markers: &["profiles"] },
        pointer: Pointer { kind: "root" },
        register: Register::NodeEntry { node_path: "{home}/profiles/*/node_modules/dsh-aimail/dist/register-cli.js", fail_hint: "dsh-aimail 插件未就绪——先 `dsh plugin --profile web add dsh-aimail` 装插件再注册" },
        register_all: None,
        install_steps: &[Step::Spawn { when: None, argv: &["dsh", "plugin", "--profile", "web", "add", "dsh-aimail", "--config.minimumReleaseAge=0"], env: Some(EnvPairs(&[("DSH_HOME","{home}")])), skip_if: None, on_missing: Some("fail"), fail_hint: Some("`dsh` CLI not found — build/install deepseek-harness first (e.g. cd ~/deepseek-harness && cargo build --release && ln -sf \"$PWD/target/release/dsh\" ~/.local/bin/dsh), then re-run aimail install"), on_error: Some("warn"), warn_hint: Some("dsh plugin add aimail 失败(检查 aimail npm 包发布与网络)"), ok_text: Some("aimail 插件已安装(dsh bundle 层挂载)"), skipped_text: None }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("dsh agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::Print { when: None, text: "  dsh: 绑定 = 创建 dsh session(mail preset)后自动 auto-bind;" }, Step::Print { when: None, text: "        CLI 侧注册:`aimail address -s SID`" }],
        uninstall_steps: &[Step::Print { when: None, text: "  dsh: 共享配置已清理;dsh 侧卸载 mail 插件(mail/mail-inbound/tool-mail)+ 删 preset 即可" }],
        health_checks: &[],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "hermes",
        home_dir: ".hermes",
        detect: Detect { dir_name: None, markers: &["hermes-agent"] },
        pointer: Pointer { kind: "root_or_profiles" },
        register: Register::PythonModule { module: "aimail_hermes", fn_name: "_auto_register_email", profile: Profile { kind: "root_or_named", root_when: &["default", "{home_name}"] }, pythonpath: &["hermes"] },
        register_all: Some(RegisterAll::PythonScript { python: "hermes/register_profiles.py", args: &[], env: EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")]), fail_hint: "register_profiles 执行失败(见上输出)" }),
        install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: Some("{home}/hermes-agent"), command_exists: None, runtime: None, runtime_not: Some("docker") }), argv: &["sh", "-c", "P='{python}'; if command -v uv >/dev/null 2>&1; then echo 'pkgmgr=uv'; uv pip install --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; else echo 'pkgmgr=pip'; \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true; \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; fi"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(宿主包管理器自适配 uv→venv pip→ensurepip 三径均不可用) exit {rc}(webhook 适配运行时缺失?)"), ok_text: Some("aimailsdk installed via uv|pip into {python}"), skipped_text: None }, Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), argv: &["docker", "exec", "{container}", "sh", "-c", "cd /; unset UV_EXCLUDE_NEWER UV_INDEX_URL UV_INDEX PIP_INDEX_URL PIP_EXTRA_INDEX_URL 2>/dev/null; P=/opt/hermes/.venv/bin/python; [ -x \"$P\" ] || P=\"{container_home}/.venv/bin/python\"; ok=0\nif command -v uv >/dev/null 2>&1; then\n  [ -x \"$P\" ] || uv venv \"$P\" >/dev/null 2>&1 || true\n  for i in 1 2 3; do uv pip install --no-cache --upgrade --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk && { ok=1; break; }; sleep 8; done\nfi\nif [ \"$ok\" = 0 ]; then\n  echo pkgmgr=pip\n  \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true\n  for j in 1 2 3; do \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk && { ok=1; break; }; sleep 8; done\nfi\n\"$P\" -c \"import aimail\" >/dev/null 2>&1 || ok=0\n[ \"$ok\" = 1 ] || { echo '✗ aimailsdk 安装失败(uv 与 venv pip 两径均失败)'; exit 1; }\necho \"aimailsdk=$(\"$P\" -c 'import importlib.metadata as m;print(m.version(\"aimailsdk\"))' 2>/dev/null)\"\n"], env: None, skip_if: None, on_missing: Some("fail"), fail_hint: Some("docker CLI not found — --container mode requires docker on the host PATH"), on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(容器内包管理器自适配 uv→venv pip→ensurepip 三径) exit {rc} —— register/sdk 文件步骤已由 sdk_install 落盘, 可人工 docker exec <c> <home>/.venv/bin/python -m pip install aimailsdk 后重跑 install"), ok_text: Some("aimailsdk installed inside container {container} (uv|pip adapted)"), skipped_text: None }, Step::SdkInstall { target: "hermes", fn_name: "install_hermes", ok_text: Some("hermes SDK install done(重启 hermes gateway 生效)"), fail_hint: None, on_error: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("hermes 主 agent 注册失败(见上)——稍后 reset 重试") }, Step::RegisterAll { ok_text: Some("hermes --all-agents 全量注册完成"), warn_hint: Some("hermes 全量注册失败(可 reset --all-agents 重试)") }, Step::Print { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), text: "  docker: 重启宿主 = `docker restart {container}`; 容器重建后必须重跑 aimail install -H <home> … --container {container}(幂等)" }],
        uninstall_steps: &[Step::SdkUninstall { target: "hermes", fn_name: "uninstall_hermes" }, Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: Some("{home}/hermes-agent"), command_exists: None, runtime: None, runtime_not: None }), argv: &["{python}", "-m", "pip", "uninstall", "-y", "aimailsdk"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("pip uninstall aimailsdk exit {rc}(webhook 适配运行时残留)"), ok_text: None, skipped_text: None }],
        health_checks: &[Health { id: "patch-webhook", spec: HealthSpec::FileContains { path: "{home}/hermes-agent/gateway/platforms/webhook.py", marker: "PREPROCESS_REGISTRY", ok_text: "webhook.py patched (PREPROCESS_REGISTRY present)", fail_text: "webhook.py NOT patched or missing" } }, Health { id: "patch-profiles", spec: HealthSpec::FileContainsAlt { path: "{home}/hermes-agent/hermes_cli/profiles.py", alt: "{home}/hermes-agent/cli/profiles.py", marker: "AimailGateway", ok_text: "profiles.py patched (AimailGateway hook present)", fail_text: "profiles.py NOT patched or missing" } }, Health { id: "skills", spec: HealthSpec::FileExistsAny { paths: &["{home}/skills/agentmail/SKILL.md", "{home}/profiles/*/skills/agentmail/SKILL.md"], ok_text: "at least one profile has skills/agentmail/SKILL.md", fail_text: "no profile has skills/agentmail/SKILL.md" } }, Health { id: "toolsets", spec: HealthSpec::YamlToolsets { configs: &["{home}/config.yaml", "{home}/profiles/*/config.yaml"], ok_text: "platform_toolsets contains agentmail", fail_text: "platform_toolsets lacks agentmail" } }],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "openclaw",
        home_dir: ".openclaw",
        detect: Detect { dir_name: None, markers: &["openclaw.json"] },
        pointer: Pointer { kind: "root" },
        register: Register::NodeEntry { node_path: "{home}/npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js", fail_hint: "先安装 openclaw-aimail 插件(`openclaw plugins install openclaw-aimail --force --accept-capabilities` → 重启 gateway);注册经插件自带的 node 入口 --op assemble(owner 2026-10-11:host_command 形态退役)" },
        register_all: Some(RegisterAll::NodeEntry { node_path: "{home}/npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js", fail_hint: "openclaw-aimail 需含独立 node 入口(dist/register-cli.js, --op 门)" }),
        install_steps: &[Step::Print { when: None, text: "openclaw platform: ensure the openclaw-aimail plugin (auto; skipped when present) + register the agent (`aimail address -s SID`):" }, Step::Spawn { when: None, argv: &["openclaw", "plugins", "install", "openclaw-aimail@latest", "--force", "--accept-capabilities"], env: None, skip_if: Some(SkipIf { argv: &["openclaw", "plugins", "list"], match_: "aimail" }), on_missing: Some("fail"), fail_hint: Some("`openclaw` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("openclaw-aimail plugin install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`; keep `@latest` in the spec: a bare name is shadowed by a same-named local dir in cwd"), ok_text: Some("openclaw-aimail plugin installed (npm registry latest stable; takes effect after the gateway restarts)"), skipped_text: Some("openclaw-aimail plugin already present (install skipped)") }, Step::Print { when: None, text: "  restart the openclaw gateway to activate the plugin: `openclaw gateway install` (or `restart`)" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(true), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -s SID`（经插件 node 入口 --op assemble）" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(false), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -s SID` (once pointer/system ready)" }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("openclaw agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::RegisterAll { ok_text: Some("openclaw --all-agents registration complete"), warn_hint: Some("openclaw register-all failed (rerun `aimail reset -s {sid} --all-agents` once the plugin is in place)") }],
        uninstall_steps: &[Step::Print { when: None, text: "  openclaw: 对接全在 TS 插件(openclaw-aimail)——插件移除 = openclaw 宿主侧卸载 + AIMail 侧 teardown（经插件 node 入口 --op teardown）;共享系统数据已在上段(网关注销+本机数据)清理" }],
        health_checks: &[Health { id: "plugin-installed", spec: HealthSpec::CommandMatch { argv: &["openclaw", "plugins", "list"], match_: "openclaw-aimail", fallback_glob: Some("{user_home}/.openclaw/npm/projects/openclaw-aimail*"), ok_text: "openclaw-aimail 插件在位(openclaw plugins list)", fail_text: "openclaw-aimail 插件未安装", fix: Some("aimail install --home <openclaw home>(自动装;已装则跳过)或 openclaw plugins install openclaw-aimail@latest --force --accept-capabilities") } }, Health { id: "skills", spec: HealthSpec::FileExists { path: "{user_home}/.openclaw/skills/agentmail/SKILL.md", ok_text: "skills/agentmail/SKILL.md present", fail_text: "skills/agentmail/SKILL.md missing", fix: Some("插件自带 skill(openclaw-aimail resources)——检查插件 enabled:openclaw plugins list") } }],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "deerflow",
        home_dir: ".deer-flow",
        detect: Detect { dir_name: None, markers: &["backend/app/gateway"] },
        pointer: Pointer { kind: "root" },
        register: Register::PythonScript { python: "deer-flow/manage.py", args: &["register", "--system-id", "{sid}", "--agent", "{agent}", "--manager", "{manager}", "--name", "{name}"], fail_hint: "deer-flow manage.py 注册器失败——检查 deer-flow 模块与系统配置" },
        register_all: Some(RegisterAll::PythonScript { python: "deer-flow/manage.py", args: &["register", "--all", "--system-id", "{sid}"], env: EnvPairs(&[]), fail_hint: "manage.py register --all 失败(见上输出)" }),
        install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: Some("python3"), runtime: None, runtime_not: None }), argv: &["sh", "-c", "P=\"{python}\"; command -v \"$P\" >/dev/null 2>&1 || P=python3; if command -v uv >/dev/null 2>&1; then uv pip install --python \"$P\" -q aimailsdk && exit 0; fi; \"$P\" -m pip install -q --break-system-packages aimailsdk 2>/dev/null || \"$P\" -m pip install -q aimailsdk"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(宿主 python 自适配 uv→pip(含 PEP668 --break-system-packages) 均不可用) exit {rc}"), ok_text: Some("aimailsdk installed into the SAME interpreter the CLI resolves (${AIMAIL_PYTHON:-python3})"), skipped_text: None }, Step::SdkInstall { target: "deerflow", fn_name: "install_deerflow", ok_text: Some("deerflow SDK install done"), fail_hint: None, on_error: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-skill.sh"], env: Some(EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")])), skip_if: None, on_missing: None, fail_hint: Some("deer-flow install-skill.sh exit {rc}(关键装配步: skills 落点; 失败必须可见, 不许当成功)"), on_error: Some("fail"), warn_hint: None, ok_text: None, skipped_text: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-mcp.sh"], env: Some(EnvPairs(&[("AIMAIL_SYSTEM_ID","{sid}")])), skip_if: None, on_missing: None, fail_hint: None, on_error: Some("fail"), warn_hint: Some("deer-flow install-mcp.sh exit {rc}(关键装配步: MCP toolset 落点; 失败必须可见, 不许当成功)"), ok_text: None, skipped_text: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("deerflow 主 agent 注册失败(见上)——稍后 reset 重试") }, Step::RegisterAll { ok_text: Some("deerflow --all-agents 全量注册完成"), warn_hint: Some("deerflow 全量注册失败(可 reset --all-agents 重试)") }],
        uninstall_steps: &[Step::SdkUninstall { target: "deerflow", fn_name: "uninstall_deerflow" }, Step::Print { when: None, text: "  deerflow: skill/mcp 宿主装配残留可用宿主侧管理清理" }],
        health_checks: &[Health { id: "patch-app", spec: HealthSpec::FileContainsAlt { path: "{home}/backend/app/gateway/app.py", alt: "{home}/app/gateway/app.py", marker: "aimail_inbound", ok_text: "app.py patched (aimail_inbound router present)", fail_text: "app.py NOT patched or missing" } }],
        payload_refs: &["{home}/extensions_config.json"],
        agent_check_remote: true,
        },
];
