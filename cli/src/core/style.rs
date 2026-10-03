//! 终端颜色/符号常量 —— Python 侧同名常量的单一落点。
//!
//! 现状码：`cli/aimail:76`（GREEN/RED/YELLOW/NC）与 `cli/check_status.py:23-30`
//! （多出 BROWN、CHECK=✓、CROSS=✗）。数值逐字照抄（含 `\033[0;33m` 这种
//! "BROWN" 叫法）—— 门禁与 parity 按输出原文比对，改一个字节都算漂移。

pub const GREEN: &str = "\u{1b}[0;32m";
pub const RED: &str = "\u{1b}[0;31m";
pub const YELLOW: &str = "\u{1b}[1;33m";
pub const BROWN: &str = "\u{1b}[0;33m";
pub const NC: &str = "\u{1b}[0m";
pub const CHECK: &str = "\u{2713}";
pub const CROSS: &str = "\u{2717}";
