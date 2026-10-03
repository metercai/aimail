//! 用户面输出助手（`_fail` / `_ok` 的逐字复刻）。
//!
//! 现状码：`cli/aimail:76-83`
//! ```python
//! def _ok(msg):   print(f"  {GREEN}✓{NC} {msg}")
//! def _warn(msg): print(f"  {YELLOW}⚠{NC} {msg}")
//! def _fail(msg): print(f"  {RED}✗{NC} {msg}"); sys.exit(1)
//! ```
//! 注意 `_fail` 走的是 **stdout**（不是 stderr）且 rc=1 —— 门禁按输出原文判读，
//! 所以这两点都必须照抄。颜色常量单点在 [`crate::core::style`]。

use crate::core::style::{GREEN, NC, RED, YELLOW};

/// 成功行（stdout，rc 不动）。
pub fn ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}

/// 警告行（stdout，rc 不动）。
pub fn warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}

/// 失败行（stdout）+ 调用方返回 rc 1。
pub fn fail(msg: &str) -> i32 {
    println!("  {RED}✗{NC} {msg}");
    1
}

#[cfg(test)]
mod tests {
    #[test]
    fn fail_returns_one() {
        assert_eq!(super::fail("x"), 1);
    }
}
