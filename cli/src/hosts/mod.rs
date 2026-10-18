//! 宿主（agent 系统）语义分层。
//!
//! 旧结构是一个数据表 + 一层值视图算法（两个 core 模块，2026-10-18 退役）；
//! 现按功能语义拆成本目录的六个面：
//!
//! | 面 | 模块 | 职责 |
//! |----|------|------|
//! | ① 身份 | [`identity`] | 产品类型 / 语言类型 / home 落点 / 识别规则 / 指针形态 |
//! | ② 运行时 | `runtime`（P3） | 解析器 / 包管理器 / SDK 根（唯一子进程面，惰性） |
//! | ③ 执行 | [`exec`] | 注册 / 全量注册 / 安装步骤 / 卸载步骤 / op 入口 |
//! | ④ 体检 | [`health`] | 健康检查 / 载荷引用 / 远端自证 |
//! | ⑤ 指针 | `pointer`（P6） | 指针路径 / 归属 sid / 扫描 |
//! | ⑥ 目录 | 本模块 | 枚举顺序 / 按 id 取宿主 / `spec --json` 导出 |
//!
//! 对外唯一契约是 [`Host`]：调用方只经 [`get`] / [`all`] 取宿主，不接触任何数据表。
//! 新增一个 agent 类型 = 新增 `hosts/<name>.rs` + 在 [`ALL`] 注册一行，引擎不动。

pub mod exec;
pub mod health;
pub mod identity;
pub mod pointer;
pub mod runtime;

mod deerflow;
mod dsh;
mod hermes;
mod openclaw;
mod pi;

use serde::Serialize;
use serde_json::{json, Map, Value};

use identity::{Detect, PointerKind};

/// agent 系统的实现语言。由 [`Host::register`] 的 kind 派生（单一映射点）。
pub use identity::Lang;

/// 一个宿主系统的全部能力面。
///
/// 语义边界：身份/指针只读文件系统；运行时（P3）是唯一会起子进程的面；
/// 执行/体检只提供数据，真正跑步骤的引擎在 `core::steps` / `core::checks::l2r`。
pub trait Host: Sync {
    // ── ① 身份 ──
    fn id(&self) -> &'static str;
    fn home_dir(&self) -> &'static str;
    fn detect(&self) -> Detect;
    fn pointer_kind(&self) -> PointerKind;

    /// 语言类型：唯一的派生点（node_entry ⇒ Node，其余 ⇒ Python）。
    fn lang(&self) -> Lang {
        match self.register() {
            exec::Register::NodeEntry { .. } => Lang::Node,
            _ => Lang::Python,
        }
    }

    /// 是否需要 python 解释器（node 宿主不需要：其步骤不引用 `{python}`）。
    fn uses_python(&self) -> bool {
        matches!(self.lang(), Lang::Python)
    }

    /// `--agent-home` 语义归谁（默认无人；hermes 是唯一用它定位的平台）。
    fn uses_agent_home(&self) -> bool {
        false
    }

    /// repair 宽松判定时额外认可的路径（默认无；hermes 历史语义还认 `profiles/`）。
    fn repair_hints(&self) -> &'static [&'static str] {
        &[]
    }

    /// node 宿主的 op 入口 argv 模板；`None` ⇒ python 门（`-m aimail.sdk_ops`）。
    fn op_template(&self) -> Option<&'static [&'static str]> {
        match self.lang() {
            Lang::Node => Some(exec::NODE_OP_TEMPLATE),
            Lang::Python => None,
        }
    }

    // ── ③ 执行 ──
    fn register(&self) -> exec::Register;
    fn register_all(&self) -> Option<exec::RegisterAll>;
    fn install_plan(&self) -> &'static [exec::Step];
    fn uninstall_plan(&self) -> &'static [exec::Step];

    // ── ④ 体检 ──
    fn health_checks(&self) -> &'static [health::Health];
    fn agent_check_remote(&self) -> bool;
}

/// 平台枚举顺序（判定序、扫描序、帮助分组都用它）。
pub const ORDER: &[&str] = &["pi", "dsh", "hermes", "openclaw", "deerflow"];

/// 注册表头注（`spec --json` 的 `_comment`）。
pub const COMMENT: &str = "AIMail CLI 平台宿主表 — CLI 侧唯一平台知识源(检测/指针/枚举/注册委托/安装步骤)。适配实现全在 SDK:pysdk(hermes/deer-flow)+ tssdk 包(openclaw 宿主命令/dsh/pi node 入口)。CLI 读本表调度,零平台协议。";

/// 全部宿主（顺序 = [`ORDER`]）。
pub static ALL: &[&HostDef] = &[
    &pi::PI,
    &dsh::DSH,
    &hermes::HERMES,
    &openclaw::OPENCLAW,
    &deerflow::DEERFLOW,
];

/// 全部宿主（顺序 = [`ORDER`]）。
pub fn all() -> &'static [&'static HostDef] {
    ALL
}

/// 按 id 取宿主（不存在 ⇒ None，不猜）。
pub fn get(id: &str) -> Option<&'static HostDef> {
    ALL.iter().copied().find(|h| h.id == id)
}

/// 枚举顺序。
pub fn order() -> &'static [&'static str] {
    ORDER
}

