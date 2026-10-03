//! aimail Rust CLI —— 库形态（bin 是同一份实现的薄入口）。
//!
//! 模块划分（plan v3 §2）：
//! - [`app`]  —— clap 命令树 + 分发（命令面是冻结面，见边界定稿 §1）；
//! - [`cmd`]  —— 各子命令实现（S1 只有 `version`，其余 `not_yet_ported`）；
//! - [`core`] —— 基础设施层（home / perms / contract / platforms / config …）。

pub mod app;
pub mod cmd;
pub mod core;
