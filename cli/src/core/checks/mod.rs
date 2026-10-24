//! Probe grouping for the check engine (implemented slice by slice according to plan v3 S4).
//!
//! | Module | Layer | Current Code | Status |
//! |---|---|---|---|
//! | [`l0`] | L0 configuration (gateway/binding files/bridge config) | `cli/check_status.py:983-1185` | Implemented |
//! | `l1` | L1 gateway (health/SMTP/credentials) | `cli/check_status.py:906-982` | Pending |
//! | `l2` | L2 bridge + runtime resources + payload | `cli/check_status.py:1205-1460` | Pending |
//! | `l3l4` | L3 agent config + L4 hook (five-platform adaptation) | `cli/check_status.py:185-799` | Pending |
//!
//! All probes only **produce records**, no printing, no exiting — output and exit codes are unified by
//! [`crate::core::check::Check`] and the command interface (the current code also follows this layering).

pub mod adapters;
pub mod l0;
pub mod l1;
pub mod l2;
pub mod l2r;