/// 一个宿主的**静态定义**：各 `hosts/<name>.rs` 的唯一内容。
///
/// 序列化形状即 `platform spec --json` 的条目（字段声明序 = 键序）；
/// `#[serde(skip)]` 的两个字段是宿主私有语义（`--agent-home` 归属、repair 宽松线索），
/// 不进值视图。`impl Host` 只在本文件写一次，宿主模块只填数据。
#[derive(Serialize)]
pub struct HostDef {
    #[serde(skip)]
    pub id: &'static str,
    pub detect: Detect,
    pub pointer: PointerKind,
    pub register: exec::Register,
    pub home_dir: &'static str,
    pub install_steps: &'static [exec::Step],
    pub uninstall_steps: &'static [exec::Step],
    pub health_checks: &'static [health::Health],
    #[serde(skip_serializing_if = "Option::is_none")]
    pub register_all: Option<exec::RegisterAll>,
    #[serde(skip_serializing_if = "is_false")]
    pub agent_check_remote: bool,
    #[serde(skip)]
    pub uses_agent_home: bool,
    #[serde(skip)]
    pub repair_hints: &'static [&'static str],
    /// agent 键容器目录（宿主 home 内）：`profiles`（hermes/dsh）、`agents`（openclaw）、
    /// `""`（单 agent 宿主）。只供 `platform spec` 的 agent_keys 扫描，不进值视图。
    #[serde(skip)]
    pub agents_container: &'static str,
}

impl Host for HostDef {
    fn id(&self) -> &'static str {
        self.id
    }
    fn home_dir(&self) -> &'static str {
        self.home_dir
    }
    fn detect(&self) -> Detect {
        self.detect
    }
    fn pointer_kind(&self) -> PointerKind {
        self.pointer
    }
    fn uses_agent_home(&self) -> bool {
        self.uses_agent_home
    }
    fn repair_hints(&self) -> &'static [&'static str] {
        self.repair_hints
    }
    fn register(&self) -> exec::Register {
        self.register
    }
    fn register_all(&self) -> Option<exec::RegisterAll> {
        self.register_all
    }
    fn install_plan(&self) -> &'static [exec::Step] {
        self.install_steps
    }
    fn uninstall_plan(&self) -> &'static [exec::Step] {
        self.uninstall_steps
    }
    fn health_checks(&self) -> &'static [health::Health] {
        self.health_checks
    }
    fn agent_check_remote(&self) -> bool {
        self.agent_check_remote
    }
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// 整份注册表视图（`aimail platform spec --json` 的数据源）。
pub fn describe_all() -> Value {
    let mut platforms = Map::new();
    for h in ALL {
        platforms.insert(
            h.id.to_string(),
            serde_json::to_value(h).expect("host def must serialize"),
        );
    }
    json!({ "_comment": COMMENT, "order": ORDER, "platforms": platforms })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 目录完整性：`all()` 与 `ORDER` 一一对应且顺序一致。
    #[test]
    fn catalog_matches_order() {
        let ids: Vec<&str> = ALL.iter().map(|h| h.id()).collect();
        assert_eq!(ids, ORDER.to_vec());
        for id in ORDER {
            assert!(get(id).is_some(), "catalog missing {id}");
        }
        assert!(get("nope").is_none());
    }

    /// 平台注册表 JSON 已退役（P2c S3b），不得回潮：文件不存在 + 源码零加载引用。
    #[test]
    fn retired_platforms_json_must_not_return() {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let needle = ["plat", "forms.json"].concat();
        assert!(
            !manifest.join(&needle).exists(),
            "平台注册表 JSON 已退役（真源 = hosts/ 各宿主模块），不要恢复该文件"
        );
        let mut offenders: Vec<String> = Vec::new();
        let mut stack = vec![manifest.join("src")];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else {
                continue;
            };
            for e in entries.flatten() {
                let path = e.path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|x| x == "rs") {
                    if let Ok(t) = std::fs::read_to_string(&path) {
                        if t.contains(&needle) {
                            offenders.push(path.display().to_string());
                        }
                    }
                }
            }
        }
        assert!(
            offenders.is_empty(),
            "源码出现平台注册表 JSON 加载引用: {offenders:?}"
        );
    }

    /// 条目一律是 **SDK 根相对**；出现 `pysdk/` 即布局知识回流 ⇒ 红。
    #[test]
    fn registry_data_has_no_repo_layout_paths() {
        let raw = describe_all().to_string();
        assert!(!raw.contains("\"pysdk/"), "注册表数据里出现了仓库布局路径");
        for entry in [
            "hermes/register_profiles.py",
            "deer-flow/manage.py",
            "hermes",
        ] {
            assert!(raw.contains(entry), "注册表应含 SDK 根相对入口 {entry}");
        }
    }

    /// 每个宿主的安装步骤都能序列化成带 kind 的对象（serde 回归钉）。
    #[test]
    fn step_tables_serialize_with_kind() {
        for h in ALL {
            for st in h.install_plan().iter().chain(h.uninstall_plan()) {
                let v = serde_json::to_value(st).expect("step serializes");
                assert!(v.get("kind").is_some(), "{} 的步骤缺少 kind: {v}", h.id());
            }
        }
    }

    /// 语言类型只有一处映射：node 宿主声明 node op 模板，python 宿主没有。
    #[test]
    fn lang_matches_register_kind_and_op_template() {
        for h in ALL {
            match h.register() {
                exec::Register::NodeEntry { .. } => {
                    assert_eq!(h.lang(), Lang::Node, "{}", h.id());
                    assert!(
                        h.op_template().is_some(),
                        "{} 是 node 宿主，必须声明 op 模板",
                        h.id()
                    );
                }
                _ => {
                    assert_eq!(h.lang(), Lang::Python, "{}", h.id());
                    assert!(
                        h.op_template().is_none(),
                        "{} 是 python 宿主，不得声明 node op 模板",
                        h.id()
                    );
                }
            }
        }
    }
}
