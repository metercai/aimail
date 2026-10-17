//! 平台数据 —— 类型化常量（P2c S1 生成件）。
//!
//! 来源：由平台注册表（待退役的 JSON，见 `platforms` 模块）经一次性生成器产出；
//! 生成器随 S3（删 JSON）退役，**本文件自此为平台静态知识的单真源**——改平台
//! 动作/健康项直接改这里（类型系统兜底形状）。
//!
//! 保真：`tests::typed_data_round_trips_the_registry_being_retired` 证明
//! JSON → 类型化 → JSON 往返无损（S3 删文件时随文件一并移除）。
//!
//! 形状（穷举）：Step 9 kind · When 6 谓词 · SkipIf · Health 7 kind ·
//! Register 3 kind · RegisterAll 2 kind。

use serde_json::{json, Map, Value};

/// `when` 前置条件（谓词并集，AND 语义；JSON 序列化只含 Some 字段）。
/// 实测形状：多数步单谓词，hermes 的 spawn 步双谓词（`path_exists` + `runtime_not`）。
pub struct When {
    pub cfg_complete: Option<bool>,
    pub sid_set: Option<bool>,
    pub path_exists: Option<&'static str>,
    pub command_exists: Option<&'static str>,
    pub runtime: Option<&'static str>,
    pub runtime_not: Option<&'static str>,
}

impl When {
    pub fn to_json(&self) -> Value {
        let mut v = Map::new();
        if let Some(b) = self.cfg_complete {
            v.insert("cfg_complete".into(), json!(b));
        }
        if let Some(b) = self.sid_set {
            v.insert("sid_set".into(), json!(b));
        }
        if let Some(t) = self.path_exists {
            v.insert("path_exists".into(), json!(t));
        }
        if let Some(t) = self.command_exists {
            v.insert("command_exists".into(), json!(t));
        }
        if let Some(t) = self.runtime {
            v.insert("runtime".into(), json!(t));
        }
        if let Some(t) = self.runtime_not {
            v.insert("runtime_not".into(), json!(t));
        }
        Value::Object(v)
    }
}

/// `skip_if` 探针：跑 argv，输出含 `match_`（忽略大小写）⇒ "已在场"。
pub struct SkipIf {
    pub argv: &'static [&'static str],
    pub match_: &'static str,
}

impl SkipIf {
    pub fn to_json(&self) -> Value {
        json!({ "argv": self.argv, "match": self.match_ })
    }
}

/// 安装/卸载动作（9 kind，字段并集见 P2c 计划 §8）。
pub enum Step {
    Print {
        when: Option<When>,
        text: &'static str,
    },
    Warn {
        when: Option<When>,
        text: &'static str,
    },
    Spawn {
        when: Option<When>,
        argv: &'static [&'static str],
        env: Option<&'static [(&'static str, &'static str)]>,
        skip_if: Option<SkipIf>,
        on_missing: Option<&'static str>,
        fail_hint: Option<&'static str>,
        on_error: Option<&'static str>,
        warn_hint: Option<&'static str>,
        ok_text: Option<&'static str>,
        skipped_text: Option<&'static str>,
    },
    RegisterDefault {
        when: Option<When>,
        on_error: Option<&'static str>,
        warn_hint: Option<&'static str>,
    },
    RegisterAll {
        ok_text: Option<&'static str>,
        warn_hint: Option<&'static str>,
    },
    SdkInstall {
        target: &'static str,
        fn_name: &'static str,
        ok_text: Option<&'static str>,
        fail_hint: Option<&'static str>,
        on_error: Option<&'static str>,
    },
    SdkUninstall {
        target: &'static str,
        fn_name: &'static str,
    },
    RmPointer {
        path: &'static str,
        ok_text: Option<&'static str>,
    },
    RmDir {
        path: &'static str,
        ok_text: Option<&'static str>,
    },
}

