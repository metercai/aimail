//! Subcommand implementation.
//!
//! Ported: `version` (S1) · `stats` / `persona` (S3) · `domain` (list) / `address` (view).
//! `doctor --answers '<json>'`: **verify** self-report item by item (path exists/writable, command executable, sample plugin readable,
//! [`stub::not_yet_ported`] — an explicit non-zero exit; never silently treated as success, never silently degraded.

pub mod address;
pub mod agent;
pub mod bridge;
pub mod check;
pub mod domain;
pub mod install;
pub mod persona;
pub mod ping;
pub mod platform;
pub mod prompt;
pub mod renew;
pub mod repair;
pub mod report;
pub mod reset;
pub mod stats;
pub mod stub;
pub mod uninstall;
pub mod version;
pub mod welcome;
