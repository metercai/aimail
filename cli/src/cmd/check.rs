//! `aimail check` —— full machine health check (config / runtime resources / delivery chain).
//!
//! Orchestration aligns line-by-line with Python `check_status.py:1482-1607` (including CLI side forwarding surface
//! `cli/aimail:356-368`: only exposes `-s/--system-id`, `-H/--home`, `-v/--verbose`;
//! `--json` is an internal switch for check_status, **not on the command surface**).
//!
//! Order is semantics: system anchor → L0 config → L1 gateway → L2 bridge → bridge completeness →
//! Bind file → L2r runtime resources → payload → L3/L4 adapter surface → output (table + conclusion line) → rc.
//!
//! [`engine`] is the **in-process reuse** entry for `repair` (Python side uses "subprocess + output capture"
//!); [`run`] is the command-line wrapper.

use crate::core::check::Check;
use crate::core::checks::adapters::{self, Ctx, DEFAULT_SID_ORDER};
use crate::core::checks::l0::{self, Ctx as L0Ctx};
use crate::core::checks::{l1, l2, l2r};
use crate::core::style::{BOLD, CROSS, GREEN, NC, YELLOW};
use crate::core::{config, home};
use crate::hosts::Host;
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Args {
    pub system_id: String,
    pub home: Option<String>,
    pub verbose: bool,
}

/// Engine output: records + print lines "before the table" (platform deselection / default sid / no sid hint / L3-L4 hints).
/// Extracted to let the caller decide whether to print these lines (repair discards them, corresponding to the Python side's capture).
pub struct EngineOut {
    pub check: Check,
    pub pre_lines: Vec<String>,
}

fn env_nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

/// `_resolve_platform_sid`: explicit argv > platform pointer (root / root_or_profiles) > env `SYSTEM_ID`.
///
/// Details copied verbatim: pointer file exists but parsing fails → falls back to env; parsing succeeds but `system_id` key is missing → returns empty string
/// (**does not** fall back to env).
fn resolve_platform_sid(
    user_home: &Path,
    agent_type: &str,
    argv_sid: &str,
    env_sid: Option<&str>,
) -> String {
    if !argv_sid.is_empty() {
        return argv_sid.to_string();
    }
    if crate::hosts::get(agent_type)
        .map(|h| h.home_dir())
        .is_some()
    {
        let ptr = crate::hosts::pointer::path_for(user_home, agent_type);
        if ptr.is_file() {
            if let Ok(text) = std::fs::read_to_string(&ptr) {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    return v
                        .get("system_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                }
            }
        }
    }
    env_sid.unwrap_or("").to_string()
}

/// `_detect_default_sid`: finds the first platform pointer that is "installed and integrated" according to the declaration order of Python `PLATFORMS`.
fn detect_default_sid(user_home: &Path, argv_sid: &str, env_sid: Option<&str>) -> String {
    for pid in DEFAULT_SID_ORDER {
        let root = crate::hosts::identity::platform_root(user_home, pid);
        if !root.exists() {
            continue;
        }
        let sid = resolve_platform_sid(user_home, pid, argv_sid, env_sid);
        if !sid.is_empty() {
            return sid;
        }
    }
    String::new()
}

/// Reverse lookup of system_home for `_system_agent_path(sid)` (fallback branch for anchor reverse selection).
fn platform_by_system_home(user_home: &Path, sid: &str) -> Option<&'static str> {
    let cfg = config::load_gateway_config(sid)?;
    let sh = cfg.system_home.trim();
    if sh.is_empty() {
        return None;
    }
    let sh = std::fs::canonicalize(sh).unwrap_or_else(|_| PathBuf::from(sh));
    for name in crate::hosts::order() {
        let hd = crate::hosts::get(name).map(|h| h.home_dir()).unwrap_or("");
        if hd.is_empty() {
            continue;
        }
        let want = std::fs::canonicalize(user_home.join(hd)).unwrap_or_else(|_| user_home.join(hd));
        if sh == want {
            return Some(name);
        }
    }
    None
}

/// Runs the full check engine once (`sid` is treated as if argv `--system-id` was provided; `agent_home` = normalized platform root from `-H`).
pub fn engine(sid: &str, agent_home: Option<&Path>, verbose: bool) -> EngineOut {
    let a = Args {
        system_id: sid.to_string(),
        home: agent_home.map(|p| p.to_string_lossy().into_owned()),
        verbose,
    };
    let mut pre_lines: Vec<String> = Vec::new();
    build_out(&a, &home::user_home(), &home::aimail_home(), &mut pre_lines)
}