impl Step {
    pub fn kind(&self) -> &'static str {
        match self {
            Step::Print { .. } => "print",
            Step::Warn { .. } => "warn",
            Step::Spawn { .. } => "spawn",
            Step::RegisterDefault { .. } => "register_default",
            Step::RegisterAll { .. } => "register_all",
            Step::SdkInstall { .. } => "sdk_install",
            Step::SdkUninstall { .. } => "sdk_uninstall",
            Step::RmPointer { .. } => "rm_pointer",
            Step::RmDir { .. } => "rm_dir",
        }
    }

    pub fn to_json(&self) -> Value {
        let mut v = Map::new();
        v.insert("kind".into(), json!(self.kind()));
        match self {
            Step::Print { when, text } => {
                if let Some(w) = when {
                    v.insert("when".into(), w.to_json());
                }
                v.insert("text".into(), json!(text));
            }
            Step::Warn { when, text } => {
                if let Some(w) = when {
                    v.insert("when".into(), w.to_json());
                }
                v.insert("text".into(), json!(text));
            }
            Step::Spawn {
                when,
                argv,
                env,
                skip_if,
                on_missing,
                fail_hint,
                on_error,
                warn_hint,
                ok_text,
                skipped_text,
            } => {
                if let Some(w) = when {
                    v.insert("when".into(), w.to_json());
                }
                v.insert("argv".into(), json!(argv));
                if let Some(e) = env {
                    let mut m = Map::new();
                    for (k, val) in *e {
                        m.insert(k.to_string(), json!(val));
                    }
                    v.insert("env".into(), Value::Object(m));
                }
                if let Some(s) = skip_if {
                    v.insert("skip_if".into(), s.to_json());
                }
                for (k, val) in [
                    ("on_missing", on_missing),
                    ("fail_hint", fail_hint),
                    ("on_error", on_error),
                    ("warn_hint", warn_hint),
                    ("ok_text", ok_text),
                    ("skipped_text", skipped_text),
                ] {
                    if let Some(s) = val {
                        v.insert(k.into(), json!(s));
                    }
                }
            }
            Step::RegisterDefault {
                when,
                on_error,
                warn_hint,
            } => {
                if let Some(w) = when {
                    v.insert("when".into(), w.to_json());
                }
                if let Some(s) = on_error {
                    v.insert("on_error".into(), json!(s));
                }
                if let Some(s) = warn_hint {
                    v.insert("warn_hint".into(), json!(s));
                }
            }
            Step::RegisterAll { ok_text, warn_hint } => {
                if let Some(s) = ok_text {
                    v.insert("ok_text".into(), json!(s));
                }
                if let Some(s) = warn_hint {
                    v.insert("warn_hint".into(), json!(s));
                }
            }
            Step::SdkInstall {
                target,
                fn_name,
                ok_text,
                fail_hint,
                on_error,
            } => {
                v.insert("target".into(), json!(target));
                v.insert("fn".into(), json!(fn_name));
                if let Some(s) = ok_text {
                    v.insert("ok_text".into(), json!(s));
                }
                if let Some(s) = fail_hint {
                    v.insert("fail_hint".into(), json!(s));
                }
                if let Some(s) = on_error {
                    v.insert("on_error".into(), json!(s));
                }
            }
            Step::SdkUninstall { target, fn_name } => {
                v.insert("target".into(), json!(target));
                v.insert("fn".into(), json!(fn_name));
            }
            Step::RmPointer { path, ok_text } | Step::RmDir { path, ok_text } => {
                v.insert("path".into(), json!(path));
                if let Some(s) = ok_text {
                    v.insert("ok_text".into(), json!(s));
                }
            }
        }
        Value::Object(v)
    }
}

/// 运行时健康检查项（7 kind）。
pub enum Health {
    PointerMatch {
        id: &'static str,
        path: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileContains {
        id: &'static str,
        path: &'static str,
        marker: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileContainsAlt {
        id: &'static str,
        path: &'static str,
        alt: &'static str,
        marker: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileExists {
        id: &'static str,
        path: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
        fix: Option<&'static str>,
    },
    FileExistsAny {
        id: &'static str,
        paths: &'static [&'static str],
        ok_text: &'static str,
        fail_text: &'static str,
    },
    CommandMatch {
        id: &'static str,
        argv: &'static [&'static str],
        match_: &'static str,
        fallback_glob: Option<&'static str>,
        ok_text: &'static str,
        fail_text: &'static str,
        fix: Option<&'static str>,
    },
    YamlToolsets {
        id: &'static str,
        configs: &'static [&'static str],
        ok_text: &'static str,
        fail_text: &'static str,
    },
}

