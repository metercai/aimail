//! terminal color/symbol constants — the single landing spot of the same-named constants on the Python side.
//!
//! Current codes: `cli/aimail:76` (GREEN/RED/YELLOW/NC) and `cli/check_status.py:23-30`
//! (extra BROWN, CHECK=✓, CROSS=✗). Values copied verbatim (including `\033[0;33m` style
//! the "BROWN" naming) — the gate and parity compare against the raw output text; even a one-byte change counts as drift.

pub const GREEN: &str = "\u{1b}[0;32m";
pub const RED: &str = "\u{1b}[0;31m";
pub const YELLOW: &str = "\u{1b}[1;33m";
pub const BROWN: &str = "\u{1b}[0;33m";
pub const NC: &str = "\u{1b}[0m";
/// bold (`BOLD`) — check's final conclusion line uses it (`check_status.py:20`).
pub const BOLD: &str = "\u{1b}[1m";
pub const CHECK: &str = "\u{2713}";
pub const CROSS: &str = "\u{2717}";