/// Records the orchestration body (`check_status.main()` at :1489-1592). `pre_lines` collects output "before the table".
fn build_out(
    a: &Args,
    user_home: &Path,
    aimail_home: &Path,
    pre_lines: &mut Vec<String>,
) -> EngineOut {
    // AGENT_HOME: normalized platform root from `--home` > env AGENT_HOME > `~/.hermes` (same as Python)
    let agent_home_explicit = a.home.is_some();
    let agent_home: PathBuf = match &a.home {
        Some(h) => crate::hosts::identity::normalize_platform_home(Path::new(h)),
        None => match env_nonempty("AGENT_HOME") {
            Some(v) => PathBuf::from(v),
            None => user_home.join(".hermes"),
        },
    };

    // Platform identification: explicit `--agent-type` > auto-detection
    let mut agent_type = adapters::detect_agent_type(user_home, None, agent_home_explicit);
    let env_sid = env_nonempty("SYSTEM_ID");

    // Platform deselection by sid ownership (overrides detection result when dual platforms coexist based on sid pointer ownership)
    if !a.system_id.is_empty() {
        let sid_platform = {
            let hits = l0::pointer_platforms_for_sid(
                &L0Ctx {
                    aimail_home: aimail_home.to_path_buf(),
                    user_home: user_home.to_path_buf(),
                },
                &a.system_id,
            );
            if !hits.is_empty() {
                Some(hits[0].clone())
            } else {
                platform_by_system_home(user_home, &a.system_id).map(str::to_string)
            }
        };
        if let Some(sid_platform) = sid_platform {
            if sid_platform != agent_type {
                let head: String = a.system_id.chars().take(8).collect();
                pre_lines.push(format!(
                    "  platform: {agent_type} → {sid_platform} (by system_id {head}…)"
                ));
                agent_type = sid_platform;
            }
        }
    }

    // system_id anchor
    let mut platform_sid =
        resolve_platform_sid(user_home, &agent_type, &a.system_id, env_sid.as_deref());
    if platform_sid.is_empty() {
        platform_sid = detect_default_sid(user_home, &a.system_id, env_sid.as_deref());
        if !platform_sid.is_empty() {
            pre_lines.push(format!(
                "  default system_id: {platform_sid} (from {} pointer)",
                adapters::detect_agent_type(user_home, None, agent_home_explicit)
            ));
        }
    }
    if platform_sid.is_empty() {
        pre_lines.push(format!(
            "{YELLOW}⚠ No system_id resolved — please specify with --system-id (or confirm that an agent platform is installed on this machine and has an aimail pointer){NC}"
        ));
    }

    let mut c = Check::new();
    c.verbose = a.verbose;
    c.add(
        "system",
        "id",
        !platform_sid.is_empty(),
        if platform_sid.is_empty() {
            "none found"
        } else {
            platform_sid.as_str()
        },
        "Run: aimail install (activate the system)",
    );

    let ctx = L0Ctx {
        aimail_home: aimail_home.to_path_buf(),
        user_home: user_home.to_path_buf(),
    };

    // L0 configuration
    l0::l0_configs(&mut c, &platform_sid, &ctx);
    // L0 extension: inbound webhook plan drift (three-state vs judgment function single source of truth, offline)
    l0::webhook_plan_drift(&mut c, &platform_sid, &ctx);
    // L1 gateway
    l1::gateway(&mut c, &platform_sid, &ctx);
    // L2 bridge
    l2::bridge(&mut c, &platform_sid, &ctx);
    // L0 extension: bridge completeness + bind file completeness/consistency
    l0::bridge_completeness(&mut c, &platform_sid, &ctx);
    l0::agentmail_json(&mut c, &platform_sid, &ctx);
    // L2r runtime resources + payload
    l2r::runtime(&mut c, &platform_sid, &ctx);

    // L3/L4 adapter surface
    let resolve_sid =
        adapters::resolve_system_id(&agent_home, Some(&a.system_id), env_sid.as_deref());
    let systems_dir = aimail_home.join("systems");
    let actx = Ctx {
        user_home,
        agent_home: &agent_home,
        systems_dir: &systems_dir,
        sid: &platform_sid,
        resolve_sid: &resolve_sid,
    };
    if let Some(hint) = adapters::run_l3_l4(&mut c, &actx, &agent_type) {
        pre_lines.push(format!("{YELLOW}{hint}{NC}"));
    }

    EngineOut {
        check: c,
        pre_lines: std::mem::take(pre_lines),
    }
}

pub fn run(a: Args) -> i32 {
    let out = engine(&a.system_id, a.home.as_deref().map(Path::new), a.verbose);
    for line in &out.pre_lines {
        println!("{line}");
    }
    let c = out.check;
    c.print_table();
    println!();
    if c.all_pass() {
        println!("  {GREEN}{BOLD}✓ All clear — aimail-gateway → agent-platform ready{NC}");
        0
    } else {
        let fail = c.checks.iter().filter(|r| !r.pass).count();
        println!("  {YELLOW}{BOLD}⚠ {fail}  issue(s) — check items marked  {CROSS} {NC}");
        if !a.verbose {
            println!("    Use --verbose for fix suggestions");
        }
        1
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn platform_sid_prefers_argv_then_pointer_then_env() {
        let tmp = TempDir::new("check-sid");
        let uh = tmp.path();
        assert_eq!(
            resolve_platform_sid(uh, "hermes", "argv", Some("env")),
            "argv"
        );
        assert_eq!(resolve_platform_sid(uh, "hermes", "", Some("env")), "env");
        // Platform pointer (hermes root layout)
        let hermes = uh.join(".hermes");
        std::fs::create_dir_all(&hermes).unwrap();
        std::fs::write(
            hermes.join(crate::core::contract::pointer_file()),
            r#"{"system_id":"ptr"}"#,
        )
        .unwrap();
        assert_eq!(resolve_platform_sid(uh, "hermes", "", Some("env")), "ptr");
    }
}
