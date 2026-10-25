//! repair engine — a re-implementation of `cli/repair.py` (plan v3 S5, landed in slices).
//!
//! This slice (S5a) lands the **framework and verdicts**:
//! - the `REPAIRABILITY` table (check dimension -> repairability class: auto/hint + ladder step no. + needs)
//!   — aligned entry-by-entry with `cli/repair.py:851-897`, with a **table-consistency gate** test that fetches
//!   the ground truth from Python and compares (any table drift goes red);
//! - `classify_residual`: re-check residuals -> (locally fixable defects, hints needing admin/host action);
//! - the ladder (`ladder`) descriptions and order (including the drain step appended by `--deep`);
//! - `run`: the repair orchestration skeleton (print header -> run check (in-process engine reuse, zero logic duplication)
//!   -> list failures -> build the ladder -> dry-run lists the plan only -> otherwise execute step by step -> re-check -> residual classification -> rc).
//!
//! **The ladder's ten write-surface actions land in S5b**: `Step::run` currently always returns
//! `Step::NotPorted(...)`, counted into `ladder_bad` by the orchestration with a not-ported note printed — not a silent pass.
//! As a result the `repair` command surface is **still not wired up** (a repair with incomplete write surfaces would "report fixed but not fix").

use crate::core::check::{Check, Record};
use crate::core::style::{NC, RED, YELLOW};
use crate::core::{config, sdk};
use serde_json::Value;
use std::path::Path;

/// Repairability class (the return shape of `repair.py:903-905`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Repairability {
    /// Fixable automatically on this machine (`kind=auto`); `step` = the ladder step no. (for docs/ordering only).
    Auto { step: u8 },
    /// Hint only: needs host/server-side/admin action.
    Hint { why: Option<&'static str> },
}

impl Repairability {
    pub fn is_auto(&self) -> bool {
        matches!(self, Repairability::Auto { .. })
    }
}

use Repairability::{Auto, Hint};

/// Table-consistency gate: this table must match `cli/repair.py:851-897` entry by entry
/// (`cli/tests/repair_parity.rs` fetches the ground truth from Python and compares).
pub const REPAIRABILITY: &[((&str, &str), Repairability)] = &[
    (("config", "complete"), Auto { step: 4 }),
    (("config", "gateway_json"), Auto { step: 4 }),
    (("config", "system_home"), Auto { step: 4 }),
    (("config", "pointer"), Auto { step: 5 }),
    (
        ("config", "webhook-plan-drift"),
        Hint {
            why: Some(
                "webhook_host drifted from the install plan (it is the plan's output: install writes it, check verifies it, repair does not backfill it) -> re-run the install plan (aimail install), which re-derives the inbound webhook state from the live topology",
            ),
        },
    ),
    (("gateway", "config"), Auto { step: 4 }),
    (("gateway", "smtp_port"), Auto { step: 4 }),
    (
        ("gateway", "health"),
        Hint {
            why: Some("the target gateway is down/unreachable -> start or fix it on the host side"),
        },
    ),
    (
        ("gateway", "api_key"),
        Hint {
            why: Some(
                "admin key missing or invalid -> re-install (aimail install) or have an administrator issue one",
            ),
        },
    ),
    (("bridge", "process"), Auto { step: 1 }),
    (("bridge", "config"), Auto { step: 1 }),
    (("bridge", "config-complete"), Auto { step: 1 }),
    (("bridge", "config_consistency"), Auto { step: 1 }),
    (("bridge", "config-mode"), Auto { step: 1 }),
    (("bridge", "activity"), Auto { step: 1 }),
    (("bridge", "self_health"), Auto { step: 1 }),
    (
        ("bridge", "pull_path"),
        Hint {
            why: Some(
                "bridge cannot poll the gateway pending API -> the gateway must be reachable (host side); a missing config entry is fixed by pull-entry",
            ),
        },
    ),
    (("bridge", "pull-entry"), Auto { step: 9 }),
    (("bridge", "routes-entry"), Auto { step: 8 }),
    (
        ("bridge", "routes-target"),
        Hint {
            why: Some(
                "the target agent endpoint is down / the address is unreachable -> start that agent on the host side",
            ),
        },
    ),
    (("agent", "config"), Auto { step: 7 }),
    (("agent", "config-json"), Auto { step: 7 }),
    (("agent", "config-consistency"), Auto { step: 7 }),
    (("agent", "name_apikey"), Auto { step: 7 }),
    (
        ("agent", "config-complete"),
        Hint {
            why: Some(
                "fields such as system_name are authoritative server-side (no local source) -> re-register with aimail install, or let an administrator handle it",
            ),
        },
    ),
    (("agent", "pointer"), Auto { step: 5 }),
    (
        ("agent", "hook"),
        Auto { step: 6 },
    ),
    (
        ("agent", "skill"),
        Auto { step: 6 },
    ),
    (
        ("agent", "toolset"),
        Auto { step: 6 },
    ),
    (("agent", "webhook"), Auto { step: 3 }),
    (
        ("agent", "discovery"),
        Hint {
            why: Some(
                "the platform has no record of this agent yet -> create/register it with the platform's own command",
            ),
        },
    ),
    (
        ("agent", "register"),
        Hint {
            why: Some(
                "the address is not registered in the cloud -> aimail install / the registration chain, or an administrator",
            ),
        },
    ),
    (
        ("agent", "session"),
        Hint {
            why: Some("the agent session/endpoint is down -> start that agent on the host side"),
        },
    ),
    (("runtime", "board-resources"), Auto { step: 6 }),
    (("runtime", "platform-locatable"), Auto { step: 5 }),
    (("system", "id"), Auto { step: 4 }),
];

/// Fallback for unregistered dimensions (`_UNREGISTERED`): treated as a HINT, and **explicitly** stated as not "covered".
pub const UNREGISTERED: Repairability = Hint {
    why: Some(
        "unregistered check dimension -> treated as needing admin action (please report it to the maintainer)",
    ),
};

/// `repairability(level, name)`.
pub fn repairability(level: &str, name: &str) -> Repairability {
    REPAIRABILITY
        .iter()
        .find(|((l, n), _)| *l == level && *n == name)
        .map(|(_, v)| *v)
        .unwrap_or(UNREGISTERED)
}

/// `classify_residual`: re-check residuals -> (locally fixable defects, hints needing admin/host action).
/// Order-preserving (Python does a single pass; each list keeps its original order).
pub fn classify_residual(residual: &[Record]) -> (Vec<Record>, Vec<Record>) {
    let mut defects = Vec::new();
    let mut hints = Vec::new();
    for c in residual {
        if repairability(&c.level, &c.check).is_auto() {
            defects.push(c.clone());
        } else {
            hints.push(c.clone());
        }
    }
    (defects, hints)
}

/// One ladder step (description + action). The description text is part of the dry-run plan => copied verbatim.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    BridgeAlive,
    RefreshRoutes,
    WebhookPairing,
    GatewayConfig,
    Pointer,
    RuntimeResources,
    AgentmailJson,
    RoutesEntries,
    PullEntryKey,
    DrainStuck,
}

impl Step {
    /// This step's dry-run description (verbatim from `repair.py:980-1006`; contract values are assembled from constants, never copied literals).
    pub fn desc(&self) -> String {
        match self {
            Step::BridgeAlive => {
                "bridge alive (remote gateway only; start it idempotently when dead)".to_string()
            }
            Step::RefreshRoutes => {
                "refresh bridge routes (file + admin API hot reload)".to_string()
            }
            Step::WebhookPairing => {
                "repair the gateway webhook pairing (evidence-driven; --deep rewrites directly)"
                    .to_string()
            }
            Step::GatewayConfig => {
                "backfill the gateway config (system_home: fill gaps only)".to_string()
            }
            Step::Pointer => {
                "recreate the platform pointer (only when the platform root is certain and the pointer is missing)"
                    .to_string()
            }
            Step::RuntimeResources => {
                "redeploy runtime resources (patch marker/resources missing -> idempotent reinstall)"
                    .to_string()
            }
            // the description contains the binding file name — assembled from the **contract constant** (no copied literal)
            Step::AgentmailJson => format!(
                "fill gaps in {} + align webhook_url with the live route",
                crate::core::contract::binding_file()
            ),
            Step::RoutesEntries => {
                "complete the bridge routes entries (missing -> refresh)".to_string()
            }
            Step::PullEntryKey => {
                "align the bridge pull entry admin_key with gateway.json".to_string()
            }
            Step::DrainStuck => "drain stuck pending (--deep)".to_string(),
        }
    }