impl Health {
    pub fn kind(&self) -> &'static str {
        match self {
            Health::PointerMatch { .. } => "pointer_match",
            Health::FileContains { .. } => "file_contains",
            Health::FileContainsAlt { .. } => "file_contains_alt",
            Health::FileExists { .. } => "file_exists",
            Health::FileExistsAny { .. } => "file_exists_any",
            Health::CommandMatch { .. } => "command_match",
            Health::YamlToolsets { .. } => "yaml_toolsets",
        }
    }

    pub fn id(&self) -> &'static str {
        match self {
            Health::PointerMatch { id, .. }
            | Health::FileContains { id, .. }
            | Health::FileContainsAlt { id, .. }
            | Health::FileExists { id, .. }
            | Health::FileExistsAny { id, .. }
            | Health::CommandMatch { id, .. }
            | Health::YamlToolsets { id, .. } => id,
        }
    }

    pub fn ok_text(&self) -> &'static str {
        match self {
            Health::PointerMatch { ok_text, .. }
            | Health::FileContains { ok_text, .. }
            | Health::FileContainsAlt { ok_text, .. }
            | Health::FileExists { ok_text, .. }
            | Health::FileExistsAny { ok_text, .. }
            | Health::CommandMatch { ok_text, .. }
            | Health::YamlToolsets { ok_text, .. } => ok_text,
        }
    }

    pub fn fail_text(&self) -> &'static str {
        match self {
            Health::PointerMatch { fail_text, .. }
            | Health::FileContains { fail_text, .. }
            | Health::FileContainsAlt { fail_text, .. }
            | Health::FileExists { fail_text, .. }
            | Health::FileExistsAny { fail_text, .. }
            | Health::CommandMatch { fail_text, .. }
            | Health::YamlToolsets { fail_text, .. } => fail_text,
        }
    }

    pub fn fix(&self) -> Option<&'static str> {
        match self {
            Health::FileExists { fix, .. } | Health::CommandMatch { fix, .. } => *fix,
            _ => None,
        }
    }

    pub fn to_json(&self) -> Value {
        let mut v = Map::new();
        v.insert("id".into(), json!(self.id()));
        v.insert("kind".into(), json!(self.kind()));
        match self {
            Health::PointerMatch { path, .. } => {
                v.insert("path".into(), json!(path));
            }
            Health::FileContains { path, marker, .. } => {
                v.insert("path".into(), json!(path));
                v.insert("marker".into(), json!(marker));
            }
            Health::FileContainsAlt {
                path, alt, marker, ..
            } => {
                v.insert("path".into(), json!(path));
                v.insert("alt".into(), json!(alt));
                v.insert("marker".into(), json!(marker));
            }
            Health::FileExists { path, fix, .. } => {
                v.insert("path".into(), json!(path));
                if let Some(f) = fix {
                    v.insert("fix".into(), json!(f));
                }
            }
            Health::FileExistsAny { paths, .. } => {
                v.insert("paths".into(), json!(paths));
            }
            Health::CommandMatch {
                argv,
                match_,
                fallback_glob,
                fix,
                ..
            } => {
                v.insert("argv".into(), json!(argv));
                v.insert("match".into(), json!(match_));
                if let Some(f) = fallback_glob {
                    v.insert("fallback_glob".into(), json!(f));
                }
                if let Some(f) = fix {
                    v.insert("fix".into(), json!(f));
                }
            }
            Health::YamlToolsets { configs, .. } => {
                v.insert("configs".into(), json!(configs));
            }
        }
        v.insert("ok_text".into(), json!(self.ok_text()));
        v.insert("fail_text".into(), json!(self.fail_text()));
        Value::Object(v)
    }
}

/// 注册器入口（3 kind：node 宿主 node_entry / python 宿主 python_module|python_script）。
pub enum Register {
    NodeEntry {
        node_path: &'static str,
        args: Option<&'static [&'static str]>,
        fail_hint: &'static str,
    },
    PythonModule {
        module: &'static str,
        fn_name: &'static str,
        profile_kind: &'static str,
        root_when: &'static [&'static str],
        pythonpath: &'static [&'static str],
    },
    PythonScript {
        python: &'static str,
        args: &'static [&'static str],
        fail_hint: &'static str,
    },
}

impl Register {
    pub fn is_python(&self) -> bool {
        matches!(
            self,
            Register::PythonModule { .. } | Register::PythonScript { .. }
        )
    }

    pub fn to_json(&self) -> Value {
        match self {
            Register::NodeEntry {
                node_path,
                args,
                fail_hint,
            } => {
                let mut v = Map::new();
                v.insert("kind".into(), json!("node_entry"));
                v.insert("node_path".into(), json!(node_path));
                if let Some(a) = args {
                    v.insert("args".into(), json!(a));
                }
                v.insert("fail_hint".into(), json!(fail_hint));
                Value::Object(v)
            }
            Register::PythonModule {
                module,
                fn_name,
                profile_kind,
                root_when,
                pythonpath,
            } => {
                json!({
                    "kind": "python_module",
                    "module": module,
                    "fn": fn_name,
                    "profile": { "kind": profile_kind, "root_when": root_when },
                    "pythonpath": pythonpath,
                })
            }
            Register::PythonScript {
                python,
                args,
                fail_hint,
            } => {
                json!({
                    "kind": "python_script",
                    "python": python,
                    "args": args,
                    "fail_hint": fail_hint,
                })
            }
        }
    }
}

