//! `aimail platform` — machine-readable export of the detection surface (contract v1.0 §5).
//!
//! `aimail platform spec [--home <dir>] [--json]`：
//!   · Without `--home` ⇒ print the platform static knowledge table (the "CLI probe module" part of the constant tripartite);
//!   · With `--home` ⇒ append the probe results for that home (hit means platform/transport/agent key/pointer/entry;
//!     probe failure is presented as `{"error": "…"}`, forbidding `unknown` to masquerade as success).
//!
//! Why exported by CLI: gates and fixtures originally read platform registry JSON files directly (removed in S3b); the single source of truth is still
//! the constants in `core::detect`; this command is just an assertable export interface (Contract §5).

pub struct Args {
    pub home: Option<String>,
}

pub fn run(args: &Args) -> i32 {
    let home = args.home.as_deref().map(std::path::Path::new);
    let v = crate::core::detect::spec_json(home, None);
    match serde_json::to_string_pretty(&v) {
        Ok(s) => {
            println!("{s}");
            0
        }
        Err(e) => {
            eprintln!("platform spec: json encode failed: {e}");
            1
        }
    }
}
