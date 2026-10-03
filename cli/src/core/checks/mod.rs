//! check 引擎的探针分组（按 plan v3 S4 的切片逐个落）。
//!
//! | 模块 | 层 | 现状码 | 状态 |
//! |---|---|---|---|
//! | [`l0`] | L0 配置（gateway/绑定文件/桥配置） | `cli/check_status.py:983-1185` | 已落 |
//! | `l1` | L1 gateway（health/SMTP/凭据） | `cli/check_status.py:906-982` | 待落 |
//! | `l2` | L2 bridge + 运行时资源 + 载荷 | `cli/check_status.py:1205-1460` | 待落 |
//! | `l3l4` | L3 agent 配置 + L4 hook（五平台适配） | `cli/check_status.py:185-799` | 待落 |
//!
//! 所有探针只**产出记录**，不打印、不退出 —— 输出与退出码统一由
//! [`crate::core::check::Check`] 与命令面负责（现状码也是这个分层）。

pub mod l0;
pub mod l1;
pub mod l2;
