//! `aimail repair` — the Rust command surface of `cli/aimail:cmd_repair` + `cli/repair.py`.
//!
//! Python's command surface is just a **spawn wrapper** (`python3 repair.py --system-id/--home/--deep/--dry-run`);
//! The real engine (ten-step ladder + deep extension) is already implemented in `core::repair`, this file connects the command surface to it ——
//! `-s/-H/-D/-n` four surfaces are verbatim consistent with Python (no `--json` machine surface).
//!
//! `-H` goes through `normalize_platform_home` (same standard as Python); `-s` allows empty (the engine decides on its own and prints the header).

use std::path::Path;

pub struct Args {
    pub system_id: String,
    /// `-H/--home`（Python：`normalize_platform_home(Path(...).expanduser())`）
    pub home: Option<String>,
    /// `-D/--deep`: rewrite webhook pairing + flush stuck pending deliveries
    pub deep: bool,
    /// `-n/--dry-run`: only print the repair plan
    pub dry_run: bool,
}

pub fn run(a: &Args) -> i32 {
    let home = match a.home.as_deref() {
        Some(h) => {
            crate::hosts::identity::normalize_platform_home(&crate::core::home::expand_user(h))
                .to_string_lossy()
                .into_owned()
        }
        None => String::new(),
    };
    // In-domain host interpreter binding (Contract §4.1(2) single source of truth): the rebuild ladder must run SDK installation using the host interpreter.
    let _ = if a.system_id.is_empty() {
        crate::core::sdkroot::bind_domain("", &home)
    } else {
        crate::core::sdkroot::bind_domain_for_system(&a.system_id)
    };
    crate::core::repair::run(
        &a.system_id,
        a.deep,
        a.dry_run,
        &home,
        |sid: &str, ah: Option<&Path>| crate::cmd::check::engine(sid, ah, false).check,
    )
}
