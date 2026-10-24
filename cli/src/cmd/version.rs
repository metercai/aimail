//! `aimail version` —— output format identical to Python side: `aimail <ver> (<mode>)`.
//!
//! Python source of truth: `cli/aimail:758-774`
//! - `ver` reads `[project].version` from the repo-root `pyproject.toml`;
//! - `mode` = `dev` | `bootstrapped` (criteria see `core::home::release_mode`).
//!
//! Version source of truth = repo root `pyproject.toml`. Rust side takes version from `Cargo.toml` at compile time,
//! equality of the two is verified by `tests/test_cli_parity.py` (reads and compares both files) and
//! `cli/tests/cli_surface.rs` (output format) together.

pub fn run() -> i32 {
    println!(
        "aimail {} ({})",
        env!("CARGO_PKG_VERSION"),
        crate::core::home::release_mode()
    );
    0
}