    /// Execute the step. **Steps that are still not ported always return `NotPorted`** (never a silent success); landed steps use the real implementation.
    pub fn run(&self, sid: &str, home: &str, _deep: bool) -> StepResult {
        match self {
            // step 7: fill binding-file gaps + align webhook_url (written back through the SDK door)
            Step::AgentmailJson => {
                let ah = crate::core::home::aimail_home();
                if agentmail_backfill(sid, &ah) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // step 6: runtime resources missing -> idempotent redeploy through the platform's **self-contained SDK install entry**
            Step::RuntimeResources => {
                if runtime_resources(sid, home, &crate::core::home::program_root()) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // step 11 (--deep): ack every stuck pending (last-resort cleanup)
            Step::DrainStuck => {
                let client = gateway_client(sid);
                if drain_stuck(&client) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // step 1: bridge alive (local gateway = direct mode => no bridge needed; otherwise probe by pid and start idempotently if dead)
            Step::BridgeAlive => {
                if ensure_bridge_running(sid) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // step 2: re-refresh this system's routes = reuse this repo's `bridge --system-id` (same implementation, not re-implemented)
            Step::RefreshRoutes => {
                let rc = crate::cmd::bridge::run(&crate::cmd::bridge::Args {
                    system_id: sid.to_string(),
                    home: String::new(),
                    restart: false,
                    upgrade: false,
                    platform: String::new(),
                });
                if rc == 0 {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            Step::GatewayConfig => {
                // only fill gaps in `system_home`, **never overwrite existing values**.
                // `webhook_host` has converged into the inbound verdict function (single source `webhookplan`): install writes it,
                // check verifies it (`config/webhook-plan-drift`); repair **no longer** probes/backfills it —
                // the old `detect_webhook_host` (bare-host verdict) has been deleted; that branch would treat a legitimate
                // S3 (key absent) as a "gap" and backfill it.
                let gw_path = crate::core::config::gateway_config_path(sid);
                if !gw_path.is_file() {
                    fail(&format!(
                        "gateway config does not exist: {}",
                        gw_path.display()
                    ));
                    StepResult::NothingToDo
                } else {
                    let mut v: serde_json::Value = std::fs::read_to_string(&gw_path)
                        .ok()
                        .and_then(|t| serde_json::from_str(&t).ok())
                        .unwrap_or(serde_json::Value::Null);
                    if v.is_null() {
                        fail(&format!("gateway config unreadable: {}", gw_path.display()));
                        StepResult::NothingToDo
                    } else {
                        let mut changed = false;
                        let root = if !home.is_empty() {
                            home.to_string()
                        } else {
                            auto_platform_home(
                                &crate::core::home::user_home(),
                                v.get("system_home").and_then(|x| x.as_str()),
                            )
                        };
                        let need_home = v
                            .get("system_home")
                            .and_then(|x| x.as_str())
                            .unwrap_or("")
                            .is_empty();
                        if need_home {
                            if !root.is_empty()
                                && crate::hosts::identity::detect_platform_from_home(
                                    std::path::Path::new(&root),
                                ) != "unknown"
                            {
                                v["system_home"] = serde_json::Value::String(root.clone());
                                ok(&format!("system_home backfilled: {root}"));
                                changed = true;
                            } else {
                                warn("system_home missing and the platform root cannot be determined (on a multi-platform machine pass --home)");
                            }
                        }
                        if changed {
                            let txt = serde_json::to_string_pretty(&v).unwrap_or_default();
                            let _ = std::fs::write(&gw_path, txt + "\n");
                            #[cfg(unix)]
                            {
                                let _ = std::fs::set_permissions(
                                    &gw_path,
                                    std::os::unix::fs::PermissionsExt::from_mode(0o600),
                                );
                            }
                            StepResult::Fixed
                        } else {
                            StepResult::NothingToDo
                        }
                    }
                }
            }
            Step::RoutesEntries => {
                // `cli/repair.py:720-757`: missing route entries => hand to this repo's `bridge --system-id` to refresh (generated on the bridge side).
                let sysdir =
                    crate::core::config::system_dir_in(&crate::core::home::aimail_home(), sid);
                let rf = crate::core::bridge_wire::routes_file();
                if !sysdir.is_dir() || !rf.is_file() {
                    return StepResult::NothingToDo;
                }
                let routes = crate::core::bridge_wire::read_routes(&rf);
                let mut subs: Vec<std::path::PathBuf>;
                let mut missing: Vec<String> = Vec::new();
                let mut skipped: Vec<String> = Vec::new();
                subs = match std::fs::read_dir(&sysdir) {
                    Ok(rd) => rd.filter_map(|e| e.ok().map(|x| x.path())).collect(),
                    Err(e) => {
                        warn(&format!(
                            "system directory unreadable ({e}) -> skipping the routes-entry repair"
                        ));
                        return StepResult::NothingToDo;
                    }
                };
                subs.sort();
                for sub in subs {
                    let aj = sub.join(crate::core::contract::binding_file());
                    if !aj.is_file() {
                        continue;
                    }
                    // per-file tolerance: one unreadable/corrupt file => skip only that file, never abort the whole step (measured 2026-09-20)
                    let raw = match std::fs::read_to_string(&aj) {
                        Ok(t) => t,
                        Err(_) => {
                            skipped.push(format!(
                                "{}(OSError)",
                                sub.file_name()
                                    .map(|x| x.to_string_lossy().to_string())
                                    .unwrap_or_default()
                            ));
                            continue;
                        }
                    };
                    let d: serde_json::Value = match serde_json::from_str(&raw) {
                        Ok(d) => d,
                        Err(_) => {
                            skipped.push(format!(
                                "{}(JSONDecodeError)",
                                sub.file_name()
                                    .map(|x| x.to_string_lossy().to_string())
                                    .unwrap_or_default()
                            ));
                            continue;
                        }
                    };
                    let email = d
                        .get("email")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string();
                    if !email.is_empty() && !routes.contains_key(&email) {
                        missing.push(email);
                    }
                }
                if !skipped.is_empty() {
                    warn(&format!(
                        "routes-entry repair: skipped {} unreadable file(s): {}",
                        skipped.len(),
                        skipped.join(", ")
                    ));
                }
                if missing.is_empty() {
                    ok("routes entries ok");
                    return StepResult::NothingToDo;
                }
                warn(&format!(
                    "routes missing {} entr(ies) -> refreshed via bridge",
                    crate::core::pyjson::repr_python(&serde_json::json!(missing))
                ));
                if refresh_routes(sid) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            Step::Pointer => {
                // `cli/repair.py:520-555`: the platform root is certain + this sid has no pointer + the target pointer does not exist => only then write.
                // single implementation = [`repair_pointer`] (includes perms 0600; the inline copy was deleted on 2026-10-18).
                if repair_pointer(
                    &crate::core::home::aimail_home(),
                    &crate::core::home::user_home(),
                    sid,
                    home,
                ) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            Step::PullEntryKey => {
                // `cli/repair.py:775-8xx`: align the bridge `pull.systems[].admin_key` with gateway.json (the sole authority).
                let gw_path = crate::core::config::gateway_config_path(sid);
                let cfg = crate::core::bridge_wire::bridge_cfg_file();
                if !gw_path.is_file() || !cfg.is_file() {
                    return StepResult::NothingToDo;
                }
                let gw: serde_json::Value = match std::fs::read_to_string(&gw_path)
                    .ok()
                    .and_then(|t| serde_json::from_str(&t).ok())
                {
                    Some(v) => v,
                    None => return StepResult::NothingToDo,
                };
                let gk = gw
                    .get("admin_key")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if gk.is_empty() {
                    return StepResult::NothingToDo;
                }
                let raw = std::fs::read_to_string(&cfg).unwrap_or_default();
                let td: toml::Value = match raw.parse() {
                    Ok(v) => v,
                    Err(_) => return StepResult::NothingToDo,
                };
                let systems = td
                    .get("pull")
                    .and_then(|p| p.get("systems"))
                    .and_then(|s| s.as_array())
                    .cloned()
                    .unwrap_or_default();
                let entry = systems
                    .iter()
                    .find(|x| x.get("system_id").and_then(|v| v.as_str()) == Some(sid));
                if entry.is_none() {
                    // entry missing => create it locally (no server side needed), reusing install's writer
                    let url = gw
                        .get("gateway_url")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .or_else(|| {
                            gw.get("aimail_url")
                                .and_then(|v| v.as_str())
                                .filter(|s| !s.is_empty())
                        })
                        .unwrap_or("")
                        .to_string();
                    if url.is_empty() {
                        warn("pull entry missing and the gateway config has no gateway_url -> cannot create it locally (fix the config first)");
                        return StepResult::NothingToDo;
                    }
                    let mode = td
                        .get("mode")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .unwrap_or("pull")
                        .to_string();
                    let addr = td
                        .get("bind")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .unwrap_or("127.0.0.1:38081")
                        .to_string();
                    let spec = crate::core::bridge_deploy::BridgeConfigSpec {
                        path: cfg.clone(),
                        mode,
                        addr,
                        gateway_url: url,
                        admin_key: gk.clone(),
                        system_id: sid.to_string(),
                        api_key: gw
                            .get("api_key")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        webhook_secret: String::new(),
                        hostname: String::new(),
                    };
                    return match crate::core::bridge_deploy::write_bridge_config(&spec) {
                        Ok(_) => {
                            ok("bridge pull entry missing -> created locally (gateway_url/admin_key/system_id from the gateway config)");
                            StepResult::Fixed
                        }
                        Err(e) => {
                            fail(&format!("pull entry creation failed: {e}"));
                            StepResult::NothingToDo
                        }
                    };
                }
                if entry
                    .and_then(|e| e.get("admin_key"))
                    .and_then(|v| v.as_str())
                    == Some(gk.as_str())
                {
                    return StepResult::NothingToDo;
                }
                // targeted replacement of this entry's admin_key (both field orders; equivalent to Python's two regexes)
                match replace_entry_admin_key(&raw, sid, &gk) {
                    Some(new_raw) => {
                        if let Err(e) = std::fs::write(&cfg, &new_raw) {
                            fail(&format!("pull entry admin_key alignment failed: {e}"));
                            return StepResult::NothingToDo;
                        }
                        chmod600(&cfg);
                        ok("bridge pull entry admin_key aligned with the gateway config");
                        StepResult::Fixed
                    }
                    None => {
                        warn("pull entry admin_key alignment failed (no format match) -- check the bridge config by hand");
                        StepResult::NothingToDo
                    }
                }
            }
            Step::WebhookPairing => {
                let env: Vec<(String, String)> = Vec::new();
                if webhook_pairing(sid, _deep, &env) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepResult {
    /// An actual action (or an idempotent confirmation).
    Fixed,
    /// Explicitly not done (not counted as a failure, but must be printed).
    NothingToDo,
    /// Not yet ported in this slice (counted as ladder_bad, the reason is printed).
    NotPorted,
}

/// The ladder (`--deep` appends the drain step).
pub fn ladder(deep: bool) -> Vec<Step> {
    let mut plan = vec![
        Step::BridgeAlive,
        Step::RefreshRoutes,
        Step::WebhookPairing,
        Step::GatewayConfig,
        Step::Pointer,
        Step::RuntimeResources,
        Step::AgentmailJson,
        Step::RoutesEntries,
        Step::PullEntryKey,
    ];
    if deep {
        plan.push(Step::DrainStuck);
    }
    plan
}

fn warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}
fn fail(msg: &str) {
    println!("  {RED}✗{NC} {msg}");
}
fn ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}
use crate::core::style::GREEN;

/// The output tail of a re-check/first check (Python `_check_output_tail`: head 240 / tail 600).
/// In-process calls have no child-process stdout; a record summary stands in here, guaranteeing **evidence to look at** when something is "unreadable".
fn checks_tail(records: &[Record]) -> String {
    if records.is_empty() {
        return "(no check output)".to_string();
    }
    let line = |c: &Record| format!("{}:{}", c.level, c.check);
    let head: Vec<String> = records.iter().take(6).map(line).collect();
    if records.len() <= 12 {
        head.join(", ")
    } else {
        let tail: Vec<String> = records
            .iter()
            .rev()
            .take(6)
            .map(line)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        format!("{}, … , {}", head.join(", "), tail.join(", "))
    }
}

/// repair's table header row (both the dry-run plan and the tests compare against it).
pub fn header_line(sid: &str, deep: bool, dry_run: bool) -> String {
    let mut header = format!("  repair system={sid}");
    if dry_run {
        header.push_str(" [dry-run]");
    }
    if deep {
        header.push_str(" [deep]");
    }
    header
}

/// A dry-run plan line (`    - {desc}`) — a single point guaranteeing that "what is printed" and "what is compared" are the same data.
pub fn dry_run_plan_lines(deep: bool) -> Vec<String> {
    ladder(deep)
        .iter()
        .map(|s| format!("    - {}", s.desc()))
        .collect()
}

/// The orchestration of `repair(sid, deep, dry_run, home)` (check goes through the **in-process** engine, zero logic duplication).
///
/// `check_engine` is passed in by the command layer (cmd::check's orchestration function), so that repair and check use
/// the same engine implementation — the Python side reaches the same goal with "child process + JSON parsing".
/// Re-refreshing this system's routes = reusing this repo's `bridge --system-id` (same implementation, zero re-implementation; Python `repair.py:_refresh_routes`).
fn refresh_routes(sid: &str) -> bool {
    crate::cmd::bridge::run(&crate::cmd::bridge::Args {
        system_id: sid.to_string(),
        home: String::new(),
        restart: false,
        upgrade: false,
        platform: String::new(),
    }) == 0
}

/// Replace the `admin_key = "…"` value inside the `{…}` block owned by `system_id = "<sid>"` in the bridge config text with gk.
/// Equivalent to Python's two targeted regexes at `repair.py:815-824` (both field orders are tried).
fn replace_entry_admin_key(raw: &str, sid: &str, gk: &str) -> Option<String> {
    let sid_tok = format!("system_id = \"{sid}\"");
    let splice = |open: usize, close: usize, block_new: String| -> String {
        let mut out = String::with_capacity(raw.len() + gk.len());
        out.push_str(&raw[..open]);
        out.push_str(&block_new);
        out.push_str(&raw[close + 1..]);
        out
    };
    if let Some(pos) = raw.find(&sid_tok) {
        if let (Some(open), Some(close)) =
            (raw[..pos].rfind('{'), raw[pos..].find('}').map(|c| pos + c))
        {
            if let Some(nb) = replace_first_admin_key(&raw[open..=close], gk) {
                return Some(splice(open, close, nb));
            }
        }
    }
    let mut idx = 0usize;
    while let Some(o) = raw[idx..].find('{') {
        let open = idx + o;
        let close = match raw[open..].find('}') {
            Some(c) => open + c,
            None => break,
        };
        let block = &raw[open..=close];
        if block.contains(&sid_tok) {
            if let Some(nb) = replace_first_admin_key(block, gk) {
                return Some(splice(open, close, nb));
            }
        }
        idx = close + 1;
    }
    None
}

fn replace_first_admin_key(block: &str, gk: &str) -> Option<String> {
    let key = "admin_key = \"";
    let i = block.find(key)? + key.len();
    let j = block[i..].find('"')? + i;
    let mut out = String::with_capacity(block.len() + gk.len());
    out.push_str(&block[..i]);
    out.push_str(gk);
    out.push_str(&block[j..]);
    Some(out)
}

fn chmod600(_p: &std::path::Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(_p, std::fs::Permissions::from_mode(0o600));
    }
}

/// `_repair_binding_webhook_secrets` (`repair.py:183-253`) — local secret reconciliation (**runs first**).
///
/// The local binding is the single source of truth: each binding first takes its local secret (idempotent), then syncs it into the cloud registration copy.
/// Binding reads go through the SDK single entry `iter_bindings` (the same one as install/adapters; none invented separately).
pub fn binding_webhook_secrets(sid: &str, door_env: &[(String, String)]) -> bool {
    let gw: Value = std::fs::read_to_string(crate::core::config::gateway_config_path(sid))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(Value::Null);
    let c = gateway_client(sid);
    // contract v1.0 §4.1(1): a binding is **data-class** => the CLI reads it directly (no more enumeration fetched from the SDK).
    // The path rule is already declared by the contract (config::binding_file_path / contract::binding_file); the Python implementation is not copied.
    let list: Vec<Value> = {
        let root =
            crate::core::config::systems_root_in(&crate::core::home::aimail_home()).join(sid);
        let mut out: Vec<Value> = Vec::new();
        if let Ok(rd) = std::fs::read_dir(&root) {
            for e in rd.flatten() {
                let f = e.path().join(crate::core::contract::binding_file());
                let Ok(txt) = std::fs::read_to_string(&f) else {
                    continue;
                };
                match serde_json::from_str::<Value>(&txt) {
                    Ok(mut v) => {
                        // the SDK door's contract for `binding`: echo it back unchanged + `_config_path` marks the landing spot (reconstructed here from the direct read)
                        if let Some(o) = v.as_object_mut() {
                            o.insert(
                                "_config_path".into(),
                                Value::String(f.to_string_lossy().to_string()),
                            );
                        }
                        out.push(v);
                    }
                    Err(e) => warn(&format!("binding unreadable ({}): {e}", f.display())),
                }
            }
        }
        out
    };
    if list.is_empty() {
        warn(&format!(
            "system {sid} has no readable agent binding -- no webhook pairing to repair; register an agent first (`aimail install` or the host-side register command)"
        ));
        return false;
    }
    let mut fixed = false;
    for d in list {
        let cfg_path = d
            .get("_config_path")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let email = d
            .get("email")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .or_else(|| {
                std::path::Path::new(&cfg_path)
                    .parent()
                    .and_then(|p| p.file_name())
                    .map(|x| x.to_string_lossy().to_string())
            })
            .unwrap_or_default();
        // contract v1.0 §4.1: action-class convergence — ensure_webhook_secret is merged into update(action="webhook-secret")
        let prov = sdk::sdk_ops_call(
            "update",
            &serde_json::json!({"system_id": sid, "action": "webhook-secret", "binding": d}),
            std::time::Duration::from_secs(120),
            door_env,
        )
        .unwrap_or(Value::Null)
        .get("result")
        .cloned()
        .unwrap_or(Value::Null);
        let reason = prov.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        if reason == "provisioned" {
            ok(&format!(
                "{email}: local webhook secret provisioned + written 0600 (the host verifies with it; the cloud copy is synced below)"
            ));
            fixed = true;
        } else if reason == "no-path" || reason == "write-failed" {
            let det = prov
                .get("detail")
                .map(|v| format!(": {v}"))
                .unwrap_or_default();
            fail(&format!(
                "{email}: local webhook secret could not be provisioned ({reason}{det}) -- fix the binding file path/permissions, then re-run 'aimail repair'"
            ));
            continue;
        }
        let secret = prov
            .get("secret")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if secret.is_empty() {
            fail(&format!(
                "{email}: no usable local webhook secret -- reinstall that agent"
            ));
            continue;
        }
        let Some(_c) = c.as_ref() else {
            warn(&format!(
                "{email}: local secret ok, but the gateway client is unavailable (aimail_gateway.json without gateway_url/admin_key) -- start with 'aimail install', then re-run 'aimail repair'"
            ));
            continue;
        };
        let local_url = d
            .get("webhook_url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        // contract v1.0 §4.1: action-class + value-class convergence together — the cloud re-pairing goes through one door assemble
        // (no register_spec passed => the platform registrar does not run; the webhook address is resolved inside the SDK, and the CLI never takes an intermediate value)
        let res = sdk::sdk_ops_call(
            "assemble",
            &serde_json::json!({
                "gw": gw, "system_id": sid, "email": email,
                "agent_id": email,
                "local_webhook_url": local_url,
                "webhook_secret": secret,
                "manager_address": d.get("manager_address").and_then(|v| v.as_str()).unwrap_or(""),
            }),
            std::time::Duration::from_secs(120),
            door_env,
        );
        match res {
            Err(e) => {
                fail(&format!(
                    "{email}: cloud re-pair failed ({}) -- the gateway must be reachable; re-run 'aimail repair' once it is",
                    e.display_like_python()
                ));
                continue;
            }
            Ok(r) => {
                let key_note = if r.get("registered").and_then(|x| x.get("api_key")).is_some() {
                    " (key returned)"
                } else {
                    ""
                };
                ok(&format!(
                    "{email}: cloud registration re-paired (url + secret, idempotent){key_note}"
                ));
            }
        }
    }
    fixed
}

/// Directory key formula (`repair.py:310`: non `[\w.-]` => `_`; dotted address `agent.x@dom` => `agent.x_dom`).
fn binding_dir_key(email: &str) -> String {
    email
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// `_repair_webhook_pairing` (`repair.py:256-341`): evidence-driven re-attach + `--deep` full rewrite + bad-pending ack.
pub fn webhook_pairing(sid: &str, deep: bool, door_env: &[(String, String)]) -> bool {
    let client = gateway_client(sid);
    let mut fixed = binding_webhook_secrets(sid, door_env);
    let Some(c) = client.as_ref() else {
        fail(&format!(
            "gateway config missing, skipping the webhook pairing repair (system {sid})"
        ));
        return fixed;
    };
    let gw: Value = std::fs::read_to_string(crate::core::config::gateway_config_path(sid))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(Value::Null);
    let pend = c.post(
        "/api/v1/admin/pending",
        &serde_json::json!({"filter": [], "emails": []}),
    );
    if let Some(err) = pend.get("error") {
        warn(&format!(
            "pending query failed ({err}) -- cannot read the empty-signature evidence; use --deep to rewrite"
        ));
        return fixed;
    }
    let mut empties = pending_empties(&pend);
    if empties.is_empty() && !deep {
        ok("webhook pairing ok (no evidence of a missing pairing)");
        return fixed;
    }
    if deep && empties.is_empty() {
        let sysdir = crate::core::config::system_dir_in(&crate::core::home::aimail_home(), sid);
        let mut subs: Vec<std::path::PathBuf> = match std::fs::read_dir(&sysdir) {
            Ok(rd) => rd.filter_map(|e| e.ok().map(|x| x.path())).collect(),
            Err(_) => Vec::new(),
        };
        subs.sort();
        let mut targets: Vec<(Option<String>, String)> = Vec::new();
        for sub in subs {
            let aj = sub.join(crate::core::contract::binding_file());
            let d = std::fs::read_to_string(&aj)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok());
            if let Some(e) = d
                .as_ref()
                .and_then(|v| v.get("email"))
                .and_then(|v| v.as_str())
            {
                if !e.is_empty() {
                    targets.push((None, e.to_string()));
                }
            }
        }
        empties = targets;
        warn(&format!(
            "--deep: rewriting the webhook pairing of {} agent(s) from the binding files",
            empties.len()
        ));
    }
    let has_evidence = empties.iter().any(|(i, _)| i.is_some()) || deep;
    warn(&format!(
        "{} pairing target(s) need repair (evidence={has_evidence})",
        empties.len()
    ));
    for (_id, email) in &empties {
        let key = binding_dir_key(email);
        let sysdir = crate::core::config::system_dir_in(&crate::core::home::aimail_home(), sid);
        let direct = sysdir
            .join(&key)
            .join(crate::core::contract::binding_file());
        let aj = if direct.is_file() {
            Some(direct)
        } else {
            // loose fallback: find the copy whose email matches, by directory name
            let mut found = None;
            if let Ok(rd) = std::fs::read_dir(&sysdir) {
                let mut subs: Vec<std::path::PathBuf> =
                    rd.filter_map(|e| e.ok().map(|x| x.path())).collect();
                subs.sort();
                for sub in subs {
                    let cand = sub.join(crate::core::contract::binding_file());
                    let d = std::fs::read_to_string(&cand)
                        .ok()
                        .and_then(|t| serde_json::from_str::<Value>(&t).ok());
                    if d.as_ref()
                        .and_then(|v| v.get("email"))
                        .and_then(|v| v.as_str())
                        == Some(email.as_str())
                    {
                        found = Some(cand);
                        break;
                    }
                }
            }
            found
        };
        let Some(aj) = aj else {
            fail(&format!(
                "{email}: local binding file missing -- the single source of truth is gone; reinstall that agent"
            ));
            continue;
        };
        let local: Value = std::fs::read_to_string(&aj)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or(Value::Null);
        let local_url = local
            .get("webhook_url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        // contract v1.0 §4.1: value-class + action-class convergence together — re-registration goes through one door assemble
        // (no register_spec passed => the platform registrar does not run; the webhook address is resolved inside the SDK ✓)
        let res = sdk::sdk_ops_call(
            "assemble",
            &serde_json::json!({
                "gw": gw, "system_id": sid, "email": email,
                "agent_id": email,
                "local_webhook_url": local_url,
                "webhook_secret": local.get("webhook_secret").and_then(|v| v.as_str()).unwrap_or(""),
                "manager_address": local.get("manager_address").and_then(|v| v.as_str()).unwrap_or(""),
            }),
            std::time::Duration::from_secs(120),
            door_env,
        );
        match res {
            Ok(r) => {
                let key_note = if r.get("api_key").is_some() {
                    " (key returned)"
                } else {
                    ""
                };
                ok(&format!(
                    "{email}: registration chain re-run (url+secret re-paired){key_note}"
                ));
                fixed = true;
            }
            Err(e) => {
                fail(&format!(
                    "{email}: cloud re-registration failed ({}) -- the gateway must be reachable; re-run 'aimail repair' once it is",
                    e.display_like_python()
                ));
            }
        }
    }
    for (pid, _e) in &empties {
        let Some(pid) = pid.as_ref() else { continue };
        let res = c.post(
            "/api/v1/admin/pending/ack",
            &serde_json::json!({"ids": [pid]}),
        );
        match res.get("error") {
            Some(e) => warn(&format!("ack {pid} failed: {e}")),
            None => ok(&format!("acked the broken pending id={pid}")),
        }
    }
    fixed
}
pub fn run(
    sid: &str,
    deep: bool,
    dry_run: bool,
    home: &str,
    check_engine: impl Fn(&str, Option<&Path>) -> Check,
) -> i32 {
    println!("{}", header_line(sid, deep, dry_run));

    let agent_home = if home.is_empty() {
        None
    } else {
        Some(Path::new(home))
    };
    let first = check_engine(sid, agent_home);
    let records: Vec<Record> = first.checks.clone();
    // an empty check = GAP (unreadable), not "pass" (repair plan A 2026-09-29)
    let passed = !records.is_empty() && records.iter().all(|r| r.pass);
    if records.is_empty() {
        warn("check produced no output -- the check step is unjudgeable in this form (NOT a system_id verdict); repair continues with its unconditional ladder");
        warn(&format!(
            "    check subprocess output: {}",
            checks_tail(&records)
        ));
    }
    if passed {
        ok("check is all green, nothing to repair");
        return 0;
    }
    for c in records.iter().filter(|r| !r.pass) {
        let detail: String = c.detail.chars().take(90).collect();
        warn(&format!("check ✗ {}/{}: {}", c.level, c.check, detail));
    }

    let plan = ladder(deep);
    if dry_run {
        println!("  [dry-run] plan:");
        for line in dry_run_plan_lines(deep) {
            println!("{line}");
        }
        return 0;
    }

    let mut ladder_bad = 0;
    for step in &plan {
        println!("\n  ── {} ──", step.desc());
        match step.run(sid, home, deep) {
            StepResult::Fixed | StepResult::NothingToDo => {}
            StepResult::NotPorted => {
                fail(&format!(
                    "repair action not ported yet: {} (plan v3 S5b)",
                    step.desc()
                ));
                ladder_bad += 1;
            }
        }
    }

    println!("\n  -- re-check --");
    let second = check_engine(sid, agent_home);
    let still: Vec<Record> = second.checks.iter().filter(|r| !r.pass).cloned().collect();
    let passed2 = !second.checks.is_empty() && second.checks.iter().all(|r| r.pass);
    if second.checks.is_empty() {
        warn("check produced no output on re-check -- re-check is UNJUDGEABLE (empty output is not all green)");
        warn(&format!(
            "    check subprocess output: {}",
            checks_tail(&second.checks)
        ));
        if ladder_bad > 0 {
            fail(&format!(
                "-> re-check unjudgeable and the ladder reported {ladder_bad} failed step(s): rc=1"
            ));
            return 1;
        }
        warn(
            "-> re-check unjudgeable: rc is the ladder's own result only, NOT an all-green verdict",
        );
        return 0;
    }
    if passed2 {
        ok("re-check is all green");
        return 0;
    }
    let (defects, hints) = classify_residual(&still);
    for c in &defects {
        let detail: String = c.detail.chars().take(90).collect();
        warn(&format!(
            "[D locally fixable, still failing] {}/{}: {}",
            c.level, c.check, detail
        ));
    }
    for c in &hints {
        let detail: String = c.detail.chars().take(80).collect();
        warn(&format!(
            "[H needs admin/host action] {}/{}: {}",
            c.level, c.check, detail
        ));
        if let Repairability::Hint { why: Some(why) } = repairability(&c.level, &c.check) {
            warn(&format!("    → {why}"));
        }
    }
    warn(&format!(
        "re-check not all green: {} locally-fixable defect(s) / {} needing admin action",
        defects.len(),
        hints.len()
    ));
    if !defects.is_empty() {
        warn("-> locally-fixable items are still failing (defects): report them to the maintainer with the log above");
    } else {
        warn("-> the remaining items are not reliably self-fixable (repair only hints; a system administrator acts)");
    }
    1
}

// ════════════════════════════════════════════════════════════════════════════
// S5b-i: steps the CLI is entitled to write itself (system-level files / platform pointers) — ownership iron rule: the CLI never writes binding files
// reference code: `cli/repair.py:415-553` (_auto_platform_home / _sid_has_pointer /
// _repair_pointer). `_repair_gateway_config` has converged: `webhook_host` belongs to the inbound verdict function
// (single source; install writes / check verifies / repair does not write); the gateway config step only fills `system_home`.
// ════════════════════════════════════════════════════════════════════════════

/// `_auto_platform_home`: a single-platform machine auto-determines the platform root; multi-platform falls back to cfg's system_home; otherwise empty
/// (**no guessing**).
pub fn auto_platform_home(user_home: &Path, stored_system_home: Option<&str>) -> String {
    let mut hits: Vec<String> = Vec::new();
    for name in crate::hosts::order() {
        let root = crate::hosts::identity::platform_root(user_home, name);
        if root.exists() && crate::hosts::identity::detect_platform_from_home(&root) == *name {
            hits.push(root.to_string_lossy().into_owned());
        }
    }
    if hits.len() == 1 {
        return hits.remove(0);
    }
    if hits.len() > 1 {
        let sh = stored_system_home.unwrap_or("");
        if !sh.is_empty() && Path::new(sh).is_dir() {
            return sh.to_string();
        }
    }
    String::new()
}

/// `_sid_has_pointer(sid)`: any pointer (root / root_or_profiles) of any platform points at this sid.
pub fn sid_has_pointer(user_home: &Path, sid: &str) -> bool {
    for name in crate::hosts::order() {
        for ptr in crate::hosts::pointer::paths_for(user_home, name) {
            if ptr.is_file() {
                if let Ok(text) = std::fs::read_to_string(&ptr) {
                    if let Ok(v) = serde_json::from_str::<Value>(&text) {
                        if v.get("system_id").and_then(Value::as_str) == Some(sid) {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

/// `judge_deliverable(url)` — whether the cloud can POST to this URL (a criterion **stated plainly**).
///
/// Single source = `core::bridge_deploy::judge_deliverable` (the same implementation, reused by bridge_deploy's
/// registration chain); `repair` itself **never writes** `webhook_host` (the three states belong to `webhookplan`; install
/// writes it, check verifies it). The wording is copied verbatim from `cli/deploy_bridge.py:328-350`.
pub fn judge_deliverable(url: &str) -> (bool, String) {
    let raw = url.trim();
    if raw.is_empty() {
        return (
            false,
            "the URL is empty (pull mode: nothing is registered)".to_string(),
        );
    }
    let bare = || {
        format!(
            "'{raw}' is not an absolute http(s) URL — a bare host or host:port cannot be POSTed to (reqwest: builder error)"
        )
    };
    let Some((scheme, rest)) = raw.split_once("://") else {
        return (false, bare());
    };
    let scheme = scheme.to_lowercase();
    if scheme != "http" && scheme != "https" {
        return (false, bare());
    }
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.contains('@') {
        return (false, format!("'{raw}' carries userinfo"));
    }
    let host = host_of(authority);
    if host.is_empty() {
        return (false, format!("'{raw}' carries no host"));
    }
    if host == "0.0.0.0" || host == "::" {
        return (
            false,
            format!(
                "'{raw}' points at {host} — a bind/unspecified address, not a destination (U2: nothing can deliver there)"
            ),
        );
    }
    if let Some(p) = port_of(authority) {
        if !(1..=65535).contains(&p) {
            return (false, format!("'{raw}' carries an invalid port"));
        }
    }
    (true, String::new())
}

fn host_of(authority: &str) -> String {
    let a = authority.rsplit('@').next().unwrap_or(authority);
    if let Some(rest) = a.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_lowercase();
    }
    a.split(':').next().unwrap_or("").to_lowercase()
}

fn port_of(authority: &str) -> Option<i64> {
    let a = authority.rsplit('@').next().unwrap_or(authority);
    if a.starts_with('[') {
        return a
            .split(']')
            .nth(1)
            .and_then(|r| r.strip_prefix(':'))
            .and_then(|p| p.parse::<i64>().ok());
    }
    a.split(':').nth(1).and_then(|p| p.parse::<i64>().ok())
}

/// Public version: install's "is it a local gateway" verdict and repair share **the same** host resolution (single source).
pub fn url_host_pub(url: &str) -> String {
    url_host(url)
}

fn url_host(url: &str) -> String {
    let after = url.split("://").nth(1).unwrap_or("");
    let authority = after.split(['/', '?', '#']).next().unwrap_or("");
    host_of(authority)
}

fn step_ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}

/// `_repair_pointer`: the platform root is certain + this sid has no pointer + the target pointer file does not exist => create the pointer.
pub fn repair_pointer(
    aimail_home: &Path,
    user_home: &Path,
    sid: &str,
    platform_home: &str,
) -> bool {
    if sid_has_pointer(user_home, sid) {
        return false;
    }
    if platform_home.is_empty() {
        return false;
    }
    let plat = crate::hosts::identity::detect_platform_from_home(Path::new(platform_home));
    if plat == "unknown" {
        return false;
    }
    // binding enumeration (**read-only**): take the first one that has an email
    let sysdir = crate::core::config::system_dir_in(aimail_home, sid);
    let mut email = String::new();
    let mut subs: Vec<std::path::PathBuf> = std::fs::read_dir(&sysdir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    for sub in subs {
        let aj = sub.join(crate::core::contract::binding_file());
        if !aj.is_file() {
            continue;
        }
        if let Ok(text) = std::fs::read_to_string(&aj) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                if let Some(e) = v.get("email").and_then(Value::as_str) {
                    if !e.is_empty() {
                        email = e.to_string();
                        break;
                    }
                }
            }
        }
    }
    if email.is_empty() {
        return false;
    }
    let ptr = crate::hosts::identity::platform_root(user_home, plat)
        .join(crate::core::contract::pointer_file());
    if ptr.exists() {
        return false; // a pointer already exists (possibly for another system) — do not overwrite
    }
    if let Some(parent) = ptr.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let body = serde_json::json!({"system_id": sid, "email": email});
    let Ok(text) = serde_json::to_string_pretty(&body) else {
        return false;
    };
    if std::fs::write(&ptr, text).is_err() {
        return false;
    }
    let _ = crate::core::perms::set_user_only(&ptr);
    step_ok(&format!("pointer created: {} → {sid}", ptr.display()));
    true
}

/// A gateway client synonymous with `aimail_gateway.json` (`_gateway_client` from `repair.py:72-80`).
/// missing `gateway_url`/`admin_key` => None (the caller prints a "client unavailable" hint on this basis, instead of erroring).
pub fn gateway_client(sid: &str) -> Option<crate::core::gateway::GatewayClient> {
    let gw = config::load_gateway_config_in(&crate::core::home::aimail_home(), sid)?;
    let obj = gw.to_json();
    let url = obj.get("gateway_url").and_then(Value::as_str).unwrap_or("");
    let key = obj.get("admin_key").and_then(Value::as_str).unwrap_or("");
    if url.is_empty() || key.is_empty() {
        return None;
    }
    Some(crate::core::gateway::GatewayClient::with_defaults(url, key))
}

/// The pending-query response body -> the `batches` list (`repair.py:281`: `pend["batches"]`, falling back to the top-level array / `data`).
pub fn pending_batches(pend: &Value) -> Vec<Value> {
    if let Some(Value::Array(a)) = pend.get("batches") {
        return a.clone();
    }
    if let Value::Array(a) = pend {
        return a.clone();
    }
    if let Some(Value::Array(a)) = pend.get("data") {
        return a.clone();
    }
    Vec::new()
}

/// Empty-signature evidence: a delivery whose headers carry neither `X-Webhook-Signature` nor `-V2` => `(id, email)`
/// (`repair.py:282-290`; `id` may be None = the synthetic target of --deep).
pub fn pending_empties(pend: &Value) -> Vec<(Option<String>, String)> {
    let mut out = Vec::new();
    for b in pending_batches(pend) {
        let Some(deliveries) = b.get("deliveries").and_then(Value::as_array) else {
            continue;
        };
        for d in deliveries {
            let hs = d
                .get("headers")
                .and_then(Value::as_str)
                .and_then(|t| serde_json::from_str::<Value>(t).ok())
                .unwrap_or_else(|| serde_json::json!({}));
            let has_sig = hs
                .get("X-Webhook-Signature")
                .map(|v| !v.is_null())
                .unwrap_or(false)
                || hs
                    .get("X-Webhook-Signature-V2")
                    .map(|v| !v.is_null())
                    .unwrap_or(false);
            if !has_sig {
                out.push((
                    d.get("id").map(|v| {
                        v.as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| v.to_string())
                    }),
                    d.get("email")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                ));
            }
        }
    }
    out
}

/// Stuck verdict (`repair.py:355-366`): `created_at` age > 600s => collect the id; an unparseable time => skip that entry.
pub fn stuck_pending_ids(pend: &Value, now: i64) -> Vec<String> {
    let mut stuck = Vec::new();
    for b in pending_batches(pend) {
        let Some(deliveries) = b.get("deliveries").and_then(Value::as_array) else {
            continue;
        };
        for d in deliveries {
            let created = d.get("created_at").and_then(Value::as_str).unwrap_or("");
            let Some(ts) = crate::core::time::parse_rfc3339_secs(created) else {
                continue;
            };
            if now - ts > 600 {
                if let Some(id) = d.get("id") {
                    stuck.push(
                        id.as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| id.to_string()),
                    );
                }
            }
        }
    }
    stuck
}

/// `_drain_stuck` (`repair.py:344-373`, step 11 of `--deep`): ack every stuck pending as a last resort.
pub fn drain_stuck(client: &Option<crate::core::gateway::GatewayClient>) -> bool {
    let Some(c) = client else {
        return false;
    };
    let pend = c.post(
        "/api/v1/admin/pending",
        &serde_json::json!({"filter": [], "emails": []}),
    );
    if let Some(err) = pend.get("error") {
        warn(&format!("pending query failed: {err}"));
        return false;
    }
    let stuck = stuck_pending_ids(&pend, crate::core::time::now_secs());
    for id in &stuck {
        let res = c.post(
            "/api/v1/admin/pending/ack",
            &serde_json::json!({"ids": [id]}),
        );
        match res.get("error") {
            Some(e) => warn(&format!("ack {id} failed: {e}")),
            None => ok(&format!("ack stuck pending id={id}")),
        }
    }
    !stuck.is_empty()
}

/// `_detect_platform_from_home` (`repair.py:394-407`): the **hand-written verdict variant on the repair side**.
///
/// **Different** from the other two platform verdicts (each copied separately, not merged): hermes uses an OR over `any marker`; openclaw looks at
/// the `openclaw.json` file; deerflow looks at the `backend/app/gateway` directory; dsh recognizes `profiles` (C-3:
/// storages is a runtime product and does not enter the features). The names carry platform semantics (the registry only describes
/// features and cannot express the three shapes OR/file/directory used here).
fn detect_platform_for_repair(dir: &Path) -> &'static str {
    crate::hosts::identity::detect_for_repair(dir)
}

/// `_failing_file_checks` (`repair.py:570-596`): the **file-class** health_checks in the registry that "did not hit".
///
/// All three locator keys must be honored (`path` / `alt` / `glob` — openclaw's plugin directory check uses glob; failing to honor it would misread
/// "resources in place" as "missing").
fn failing_file_checks(plat: &str, sh: &str, user_home: &Path) -> Vec<Value> {
    const FILE_KINDS: &[&str] = &[
        "file_contains",
        "file_contains_alt",
        "file_exists",
        "file_exists_any",
    ];
    let mut fails = Vec::new();
    for ch in crate::hosts::get(plat)
        .map(|h| crate::hosts::health::checks_json(h))
        .unwrap_or_default()
    {
        let kind = ch.get("kind").and_then(Value::as_str).unwrap_or("");
        if !FILE_KINDS.contains(&kind) {
            continue;
        }
        let pat = ["path", "alt", "glob"]
            .iter()
            .find_map(|k| ch.get(*k).and_then(Value::as_str))
            .unwrap_or("")
            .replace("{home}", sh)
            .replace("{user_home}", &user_home.to_string_lossy());
        let mut ok = false;
        if let Ok(paths) = glob::glob(&pat) {
            for cand in paths.flatten() {
                if kind == "file_exists" || kind == "file_exists_any" {
                    if cand.is_file() {
                        ok = true;
                        break;
                    }
                    continue;
                }
                let marker = ch.get("marker").and_then(Value::as_str).unwrap_or("");
                if let Ok(text) = std::fs::read_to_string(&cand) {
                    if text.contains(marker) {
                        ok = true;
                        break;
                    }
                }
            }
        }
        if !ok {
            fails.push(ch);
        }
    }
    fails
}

/// `_repair_runtime_resources` (`repair.py:612-651`): L2 runtime resources missing -> through that platform's **self-contained SDK install entry**
/// idempotent redeploy (`install.py install --type <tgt> --home <sh> --system-id <sid>`).
///
/// Key verbatim points: ① the platform root cannot be resolved => skip with an explanation (a remote platform like deerflow must run on the host);
/// ② the platform has **no** sdk_install entry => only print the fix hint shipped with the registry, **do not** spawn an install doomed to fail;
/// ③ the install output is forwarded verbatim, tail 600 chars; ④ always **idempotent** (returns false and does nothing when resources are complete).
pub fn runtime_resources(sid: &str, platform_home: &str, prog_root: &Path) -> bool {
    let ah = crate::core::home::aimail_home();
    let gw = config::load_gateway_config_in(&ah, sid)
        .map(|c| c.to_json())
        .unwrap_or_default();
    let sh = if platform_home.is_empty() {
        gw.get("system_home")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        platform_home.to_string()
    };
    if sh.is_empty() || !Path::new(&sh).is_dir() {
        warn("platform root not resolvable, skipping the runtime-resource redeploy (for remote platforms such as deerflow run it on the host)");
        return false;
    }
    let plat = detect_platform_for_repair(Path::new(&sh));
    if plat == "unknown" {
        warn("platform type not recognised (the root is neither a known platform nor a self-built one) -> skipping the runtime-resource redeploy");
        return false;
    }
    let fails = failing_file_checks(plat, &sh, &crate::core::home::user_home());
    if fails.is_empty() {
        ok(&format!("{plat} runtime resources ok"));
        return false;
    }
    let tgt = crate::hosts::get(plat)
        .map(|h| crate::hosts::exec::sdk_install_target(h))
        .unwrap_or_default();
    if tgt.is_empty() {
        warn(&format!(
            "{plat} runtime resources missing; this platform has no SDK install entry (its own installer manages them) -> follow the hint:"
        ));
        for ch in &fails {
            let id = ch.get("id").and_then(Value::as_str).unwrap_or("?");
            let txt = ch
                .get("fix")
                .and_then(Value::as_str)
                .or_else(|| ch.get("fail_text").and_then(Value::as_str))
                .unwrap_or("");
            warn(&format!("  [{id}] {txt}"));
        }
        return false;
    }
    warn(&format!(
        "{plat} runtime resources missing -> idempotent reinstall (install.py install --type {tgt} --home {sh})"
    ));
    // invoked by **file path** (consistent with every step), without relying on `import aimail` in the interpreter; install.py does its own sys.path bootstrap,
    // so both the repo (pysdk/) and pip (site-packages/aimail/) layouts work.
    // invoked by **file path** (consistent with every step), without relying on `import aimail` in the interpreter; install.py does
    // its own sys.path bootstrap, so both the repo (pysdk/) and pip (site-packages/aimail/) layouts work.
    let py = crate::core::sdkroot::python_bin();
    let install_py = prog_root
        .join("aimail-src")
        .join("pysdk")
        .join("install.py");
    let mut args: Vec<String> = Vec::new();
    if install_py.is_file() {
        args.push(install_py.to_string_lossy().to_string());
    } else {
        args.push("-m".to_string());
        args.push("aimail.install".to_string());
    }
    args.extend([
        "install".to_string(),
        "--type".to_string(),
        tgt.clone(),
        "--home".to_string(),
        sh.clone(),
        "--system-id".to_string(),
        sid.to_string(),
    ]);
    match sdk::run_program(
        Path::new(&py),
        &args,
        std::time::Duration::from_secs(300),
        &[],
    ) {
        Ok((out, _err, 0)) => {
            let text = String::from_utf8_lossy(&out);
            let tail: String = text
                .chars()
                .rev()
                .take(600)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            print!("{tail}");
            ok("runtime resources reinstalled");
            true
        }
        Ok((_, err, code)) => {
            let text = String::from_utf8_lossy(&err);
            let tail: String = text
                .chars()
                .rev()
                .take(200)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            fail(&format!("reinstall failed (exit {code}): {tail}"));
            false
        }
        Err(e) => {
            fail(&format!("reinstall failed: {}", e.display_like_python()));
            false
        }
    }
}

/// `_repair_agentmail_json` (`repair.py:655-717`): fill the binding file's rebuildable fields + align with the **live** route
/// `webhook_url`; write back **through the SDK door** `backfill_binding` (per-agent bindings are written only by the SDK — ownership iron rule).
///
/// Verbatim points: ① field filling defers to the gateway config and only fills **empty** ones; ② the route table is parsed line by line (`k = v`, stripping quotes/commas, later writes override earlier ones);
/// ③ alignment condition = the route target is alive + the target is a local address + the declared value differs from the target (a live declared value is left untouched);
/// ④ compare against the original dict to decide whether to write (equal value => no write, idempotent); ⑤ a single file failure only skips that file.
pub fn agentmail_backfill(sid: &str, aimail_home: &Path) -> bool {
    agentmail_backfill_with(sid, aimail_home, &[])
}

/// Bridge route table value -> SDK envelope value: a bare `host:port` (bridge storage design, router.rs:308-314) is restored to the full
/// inbound URL (host 127.0.0.1 = the inbound listening convention, path = the contract single source); a value already carrying a scheme passes through unchanged.
/// The PY SDK expects a full URL — stuffing a bare value would downgrade the binding's `webhook_url` (re-registration => 500, see the G2 root-cause chain).
pub fn bridge_route_to_envelope_value(v: &str) -> String {
    if v.starts_with("http://") || v.starts_with("https://") {
        v.to_string()
    } else {
        format!("http://{v}{}", crate::core::contract::inbound_path())
    }
}

/// Same as above, but the child-process environment is injectable (a fixture AIMAIL_HOME => this process's environment is untouched, parallel-safe).
pub fn agentmail_backfill_with(
    sid: &str,
    aimail_home: &Path,
    door_env: &[(String, String)],
) -> bool {
    // contract v1.0 §4.1 + owner ruling (Jia): repair's **verdict belongs to the SDK** — the CLI only triggers
    // update(action="repair"); the SDK computes the gaps itself (filled[]) and fills manager/webhook.
    let routes: serde_json::Map<String, serde_json::Value> = crate::core::bridge_wire::read_routes(
        &aimail_home.join("bridge").join("aimail_routes.toml"),
    )
    .into_iter()
    .map(|(k, v)| {
        (
            k,
            serde_json::Value::String(bridge_route_to_envelope_value(&v)),
        )
    })
    .collect();
    let args = serde_json::json!({
        "system_id": sid, "action": "repair", "home": aimail_home.to_string_lossy(),
        "routes": routes,
    });
    match crate::core::sdk::sdk_ops_call(
        "update",
        &args,
        std::time::Duration::from_secs(120),
        door_env,
    ) {
        Ok(v) => {
            let res = v.get("result").cloned().unwrap_or(v);
            res.get("filled")
                .and_then(|x| x.as_array())
                .map(|a| a.len())
                .unwrap_or(0)
                > 0
        }
        Err(_) => false,
    }
}

/// `repair.py:_ensure_bridge_running`: **judge the mode first** (local gateway = direct, no bridge needed), then probe the process;
/// start idempotently only when dead (the mode verdict is shared with install via `gateway::is_local_gateway` — owner ruling 2026-09-27).
fn ensure_bridge_running(sid: &str) -> bool {
    let sid = sid.trim();
    let gw_url = if sid.is_empty() {
        String::new()
    } else {
        crate::core::config::load_gateway_config_in(&crate::core::home::aimail_home(), sid)
            .map(|c| c.gateway_url)
            .unwrap_or_default()
    };
    if !gw_url.is_empty() && crate::core::gateway::is_local_gateway(&gw_url) {
        ok("bridge: local gateway (direct mode) -- no bridge needed");
        return true;
    }
    let pids = crate::core::bridge_deploy::bridge_pids_for_probe();
    if !pids.is_empty() {
        ok(&format!("bridge ok (already running pid={})", pids[0]));
        return true;
    }
    warn("bridge not running -> starting it (deploy_bridge.start_bridge)");
    let cfg = crate::core::bridge_wire::bridge_cfg_file();
    // Python: `BRIDGE_BIN = AIMAIL_HOME/bridge/bin/aimail-bridge` (**with the bin/ level**;
    // assembling it directly from cfg.parent() would misjudge it as "not deployed" => a false BridgeAlive failure, caught by an L2 measurement).
    let bin = cfg
        .parent()
        .map(|d| d.join("bin").join("aimail-bridge"))
        .unwrap_or_else(|| std::path::PathBuf::from("aimail-bridge"));
    if !cfg.exists() || !bin.exists() {
        fail("bridge not deployed (config/binary missing) -- run install first");
        return false;
    }
    if crate::core::bridge_deploy::start_bridge(
        &bin.to_string_lossy(),
        &cfg.to_string_lossy(),
        &crate::core::bridge_wire::bridge_pid_file().to_string_lossy(),
    ) {
        let pid = std::fs::read_to_string(crate::core::bridge_wire::bridge_pid_file())
            .unwrap_or_default()
            .trim()
            .to_string();
        ok(&format!(
            "bridge started (pid={})",
            if pid.is_empty() { "?".into() } else { pid }
        ));
        true
    } else {
        fail("bridge failed to start -- check ~/.aimail/bridge/aimail-bridge.log");
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bridge_route_envelope_restores_bare_value_and_passthroughs_full_url() {
        use crate::core::contract;
        // a bare host:port (the bridge storage shape) => restore it to the full inbound URL (the contract single source constructs the expected value; the path is not hardcoded)
        assert_eq!(
            bridge_route_to_envelope_value("127.0.0.1:8644"),
            format!("http://127.0.0.1:8644{}", contract::inbound_path())
        );
        // already carries a scheme => pass through unchanged (if the bridge later stores full URLs, the reader needs no change)
        let full = format!("http://127.0.0.1:8644{}", contract::inbound_path());
        assert_eq!(bridge_route_to_envelope_value(&full), full);
        let fulls = format!("https://gw.example.test{}", contract::inbound_path());
        assert_eq!(bridge_route_to_envelope_value(&fulls), fulls);
    }

    fn rec(level: &str, check: &str, pass: bool) -> Record {
        Record {
            level: level.to_string(),
            check: check.to_string(),
            pass,
            detail: String::new(),
            fix: String::new(),
        }
    }

    #[test]
    fn entry_admin_key_replacement_handles_both_field_orders() {
        // field order ①: system_id first
        let a = r#"[[pull.systems]]
{ system_id = "s1", admin_key = "OLD", aimail_url = "http://x" }"#;
        let ra = replace_entry_admin_key(a, "s1", "NEW").expect("order ① should hit");
        assert!(ra.contains("admin_key = \"NEW\""), "{ra}");
        assert!(
            ra.contains("system_id = \"s1\""),
            "system_id is not touched"
        );
        // field order ②: admin_key first
        let b = r#"[[pull.systems]]
{ admin_key = "OLD", system_id = "s1" }"#;
        let rb = replace_entry_admin_key(b, "s1", "NEW").expect("order ② should hit");
        assert!(rb.contains("admin_key = \"NEW\""), "{rb}");
        // entries with a different sid are untouched
        let c = r#"{ system_id = "other", admin_key = "KEEP" }"#;
        assert!(replace_entry_admin_key(c, "s1", "NEW").is_none());
    }

    #[test]
    fn pointer_never_overwrites_an_existing_pointer_for_this_sid() {
        // cli/repair.py:522: this sid already has a pointer => return directly, never rewrite.
        let _g = crate::core::testutil::ENV_LOCK.lock().unwrap();
        let td = std::env::temp_dir().join(format!("ptr-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        std::fs::create_dir_all(td.join(".pi")).unwrap();
        let ptr = td.join(".pi").join(crate::core::contract::pointer_file());
        let orig = br#"{"system_id": "s1", "email": "keep@x"}"#.to_vec();
        std::fs::write(&ptr, &orig).unwrap();
        std::env::set_var("HOME", td.to_string_lossy().to_string());
        std::env::set_var("AIMAIL_HOME", td.to_string_lossy().to_string());
        let _ = run(
            "s1",
            false,
            true,
            "/nonexistent-platform-home",
            |sid: &str, ah: Option<&std::path::Path>| {
                crate::cmd::check::engine(sid, ah, false).check
            },
        );
        assert_eq!(
            std::fs::read(&ptr).unwrap(),
            orig,
            "an existing pointer is never overwritten"
        );
        std::fs::remove_dir_all(&td).ok();
    }

    #[test]
    fn routes_entries_skips_bad_binding_files_without_aborting() {
        // Python measured 2026-09-20: a single corrupt binding file is only skipped; the whole step is never aborted (repair.py:740).
        let _g = crate::core::testutil::ENV_LOCK.lock().unwrap();
        let td = std::env::temp_dir().join(format!("rt-bad-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        std::fs::create_dir_all(td.join("systems/s1/leaf")).unwrap();
        std::fs::write(
            td.join("systems/s1/leaf")
                .join(crate::core::contract::binding_file()),
            b"{not json",
        )
        .unwrap();
        std::env::set_var("AIMAIL_HOME", td.to_string_lossy().to_string());
        let before = std::fs::read(
            td.join("systems/s1/leaf")
                .join(crate::core::contract::binding_file()),
        )
        .unwrap();
        let _ = run(
            "s1",
            false,
            true,
            "",
            |sid: &str, ah: Option<&std::path::Path>| {
                crate::cmd::check::engine(sid, ah, false).check
            },
        );
        let after = std::fs::read(
            td.join("systems/s1/leaf")
                .join(crate::core::contract::binding_file()),
        )
        .unwrap();
        assert_eq!(
            before, after,
            "a corrupt file must not be rewritten/deleted"
        );
        std::fs::remove_dir_all(&td).ok();
    }

    #[test]
    fn unregistered_dimension_is_hint_with_explicit_note() {
        let r = repairability("nope", "nope");
        assert_eq!(r, UNREGISTERED);
        assert!(!r.is_auto());
    }

    #[test]
    fn residual_splits_auto_from_hint() {
        let residual = vec![
            rec("gateway", "health", false), // hint
            rec("config", "pointer", false), // auto
            rec("agent", "hook", false),     // auto
            rec("agent", "register", false), // hint
        ];
        let (defects, hints) = classify_residual(&residual);
        let names = |v: &Vec<Record>| v.iter().map(|c| c.check.clone()).collect::<Vec<_>>();
        assert_eq!(names(&defects), vec!["pointer", "hook"]);
        assert_eq!(names(&hints), vec!["health", "register"]);
    }

    #[test]
    fn ladder_order_matches_python_and_deep_appends_drain() {
        let base = ladder(false);
        assert_eq!(base.len(), 9);
        assert_eq!(base[0], Step::BridgeAlive);
        assert_eq!(base[8], Step::PullEntryKey);
        let deep = ladder(true);
        assert_eq!(deep.len(), 10);
        assert_eq!(deep[9], Step::DrainStuck);
    }

    #[test]
    fn sdk_install_target_matches_registry() {
        // registry criterion (owner 2026-10-06 Jia / tests/sdk/test_sdk_install_step_contract.py):
        // **python hosts** (hermes / deerflow) carry a self-contained install entry (the sdk_install step);
        // **node hosts** (dsh / pi / openclaw) do **not** — their SDK/extension is installed by the host package manager;
        // registration/controlled change/teardown go straight through the registry's `ops.argv` op entry (contract v1.0 §4.1(2)).
        assert_eq!(
            crate::hosts::get("hermes")
                .map(|h| crate::hosts::exec::sdk_install_target(h))
                .unwrap_or_default(),
            "hermes"
        );
        assert_eq!(
            crate::hosts::get("deerflow")
                .map(|h| crate::hosts::exec::sdk_install_target(h))
                .unwrap_or_default(),
            "deerflow"
        );
        assert_eq!(
            crate::hosts::get("dsh")
                .map(|h| crate::hosts::exec::sdk_install_target(h))
                .unwrap_or_default(),
            ""
        );
        assert_eq!(
            crate::hosts::get("pi")
                .map(|h| crate::hosts::exec::sdk_install_target(h))
                .unwrap_or_default(),
            ""
        );
        assert_eq!(
            crate::hosts::get("openclaw")
                .map(|h| crate::hosts::exec::sdk_install_target(h))
                .unwrap_or_default(),
            ""
        );
        assert_eq!(
            crate::hosts::get("nope")
                .map(|h| crate::hosts::exec::sdk_install_target(h))
                .unwrap_or_default(),
            ""
        );
    }

    #[test]
    fn detect_platform_for_repair_mirrors_python_variant() {
        let d = tempfile::tempdir().unwrap();
        // hermes's OR semantics: as long as profiles/ is present (without hermes-agent) it still counts as hermes — different from the two registry-driven ones
        let h = d.path().join(".hermes");
        std::fs::create_dir_all(h.join("profiles")).unwrap();
        assert_eq!(detect_platform_for_repair(&h), "hermes");
        // pi requires the name .pi + agent/; dsh requires the name .dsh + profiles/ (C-3: storages is a runtime product and does not enter the features)
        let pi = d.path().join(".pi");
        std::fs::create_dir_all(pi.join("agent")).unwrap();
        assert_eq!(detect_platform_for_repair(&pi), "pi");
        let dsh = d.path().join(".dsh");
        std::fs::create_dir_all(dsh.join("profiles")).unwrap();
        assert_eq!(detect_platform_for_repair(&dsh), "dsh");
        // openclaw looks at a file; deerflow looks at a directory
        let oc = d.path().join(".openclaw");
        std::fs::create_dir_all(&oc).unwrap();
        std::fs::write(oc.join("openclaw.json"), "{}").unwrap();
        assert_eq!(detect_platform_for_repair(&oc), "openclaw");
        let df = d.path().join("deer-flow");
        std::fs::create_dir_all(df.join("backend/app/gateway")).unwrap();
        assert_eq!(detect_platform_for_repair(&df), "deerflow");
        assert_eq!(
            detect_platform_for_repair(&d.path().join("nope")),
            "unknown"
        );
    }

    #[test]
    fn failing_file_checks_honours_glob_and_marker() {
        let d = tempfile::tempdir().unwrap();
        let home = d.path().join(".hermes");
        std::fs::create_dir_all(&home).unwrap();
        // all resources missing => hermes's file-class checks should have hit failure items
        let missing = failing_file_checks("hermes", &home.to_string_lossy(), d.path());
        assert!(
            !missing.is_empty(),
            "failure items should be reported when resources are missing"
        );
        // the fix hint shipped with the registry must be readable as-is (for repair to print)
        assert!(missing
            .iter()
            .all(|ch| ch.get("id").is_some() || ch.get("path").is_some()));
    }

    #[test]
    fn runtime_resources_skips_when_root_unresolvable() {
        // the platform root cannot be resolved => skip explicitly (no install spawned, no success reported)
        let _g = crate::core::testutil::ENV_LOCK.lock().unwrap();
        let d = tempfile::tempdir().unwrap();
        let ah = d.path().join("aimail");
        std::fs::create_dir_all(ah.join("systems/s1")).unwrap();
        std::env::set_var("AIMAIL_HOME", &ah);
        let r = runtime_resources("s1", "/nonexistent-platform-root", &ah);
        std::env::remove_var("AIMAIL_HOME");
        assert!(!r);
    }

    #[test]
    fn pending_batches_and_empties_match_python_reads() {
        let pend = serde_json::json!({"batches": [{"deliveries": [
            {"id": "d1", "email": "a@example.test", "headers": "{\"X-Webhook-Signature\": \"sig\"}"},
            {"id": "d2", "email": "b@example.test", "headers": "{}"},
            {"id": "d3", "email": "c@example.test", "headers": "not-json"},
            {"id": "d4", "email": "d@example.test", "headers": "{\"X-Webhook-Signature-V2\": \"v2\"}"}
        ]}]});
        assert_eq!(pending_batches(&pend).len(), 1);
        // empty-signature evidence = d2 (no signature header) + d3 (headers not JSON => treated as {} => also no signature); d1/d4 have signatures
        let empties = pending_empties(&pend);
        let ids: Vec<String> = empties.iter().filter_map(|(i, _)| i.clone()).collect();
        assert_eq!(ids, vec!["d2".to_string(), "d3".to_string()]);
        assert_eq!(empties[0].1, "b@example.test");
        // both response shapes — the top-level array / the data array — must be recognized
        assert_eq!(
            pending_batches(&serde_json::json!([{"deliveries": []}])).len(),
            1
        );
        assert_eq!(
            pending_batches(&serde_json::json!({"data": [{"deliveries": []}]})).len(),
            1
        );
        assert!(pending_batches(&serde_json::json!({"ok": true})).is_empty());
    }

    #[test]
    fn stuck_pending_ids_uses_600s_age_and_skips_unparseable() {
        let now = crate::core::time::parse_rfc3339_secs("2026-10-04T12:00:00Z").unwrap();
        let pend = serde_json::json!({"batches": [{"deliveries": [
            {"id": "old", "created_at": "2026-10-04T11:00:00Z"},   // 3600s ago => stuck
            {"id": "fresh", "created_at": "2026-10-04T11:59:30Z"},  // 30s ago => not stuck
            {"id": "bad", "created_at": "not-a-time"}               // unparseable => skip (same verdict as Python)
        ]}]});
        assert_eq!(stuck_pending_ids(&pend, now), vec!["old".to_string()]);
    }

    #[test]
    fn gateway_config_fills_gaps_only_and_never_overwrites() {
        let _g = crate::core::testutil::ENV_LOCK.lock().unwrap();
        let td = std::env::temp_dir().join(format!("aimail-gwcfg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        let sdir = td.join("systems").join("s1");
        std::fs::create_dir_all(&sdir).unwrap();
        let p = sdir.join("aimail_gateway.json");
        // ① an existing value + only webhook_host missing => the existing value is untouched
        std::fs::write(
            &p,
            r#"{"system_home":"/root/.hermes","system_name":"keepme","gateway_url":""}"#,
        )
        .unwrap();
        std::env::set_var("AIMAIL_HOME", &td);
        let _ = crate::core::repair::run("s1", false, false, "", |sid, home| {
            crate::cmd::check::engine(sid, home, false).check
        });
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(
            v["system_home"], "/root/.hermes",
            "an existing system_home must not be overwritten"
        );
        assert_eq!(
            v["system_name"], "keepme",
            "unrelated fields must be preserved"
        );
        // ② the file does not exist => fail loudly and do not touch disk
        let _ = std::fs::remove_file(&p);
        let rc = crate::core::repair::run("s1", false, false, "", |sid, home| {
            crate::cmd::check::engine(sid, home, false).check
        });
        assert!(
            rc >= 0,
            "a missing cfg should fail loudly rather than panic"
        );
        assert!(!p.exists(), "a missing cfg must not create a new file");
        let _ = std::fs::remove_dir_all(&td);
        std::env::remove_var("AIMAIL_HOME");
    }

    #[test]
    fn drain_stuck_posts_pending_then_acks_only_stuck() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::sync::atomic::{AtomicBool, Ordering};
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        listener.set_nonblocking(true).unwrap();
        let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let seen2 = seen.clone();
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let stop2 = stop.clone();
        let h = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
            // serve until **the client itself ends** (stop): no hard cap of "served N requests",
            // otherwise, when the request count changes, the extra connections would be rejected (Connection reset).
            while !stop2.load(Ordering::Relaxed) && std::time::Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut sock, _)) => {
                        sock.set_read_timeout(Some(std::time::Duration::from_secs(5)))
                            .ok();
                        // a request may arrive in multiple TCP segments (header + body) => read until
                        // the Content-Length-declared body is complete, then reply; reading once would
                        // get an empty body on a single-core/low-core runner (the old way was guaranteed red on 1-4 cores).
                        let mut raw: Vec<u8> = Vec::new();
                        let mut buf = [0u8; 4096];
                        loop {
                            match sock.read(&mut buf) {
                                Ok(0) => break,
                                Ok(n) => raw.extend_from_slice(&buf[..n]),
                                Err(_) => break,
                            }
                            let text = String::from_utf8_lossy(&raw).to_string();
                            let Some(head_end) = text.find("\r\n\r\n") else {
                                continue;
                            };
                            let head = &text[..head_end];
                            let have = text.len() - (head_end + 4);
                            let want = head
                                .lines()
                                .filter_map(|l| l.split_once(':'))
                                .find(|(k, _)| k.eq_ignore_ascii_case("content-length"))
                                .and_then(|(_, v)| v.trim().parse::<usize>().ok())
                                .unwrap_or(0);
                            if have >= want {
                                break;
                            }
                        }
                        let req = String::from_utf8_lossy(&raw).to_string();
                        let first = req.lines().next().unwrap_or("").to_string();
                        let body = req.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
                        seen2.lock().unwrap().push(format!("{first} | {body}"));
                        let payload = if first.contains("/pending/ack") {
                            "{\"ok\":true}"
                        } else {
                            "{\"batches\":[{\"deliveries\":[
                                {\"id\":\"old\",\"email\":\"a@example.test\",\"created_at\":\"2000-01-01T00:00:00Z\"},
                                {\"id\":\"fresh\",\"email\":\"b@example.test\",\"created_at\":\"2999-01-01T00:00:00Z\"}
                            ]}]}"
                        };
                        // `Connection: close` + close right after handling: the client no longer reuses the connection,
                        // the next request must go through a fresh accept (otherwise it would be reset on a half-read connection).
                        let resp = format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
                            payload.len(),
                            payload
                        );
                        let _ = sock.write_all(resp.as_bytes());
                        let _ = sock.flush();
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        let client = crate::core::gateway::GatewayClient::with_defaults(
            &format!("http://127.0.0.1:{port}"),
            "k",
        );
        let changed = drain_stuck(&Some(client));
        stop.store(true, Ordering::Relaxed);
        h.join().ok();
        assert!(
            changed,
            "stuck pendings present => should report them handled"
        );
        let log = seen.lock().unwrap().clone();
        assert!(
            log[0].starts_with("POST /api/v1/admin/pending ") && log[0].contains("\"filter\":[]"),
            "the first request should be a pending query: {log:?}"
        );
        assert!(
            log.iter()
                .any(|l| l.contains("/pending/ack") && l.contains("\"old\"")),
            "only the stuck one should be acked: {log:?}"
        );
        assert!(
            !log.iter().any(|l| l.contains("\"fresh\"")),
            "a fresh delivery should not be acked: {log:?}"
        );
    }
}
