//! aimail Rust CLI -- lib shape (the bin is a thin entry over the same impl).
//!
//! Module split (plan v3 §2):
//! - [`app`]  -- clap command tree + dispatch (the command surface is frozen,
//!   see boundary finalization §1);
//! - [`cmd`]  -- each subcommand implementation (S1 only has `version`,
//!   the rest are `not_yet_ported`);
//! - [`core`] -- infrastructure layer (home / perms / contract / platforms /
//!   config ...).

pub mod app;
pub mod cmd;
pub mod core;
pub mod hosts;
