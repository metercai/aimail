//! 子命令实现。
//!
//! 已移植：`version`（S1）· `stats` / `persona`（S3）· `domain`（列表）/ `address`（查看）。
//! 其余子命令与各命令的未移植面在 clap 树里已注册（命令面冻结），分发到
//! [`stub::not_yet_ported`] —— 明确的非零退出，绝不静默当成功、也不静默降级。

pub mod address;
pub mod bridge;
pub mod check;
pub mod domain;
pub mod install;
pub mod persona;
pub mod ping;
pub mod renew;
pub mod report;
pub mod reset;
pub mod stats;
pub mod stub;
pub mod uninstall;
pub mod version;
pub mod welcome;
