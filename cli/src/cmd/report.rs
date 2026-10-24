//! User-facing output helpers (verbatim replication of `_fail` / `_ok`).
//!
//! Current code: `cli/aimail:76-83`
//! ```python
//! def _ok(msg):   print(f"  {GREEN}✓{NC} {msg}")
//! def _warn(msg): print(f"  {YELLOW}⚠{NC} {msg}")
//! def _fail(msg): print(f"  {RED}✗{NC} {msg}"); sys.exit(1)
//! ```
//! Note that `_fail` goes through **stdout** (not stderr) and rc=1 —— the gate reads the output verbatim,
//! so both of these points must be copied exactly. Color constants are single-sourced in [`crate::core::style`].

use crate::core::style::{GREEN, NC, RED, YELLOW};

/// Success line (stdout, rc unchanged).
pub fn ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}

/// Warning line (stdout, rc unchanged).
pub fn warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}

/// Failure line (stdout) + caller returns rc 1.
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
