//! `aimail repair` —— `cli/aimail:cmd_repair` + `cli/repair.py` 的 Rust 命令面。
//!
//! Python 的命令面只是个 **spawn 包装**（`python3 repair.py --system-id/--home/--deep/--dry-run`）；
//! 真正的引擎（十步阶梯 + deep 扩展）早已在 `core::repair` 落地，本文件把命令面接上它 ——
//! `-s/-H/-D/-n` 四个面与 Python 逐字一致（无 `--json` 机器面）。
//!
//! `-H` 走 `normalize_platform_home`（与 Python 同口径），`-s` 允许空（引擎自行判定并打印 header）。

use std::path::Path;

pub struct Args {
    pub system_id: String,
    /// `-H/--home`（Python：`normalize_platform_home(Path(...).expanduser())`）
    pub home: Option<String>,
    /// `-D/--deep`：改写 webhook 配对 + 冲掉卡住待投递
    pub deep: bool,
    /// `-n/--dry-run`：只打印修复计划
    pub dry_run: bool,
}

pub fn run(a: &Args) -> i32 {
    let home = match a.home.as_deref() {
        Some(h) => {
            crate::core::platforms::normalize_platform_home(&crate::core::home::expand_user(h))
                .to_string_lossy()
                .into_owned()
        }
        None => String::new(),
    };
    crate::core::repair::run(
        &a.system_id,
        a.deep,
        a.dry_run,
        &home,
        |sid: &str, ah: Option<&Path>| crate::cmd::check::engine(sid, ah, false).check,
    )
}
