//! ③ 执行面：步骤模型 + 注册入口。数据（各宿主的步骤表）在各宿主模块内，
//! 本模块只定义模型与通用执行语义（真正跑步骤的引擎仍是 `core::steps`）。

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// `env` 对 → JSON 对象（serde 默认把 `[(k, v)]` 序列化成嵌套数组，这里保持对象形状）。
#[derive(Clone, Copy)]
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

/// `when` 前置条件（谓词并集，AND 语义）。
#[derive(Serialize, Clone, Copy)]
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
#[derive(Serialize, Clone, Copy)]
pub struct SkipIf {
    pub argv: &'static [&'static str],
    #[serde(rename = "match")]
    pub match_: &'static str,
}

/// 安装/卸载动作（9 kind）。
#[derive(Serialize, Clone, Copy)]
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

/// hermes 的 profile 判定表（`root_when` 命中 ⇒ 根 profile）。
#[derive(Serialize, Clone, Copy)]
pub struct Profile {
    pub kind: &'static str,
    pub root_when: &'static [&'static str],
}

/// 注册器入口（3 kind：node 宿主 node_entry / python 宿主 python_module|python_script）。
#[derive(Serialize, Clone, Copy)]
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
#[derive(Serialize, Clone, Copy)]
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

/// node 宿主 op 入口 argv 模板（owner 2026-10-11 三来源裁决「op 入口模板两类型各一条
/// 常量」的 node 型；python 型 = `sdk.rs::python_door_command` 函数）。三 node 宿主
/// （pi/dsh/openclaw）共用，不再逐平台复制。占位符：`{op}`/`{args}`/`{node_path}`。
pub const NODE_OP_TEMPLATE: &[&str] = &["node", "{node_path}", "--op", "{op}", "--args", "{args}"];

use serde_json::Value;

use crate::hosts::{Host, Lang};

/// 安装步骤的 JSON 视图（通用引擎 [`crate::core::steps`] 的输入；空表 ⇒ 该宿主无 CLI 侧安装动作）。
pub fn install_steps_json(h: &dyn Host) -> Vec<Value> {
    h.install_plan()
        .iter()
        .map(|s| serde_json::to_value(s).expect("install step must serialize"))
        .collect()
}

/// 卸载步骤的 JSON 视图。
pub fn uninstall_steps_json(h: &dyn Host) -> Vec<Value> {
    h.uninstall_plan()
        .iter()
        .map(|s| serde_json::to_value(s).expect("uninstall step must serialize"))
        .collect()
}

/// 注册入口的 JSON 视图（`core::register` 的输入）。
pub fn register_json(h: &dyn Host) -> Value {
    serde_json::to_value(h.register()).expect("register entry must serialize")
}

/// 全量注册入口（未定义 ⇒ 单 agent 宿主）。
pub fn register_all_json(h: &dyn Host) -> Option<Value> {
    h.register_all()
        .map(|r| serde_json::to_value(r).expect("register_all entry must serialize"))
}

/// 该宿主是否走 python 注册器（决定是否需要 SDK 根：node 宿主经 op 门，不需要）。
pub fn needs_sdk_root(h: &dyn Host) -> bool {
    matches!(h.lang(), Lang::Python)
}

/// 该宿主是否自足 SDK 安装（安装步骤里有 `sdk_install` ⇒ 其 target；否则 `""`）。
pub fn sdk_install_target(h: &dyn Host) -> String {
    for st in h.install_plan() {
        if let Step::SdkInstall { target, .. } = st {
            return target.to_string();
        }
    }
    String::new()
}

/// node 入口模板展开：`{home}` 逐个候选替换；含 `*` 则 glob 取排序首个命中；否则按文件判在。
/// 未命中 ⇒ `""`（不猜）。
pub fn resolve_path_template(tmpl: &str, homes: &[String]) -> String {
    for h in homes {
        let cand = tmpl.replace("{home}", h);
        if cand.contains('*') {
            if let Ok(hits) = glob::glob(&cand) {
                let mut v: Vec<std::path::PathBuf> = hits.flatten().collect();
                v.sort();
                if let Some(first) = v.first() {
                    return first.to_string_lossy().to_string();
                }
            }
        } else if std::path::Path::new(&cand).is_file() {
            return cand;
        }
    }
    String::new()
}
