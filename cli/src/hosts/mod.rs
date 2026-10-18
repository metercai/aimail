//! 宿主（agent 系统）语义分层。
//!
//! 旧结构 `core/platforms_hosts.rs`（数据）+ `core/platforms.rs`（值视图 + FS 算法）
//! 已按功能语义拆成本目录的六个面：
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
    fn payload_refs(&self) -> &'static [&'static str];
    fn agent_check_remote(&self) -> bool;
}

/// 平台枚举顺序（判定序、扫描序、帮助分组都用它）。
pub const ORDER: &[&str] = &["pi", "dsh", "hermes", "openclaw", "deerflow"];

/// 注册表头注（`spec --json` 的 `_comment`）。
pub const COMMENT: &str = "AIMail CLI 平台宿主表 — CLI 侧唯一平台知识源(检测/指针/枚举/注册委托/安装步骤)。适配实现全在 SDK:pysdk(hermes/deer-flow)+ tssdk 包(openclaw 宿主命令/dsh/pi node 入口)。CLI 读本表调度,零平台协议。";

/// 全部宿主（顺序 = [`ORDER`]）。
pub static ALL: &[&'static dyn Host] = &[
    &pi::PI,
    &dsh::DSH,
    &hermes::HERMES,
    &openclaw::OPENCLAW,
    &deerflow::DEERFLOW,
];

/// 全部宿主（顺序 = [`ORDER`]）。
pub fn all() -> &'static [&'static dyn Host] {
    ALL
}

/// 按 id 取宿主（不存在 ⇒ None，不猜）。
pub fn get(id: &str) -> Option<&'static dyn Host> {
    ALL.iter().copied().find(|h| h.id() == id)
}

/// 枚举顺序。
pub fn order() -> &'static [&'static str] {
    ORDER
}

/// `spec --json` 的单平台视图（字段声明序 = JSON 键序，逐字节保真）。
#[derive(Serialize)]
struct HostDesc {
    detect: Detect,
    pointer: PointerKind,
    register: exec::Register,
    home_dir: &'static str,
    install_steps: &'static [exec::Step],
    uninstall_steps: &'static [exec::Step],
    health_checks: &'static [health::Health],
    #[serde(skip_serializing_if = "refs_empty")]
    payload_refs: &'static [&'static str],
    #[serde(skip_serializing_if = "Option::is_none")]
    register_all: Option<exec::RegisterAll>,
    #[serde(skip_serializing_if = "is_false")]
    agent_check_remote: bool,
}

fn refs_empty(v: &&'static [&'static str]) -> bool {
    v.is_empty()
}

fn is_false(b: &bool) -> bool {
    !*b
}

/// 整份注册表视图（`aimail platform spec --json` 的数据源）。
pub fn describe_all() -> Value {
    let mut platforms = Map::new();
    for h in ALL {
        let d = HostDesc {
            detect: h.detect(),
            pointer: h.pointer_kind(),
            register: h.register(),
            home_dir: h.home_dir(),
            install_steps: h.install_plan(),
            uninstall_steps: h.uninstall_plan(),
            health_checks: h.health_checks(),
            payload_refs: h.payload_refs(),
            register_all: h.register_all(),
            agent_check_remote: h.agent_check_remote(),
        };
        platforms.insert(
            h.id().to_string(),
            serde_json::to_value(&d).expect("host desc must serialize"),
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
