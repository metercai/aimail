//! `aimail version` —— 与 Python 侧输出逐字同形：`aimail <ver> (<mode>)`。
//!
//! Python 真源：`cli/aimail:758-774`
//! - `ver` 取仓根 `pyproject.toml` 的 `[project].version`；
//! - `mode` = `dev` | `bootstrapped`（判据见 `core::home::release_mode`）。
//!
//! 版本号真源 = 仓根 `pyproject.toml`。Rust 侧编译期取 `Cargo.toml` 的 version，
//! 两者相等由 `tests/test_cli_parity.py`（读两个文件比对）与
//! `cli/tests/cli_surface.rs`（输出格式）共同兜住。

pub fn run() -> i32 {
    println!(
        "aimail {} ({})",
        env!("CARGO_PKG_VERSION"),
        crate::core::home::release_mode()
    );
    0
}