/// 全量注册入口（`register_all`；pi/dsh 无 ⇒ None）。
pub enum RegisterAll {
    NodeEntry {
        node_path: &'static str,
        fail_hint: &'static str,
    },
    PythonScript {
        python: &'static str,
        args: &'static [&'static str],
        env: &'static [(&'static str, &'static str)],
        fail_hint: &'static str,
    },
}

impl RegisterAll {
    pub fn to_json(&self) -> Value {
        match self {
            RegisterAll::NodeEntry {
                node_path,
                fail_hint,
            } => {
                json!({ "kind": "node_entry", "node_path": node_path, "fail_hint": fail_hint })
            }
            RegisterAll::PythonScript {
                python,
                args,
                env,
                fail_hint,
            } => {
                let mut v = Map::new();
                v.insert("kind".into(), json!("python_script"));
                v.insert("python".into(), json!(python));
                v.insert("args".into(), json!(args));
                if !env.is_empty() {
                    let mut m = Map::new();
                    for (k, val) in *env {
                        m.insert(k.to_string(), json!(val));
                    }
                    v.insert("env".into(), Value::Object(m));
                }
                v.insert("fail_hint".into(), json!(fail_hint));
                Value::Object(v)
            }
        }
    }
}

pub struct Detect {
    pub dir_name: Option<&'static str>,
    pub markers: &'static [&'static str],
}

impl Detect {
    pub fn to_json(&self) -> Value {
        let mut v = Map::new();
        if let Some(d) = self.dir_name {
            v.insert("dir_name".into(), json!(d));
        }
        v.insert("markers".into(), json!(self.markers));
        Value::Object(v)
    }
}

pub struct Pointer {
    pub kind: &'static str,
    pub file: &'static str,
}

impl Pointer {
    pub fn to_json(&self) -> Value {
        json!({ "kind": self.kind, "file": self.file })
    }
}

/// 一个平台的全部静态知识（顶层键穷举见 P2c 计划 §8）。
pub struct PlatformDef {
    pub id: &'static str,
    pub home_dir: &'static str,
    pub detect: Detect,
    pub pointer: Pointer,
    pub register: Register,
    pub register_all: Option<RegisterAll>,
    pub ops: Option<&'static [&'static str]>,
    pub install_steps: &'static [Step],
    pub uninstall_steps: &'static [Step],
    pub health_checks: &'static [Health],
    pub payload_refs: &'static [&'static str],
    pub agent_check_remote: bool,
}

impl PlatformDef {
    pub fn to_json(&self) -> Value {
        let mut v = Map::new();
        v.insert("detect".into(), self.detect.to_json());
        v.insert("pointer".into(), self.pointer.to_json());
        v.insert("register".into(), self.register.to_json());
        if let Some(o) = self.ops {
            let mut m = Map::new();
            m.insert("argv".to_string(), json!(o));
            v.insert("ops".into(), Value::Object(m));
        }
        v.insert("home_dir".into(), json!(self.home_dir));
        v.insert(
            "install_steps".into(),
            self.install_steps
                .iter()
                .map(Step::to_json)
                .collect::<Vec<_>>()
                .into(),
        );
        v.insert(
            "uninstall_steps".into(),
            self.uninstall_steps
                .iter()
                .map(Step::to_json)
                .collect::<Vec<_>>()
                .into(),
        );
        v.insert(
            "health_checks".into(),
            self.health_checks
                .iter()
                .map(Health::to_json)
                .collect::<Vec<_>>()
                .into(),
        );
        if !self.payload_refs.is_empty() {
            v.insert("payload_refs".into(), json!(self.payload_refs));
        }
        if let Some(ra) = &self.register_all {
            v.insert("register_all".into(), ra.to_json());
        }
        if self.agent_check_remote {
            v.insert("agent_check_remote".into(), json!(true));
        }
        Value::Object(v)
    }
}

/// 平台枚举顺序（唯一顺序源；与 detect 常量表同序）。
pub const ORDER: &[&str] = &["pi", "dsh", "hermes", "openclaw", "deerflow"];

