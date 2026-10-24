//! ② Runtime facet: parser / SDK root.
//!
//! This is the **only fact facet that spawns child processes**, so it is triggered on demand by the caller (`aimail address` / `stats`
//! such read-only commands must not go through here). The detection engine is still in [`crate::core::sdkroot`]:
//! Locates the interpreter based on the actual layout of `--home` (env override → process binding → in-container /proc measurement → venv layout →
//! hermes PM `facts.json` → nested venv → official toolchain); returns empty if not found,
//! does not guess. This module only serves as a facade and host language dispatcher.
//!
//! The package manager has no Rust-side probing: the adaptive uv → venv pip → ensurepip logic is written in the host's install steps
//! in the shell (`install_plan` in `hosts/*.rs`), determined by the target environment itself.

use std::path::{Path, PathBuf};

use crate::core::sdkroot;
use crate::hosts::Host;

/// Domain interpreter: required for python hosts, not needed for node hosts (`{python}` does not appear in their steps).
/// It also binds the result to process-local state (`sdkroot::bind_domain`), so the python gate and child processes share the same interpreter.
pub fn python(h: &dyn Host, home: &Path, container: &str) -> String {
    if !h.uses_python() {
        return String::new();
    }
    sdkroot::bind_domain(container, &home.to_string_lossy())
}

/// Core directory of the installed SDK (re-verify after `install` that the "package really landed in the detected interpreter").
pub fn sdk_root(python: &str) -> Option<PathBuf> {
    if python.is_empty() {
        return None;
    }
    sdkroot::resolve_with(python).ok().map(|r| r.path)
}
