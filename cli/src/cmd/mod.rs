//! 子命令实现。
//!
//! S1 只实现 `version`；其余 14 个子命令在 clap 树里已注册（命令面冻结），
//! 分发到 [`stub::not_yet_ported`] —— 明确的非零退出，绝不静默当成功。

pub mod stub;
pub mod version;
