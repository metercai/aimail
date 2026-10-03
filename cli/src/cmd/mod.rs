//! 子命令实现。
//!
//! 已移植：`version`（S1）· `stats` / `persona`（S3，只读面）。
//! 其余子命令在 clap 树里已注册（命令面冻结），分发到 [`stub::not_yet_ported`]
//! —— 明确的非零退出，绝不静默当成功。

pub mod persona;
pub mod stats;
pub mod stub;
pub mod version;