/// node 宿主 op 入口 argv 模板（owner 2026-10-11 三来源裁决「op 入口模板两类型各一条
/// 常量」的 node 型；python 型 = `sdk.rs::python_door_command` 函数）。三 node 宿主
/// （pi/dsh/openclaw）共用，不再逐平台复制。占位符：`{op}`/`{args}`/`{node_path}`。
pub const NODE_OP_TEMPLATE: &[&str] = &["node", "{node_path}", "--op", "{op}", "--args", "{args}"];

/// 注册表头注（原 JSON `_comment`；保真棘轮逐字比对）。
pub const COMMENT: &str = "AIMail CLI 平台注册表 — CLI 侧唯一平台知识源(Phase 1: 检测/指针/枚举/别名/注册委托)。适配实现全在 SDK:pysdk(hermes/deer-flow)+ tssdk 包(openclaw 宿主命令/dsh/pi node 入口)。CLI 读本表调度,零平台协议。";

/// 五平台全部静态知识（S3 后为单真源）。
pub const PLATFORMS: &[PlatformDef] = &[
PlatformDef {
        id: "pi",
        home_dir: ".pi",
        detect: Detect { dir_name: Some(".pi"), markers: &["agent"] },
        pointer: Pointer { kind: "root", file: ".agentmail" },
        register: Register::NodeEntry { node_path: "{home}/agent/npm/node_modules/pi-aimail/dist/register-cli.js", args: Some(&["--name", "{name}", "--system-id", "{sid}", "--manager", "{manager}"]), fail_hint: "pi-aimail 扩展未就绪——先 `pi install npm:pi-aimail` 装扩展再注册" },
        register_all: None,
        ops: Some(NODE_OP_TEMPLATE),
        install_steps: &[Step::Print { when: None, text: "pi platform: ensure the pi-aimail extension (auto; skipped when present) + register the agent:" }, Step::Spawn { when: None, argv: &["pi", "install", "npm:pi-aimail"], env: None, skip_if: Some(SkipIf { argv: &["pi", "list"], match_: "pi-aimail" }), on_missing: Some("fail"), fail_hint: Some("`pi` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("pi-aimail extension install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`"), ok_text: Some("pi-aimail extension installed (npm registry latest stable; takes effect after the pi session restarts)"), skipped_text: Some("pi-aimail extension already present (install skipped)") }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("pi agent registration failed (see above) — rerun `aimail reset -s {sid}` once the extension is in place (地址名由 SDK 决定，CLI 不涉及)") }, Step::Warn { when: Some(When { cfg_complete: Some(false), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "pi registration skipped (config incomplete — retry after activation/install, or run `aimail address -a pi`)" }, Step::Print { when: None, text: "  pi: inbound listener lives with the pi session lifecycle (restart the session to apply)" }],
        uninstall_steps: &[Step::RmPointer { path: "{home}/.agentmail", ok_text: Some("removed pointer {home}/.agentmail") }, Step::RmDir { path: "{home}/skills/agentmail", ok_text: Some("removed skill {home}/skills/agentmail") }, Step::Print { when: None, text: "  pi: 扩展移除 = pi 宿主侧移除 pi-aimail 扩展" }],
        health_checks: &[Health::PointerMatch { id: "pointer", path: "{user_home}/.pi/.agentmail", ok_text: ".pi pointer matches system", fail_text: ".pi pointer missing/mismatch" }],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "dsh",
        home_dir: ".dsh",
        detect: Detect { dir_name: Some(".dsh"), markers: &["profiles"] },
        pointer: Pointer { kind: "root", file: ".agentmail" },
        register: Register::NodeEntry { node_path: "{home}/profiles/*/node_modules/dsh-aimail/dist/register-cli.js", args: Some(&["--name", "{name}", "--system-id", "{sid}", "--manager", "{manager}"]), fail_hint: "dsh-aimail 插件未就绪——先 `dsh plugin --profile web add dsh-aimail` 装插件再注册" },
        register_all: None,
        ops: Some(NODE_OP_TEMPLATE),
        install_steps: &[Step::Spawn { when: None, argv: &["dsh", "plugin", "--profile", "web", "add", "dsh-aimail", "--config.minimumReleaseAge=0"], env: Some(&[("DSH_HOME","{home}")]), skip_if: None, on_missing: Some("fail"), fail_hint: Some("`dsh` CLI not found — build/install deepseek-harness first (e.g. cd ~/deepseek-harness && cargo build --release && ln -sf \"$PWD/target/release/dsh\" ~/.local/bin/dsh), then re-run aimail install"), on_error: Some("warn"), warn_hint: Some("dsh plugin add aimail 失败(检查 aimail npm 包发布与网络)"), ok_text: Some("aimail 插件已安装(dsh bundle 层挂载)"), skipped_text: None }, Step::RegisterDefault { when: Some(When { cfg_complete: Some(true), sid_set: None, path_exists: None, command_exists: None, runtime: None, runtime_not: None }), on_error: Some("warn"), warn_hint: Some("dsh agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::Print { when: None, text: "  dsh: 绑定 = 创建 dsh session(mail preset)后自动 auto-bind;" }, Step::Print { when: None, text: "        CLI 侧注册:`aimail address -s <sid> -a agent`" }],
        uninstall_steps: &[Step::Print { when: None, text: "  dsh: 共享配置已清理;dsh 侧卸载 mail 插件(mail/mail-inbound/tool-mail)+ 删 preset 即可" }],
        health_checks: &[],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "hermes",
        home_dir: ".hermes",
        detect: Detect { dir_name: None, markers: &["hermes-agent"] },
        pointer: Pointer { kind: "root_or_profiles", file: ".agentmail" },
        register: Register::PythonModule { module: "aimail_hermes", fn_name: "_auto_register_email", profile_kind: "root_or_named", root_when: &["default", "{home_name}"], pythonpath: &["hermes"] },
        register_all: Some(RegisterAll::PythonScript { python: "hermes/register_profiles.py", args: &[], env: &[("AIMAIL_SYSTEM_ID","{sid}")], fail_hint: "register_profiles 执行失败(见上输出)" }),
        ops: None,
        install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: Some("{home}/hermes-agent"), command_exists: None, runtime: None, runtime_not: Some("docker") }), argv: &["sh", "-c", "P='{python}'; if command -v uv >/dev/null 2>&1; then echo 'pkgmgr=uv'; uv pip install --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; else echo 'pkgmgr=pip'; \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true; \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; fi"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(宿主包管理器自适配 uv→venv pip→ensurepip 三径均不可用) exit {rc}(webhook 适配运行时缺失?)"), ok_text: Some("aimailsdk installed via uv|pip into {python}"), skipped_text: None }, Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), argv: &["docker", "exec", "{container}", "sh", "-c", "cd /; unset UV_EXCLUDE_NEWER UV_INDEX_URL UV_INDEX PIP_INDEX_URL PIP_EXTRA_INDEX_URL 2>/dev/null; P=/opt/hermes/.venv/bin/python; [ -x \"$P\" ] || P=\"{container_home}/.venv/bin/python\"; ok=0\nif command -v uv >/dev/null 2>&1; then\n  [ -x \"$P\" ] || uv venv \"$P\" >/dev/null 2>&1 || true\n  for i in 1 2 3; do uv pip install --no-cache --upgrade --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk && { ok=1; break; }; sleep 8; done\nfi\nif [ \"$ok\" = 0 ]; then\n  echo pkgmgr=pip\n  \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true\n  for j in 1 2 3; do \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk && { ok=1; break; }; sleep 8; done\nfi\n\"$P\" -c \"import aimail\" >/dev/null 2>&1 || ok=0\n[ \"$ok\" = 1 ] || { echo '✗ aimailsdk 安装失败(uv 与 venv pip 两径均失败)'; exit 1; }\necho \"aimailsdk=$(\"$P\" -c 'import importlib.metadata as m;print(m.version(\"aimailsdk\"))' 2>/dev/null)\"\n"], env: None, skip_if: None, on_missing: Some("fail"), fail_hint: Some("docker CLI not found — --container mode requires docker on the host PATH"), on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(容器内包管理器自适配 uv→venv pip→ensurepip 三径) exit {rc} —— register/sdk 文件步骤已由 sdk_install 落盘, 可人工 docker exec <c> <home>/.venv/bin/python -m pip install aimailsdk 后重跑 install"), ok_text: Some("aimailsdk installed inside container {container} (uv|pip adapted)"), skipped_text: None }, Step::SdkInstall { target: "hermes", fn_name: "install_hermes", ok_text: Some("hermes SDK install done(重启 hermes gateway 生效)"), fail_hint: None, on_error: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("hermes 主 agent 注册失败(见上)——稍后 reset 重试") }, Step::RegisterAll { ok_text: Some("hermes --all-agents 全量注册完成"), warn_hint: Some("hermes 全量注册失败(可 reset --all-agents 重试)") }, Step::Print { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), text: "  docker: 重启宿主 = `docker restart {container}`; 容器重建后必须重跑 aimail install -H <home> … --container {container}(幂等)" }],
        uninstall_steps: &[Step::SdkUninstall { target: "hermes", fn_name: "uninstall_hermes" }, Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: Some("{home}/hermes-agent"), command_exists: None, runtime: None, runtime_not: None }), argv: &["{python}", "-m", "pip", "uninstall", "-y", "aimailsdk"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("pip uninstall aimailsdk exit {rc}(webhook 适配运行时残留)"), ok_text: None, skipped_text: None }],
        health_checks: &[Health::FileContains { id: "patch-webhook", path: "{home}/hermes-agent/gateway/platforms/webhook.py", marker: "PREPROCESS_REGISTRY", ok_text: "webhook.py patched (PREPROCESS_REGISTRY present)", fail_text: "webhook.py NOT patched or missing" }, Health::FileContainsAlt { id: "patch-profiles", path: "{home}/hermes-agent/hermes_cli/profiles.py", alt: "{home}/hermes-agent/cli/profiles.py", marker: "AimailGateway", ok_text: "profiles.py patched (AimailGateway hook present)", fail_text: "profiles.py NOT patched or missing" }, Health::FileExistsAny { id: "skills", paths: &["{home}/skills/agentmail/SKILL.md", "{home}/profiles/*/skills/agentmail/SKILL.md"], ok_text: "at least one profile has skills/agentmail/SKILL.md", fail_text: "no profile has skills/agentmail/SKILL.md" }, Health::YamlToolsets { id: "toolsets", configs: &["{home}/config.yaml", "{home}/profiles/*/config.yaml"], ok_text: "platform_toolsets contains agentmail", fail_text: "platform_toolsets lacks agentmail" }],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "openclaw",
        home_dir: ".openclaw",
        detect: Detect { dir_name: None, markers: &["openclaw.json"] },
        pointer: Pointer { kind: "root", file: ".agentmail" },
        register: Register::NodeEntry { node_path: "{home}/npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js", args: None, fail_hint: "先安装 openclaw-aimail 插件(`openclaw plugins install openclaw-aimail --force --accept-capabilities` → 重启 gateway);注册经插件自带的 node 入口 --op assemble(owner 2026-10-11:host_command 形态退役)" },
        register_all: Some(RegisterAll::NodeEntry { node_path: "{home}/npm/projects/*/node_modules/openclaw-aimail/dist/register-cli.js", fail_hint: "openclaw-aimail 需含独立 node 入口(dist/register-cli.js, --op 门)" }),
        ops: Some(NODE_OP_TEMPLATE),
        install_steps: &[Step::Print { when: None, text: "openclaw platform: ensure the openclaw-aimail plugin (auto; skipped when present) + register the agent (single agent: `aimail address -s SID -a main`):" }, Step::Spawn { when: None, argv: &["openclaw", "plugins", "install", "openclaw-aimail@latest", "--force", "--accept-capabilities"], env: None, skip_if: Some(SkipIf { argv: &["openclaw", "plugins", "list"], match_: "aimail" }), on_missing: Some("fail"), fail_hint: Some("`openclaw` executable not found (platform detected but host CLI missing?)"), on_error: Some("warn"), warn_hint: Some("openclaw-aimail plugin install failed (check network/registry) — after fixing, rerun `aimail reset -s {sid}`; keep `@latest` in the spec: a bare name is shadowed by a same-named local dir in cwd"), ok_text: Some("openclaw-aimail plugin installed (npm registry latest stable; takes effect after the gateway restarts)"), skipped_text: Some("openclaw-aimail plugin already present (install skipped)") }, Step::Print { when: None, text: "  restart the openclaw gateway to activate the plugin: `openclaw gateway install` (or `restart`)" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(true), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -a main`（经插件 node 入口 --op assemble）" }, Step::Print { when: Some(When { cfg_complete: None, sid_set: Some(false), path_exists: None, command_exists: None, runtime: None, runtime_not: None }), text: "  register: `aimail address -a main` (once pointer/system ready)" }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("openclaw main agent registration failed (see above) — rerun `aimail reset -s {sid}` once the plugin is in place") }, Step::RegisterAll { ok_text: Some("openclaw --all-agents registration complete"), warn_hint: Some("openclaw register-all failed (rerun `aimail reset -s {sid} --all-agents` once the plugin is in place)") }],
        uninstall_steps: &[Step::Print { when: None, text: "  openclaw: 对接全在 TS 插件(openclaw-aimail)——插件移除 = openclaw 宿主侧卸载 + AIMail 侧 teardown（经插件 node 入口 --op teardown）;共享系统数据已在上段(网关注销+本机数据)清理" }],
        health_checks: &[Health::CommandMatch { id: "plugin-installed", argv: &["openclaw", "plugins", "list"], match_: "openclaw-aimail", fallback_glob: Some("{user_home}/.openclaw/npm/projects/openclaw-aimail*"), ok_text: "openclaw-aimail 插件在位(openclaw plugins list)", fail_text: "openclaw-aimail 插件未安装", fix: Some("aimail install --home <openclaw home>(自动装;已装则跳过)或 openclaw plugins install openclaw-aimail@latest --force --accept-capabilities") }, Health::FileExists { id: "skills", path: "{user_home}/.openclaw/skills/agentmail/SKILL.md", ok_text: "skills/agentmail/SKILL.md present", fail_text: "skills/agentmail/SKILL.md missing", fix: Some("插件自带 skill(openclaw-aimail resources)——检查插件 enabled:openclaw plugins list") }],
        payload_refs: &[],
        agent_check_remote: false,
        },
        PlatformDef {
        id: "deerflow",
        home_dir: ".deer-flow",
        detect: Detect { dir_name: None, markers: &["backend/app/gateway"] },
        pointer: Pointer { kind: "root", file: ".agentmail" },
        register: Register::PythonScript { python: "deer-flow/manage.py", args: &["register", "--system-id", "{sid}", "--agent", "{agent}", "--manager", "{manager}", "--name", "{name}"], fail_hint: "deer-flow manage.py 注册器失败——检查 deer-flow 模块与系统配置" },
        register_all: Some(RegisterAll::PythonScript { python: "deer-flow/manage.py", args: &["register", "--all", "--system-id", "{sid}"], env: &[], fail_hint: "manage.py register --all 失败(见上输出)" }),
        ops: None,
        install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: Some("python3"), runtime: None, runtime_not: None }), argv: &["sh", "-c", "P=\"{python}\"; command -v \"$P\" >/dev/null 2>&1 || P=python3; if command -v uv >/dev/null 2>&1; then uv pip install --python \"$P\" -q aimailsdk && exit 0; fi; \"$P\" -m pip install -q --break-system-packages aimailsdk 2>/dev/null || \"$P\" -m pip install -q aimailsdk"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(宿主 python 自适配 uv→pip(含 PEP668 --break-system-packages) 均不可用) exit {rc}"), ok_text: Some("aimailsdk installed into the SAME interpreter the CLI resolves (${AIMAIL_PYTHON:-python3})"), skipped_text: None }, Step::SdkInstall { target: "deerflow", fn_name: "install_deerflow", ok_text: Some("deerflow SDK install done"), fail_hint: None, on_error: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-skill.sh"], env: Some(&[("AIMAIL_SYSTEM_ID","{sid}")]), skip_if: None, on_missing: None, fail_hint: Some("deer-flow install-skill.sh exit {rc}(关键装配步: skills 落点; 失败必须可见, 不许当成功)"), on_error: Some("fail"), warn_hint: None, ok_text: None, skipped_text: None }, Step::Spawn { when: None, argv: &["bash", "{sdk}/deer-flow/install-mcp.sh"], env: Some(&[("AIMAIL_SYSTEM_ID","{sid}")]), skip_if: None, on_missing: None, fail_hint: None, on_error: Some("fail"), warn_hint: Some("deer-flow install-mcp.sh exit {rc}(关键装配步: MCP toolset 落点; 失败必须可见, 不许当成功)"), ok_text: None, skipped_text: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("deerflow 主 agent 注册失败(见上)——稍后 reset 重试") }, Step::RegisterAll { ok_text: Some("deerflow --all-agents 全量注册完成"), warn_hint: Some("deerflow 全量注册失败(可 reset --all-agents 重试)") }],
        uninstall_steps: &[Step::SdkUninstall { target: "deerflow", fn_name: "uninstall_deerflow" }, Step::Print { when: None, text: "  deerflow: skill/mcp 宿主装配残留可用宿主侧管理清理" }],
        health_checks: &[Health::FileContainsAlt { id: "patch-app", path: "{home}/backend/app/gateway/app.py", alt: "{home}/app/gateway/app.py", marker: "aimail_inbound", ok_text: "app.py patched (aimail_inbound router present)", fail_text: "app.py NOT patched or missing" }],
        payload_refs: &["{home}/extensions_config.json"],
        agent_check_remote: true,
        },
];

/// 按 id 取平台定义（不存在 ⇒ None，不猜）。
pub fn def(id: &str) -> Option<&'static PlatformDef> {
    PLATFORMS.iter().find(|p| p.id == id)
}
