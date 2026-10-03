//! 未移植子命令的诚实出口。
//!
//! 为什么不是"静默成功"：CLI 门禁与用户脚本按退出码判读，静默 0 会把"没做事"
//! 读成"做好了"。plan v3 S1 的验收要求 = 未实现命令返回明确的非零退出。

/// 打印一行英文说明到 stderr（用户面全英文），返回退出码 1。
pub fn not_yet_ported(name: &str) -> i32 {
    eprintln!(
        "aimail: '{name}' is not yet ported to the Rust CLI \
         (see cli-rust-ization-plan-v3 S3-S9); the Python CLI is still authoritative"
    );
    1
}
