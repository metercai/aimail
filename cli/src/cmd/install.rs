//! `aimail install` —— integration entry (human face + only hidden machine face `--system-only`).
//!
//! · `--system-only` (L1 activation/reuse, single-line JSON ABI; mutually exclusive with wiring parameters);
//! · **Human path**: activate/reuse worker (`core::setup`) → domain preset/create → platform wiring
//!   (`core::steps` table-driven) → container runtime record; bridge-related faces see `bridge` command face;
//! · Pointer ownership hard gate: home already belongs to another live system ⇒ reject (no multi-system sharing one home).
//!
//! Unported list is pinned in `cli/tests/cli_surface.rs::unported_faces_are_honest`.

use crate::core::{config, home, setup, steps};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// All parameters for `install` (human face + hidden machine face; undefined values are empty string/`false`).
#[derive(Default, Clone)]
pub struct Args {
    pub home: String,
    pub system_id: String,
    pub product_code: String,
    pub admin_key: String,
    pub gateway_url: String,
    pub system_name: String,
    pub manager: String,
    pub domain: String,
    pub all_agents: bool,
    /// `-a/--agent` (repeatable): only register these platform agents; ignore those not in host agent list.
    pub agents: Vec<String>,
    pub system_only: bool,
    pub container: String,
    pub container_home: String,
    pub platform: String,
}

pub fn run(a: Args) -> i32 {
    // ── Machine-side dispatch (mutually exclusive; and reject the other side's parameters —— never silently swallow)─────────────────
    if a.all_agents && !a.agents.is_empty() {
        eprintln!("ERROR: --all-agents is mutually exclusive with -a/--agent (all, or specify)");
        return 2;
    }
    if a.system_only {
        // system-only is the L1-only iron rule entry: reject parameters from both wiring/bundling sides
        let rejects: [(&str, bool); 2] =
            [("--all-agents", a.all_agents), ("-a", !a.agents.is_empty())];
        for (flag, present) in rejects {
            if present {
                eprintln!("ERROR: the --system-only channel does not accept {flag}");
                return 2;
            }
        }
        return ensure_system(&a);
    }
    install_human(&a)
}

fn err_json(msg: &str, hint: &str) -> i32 {
    let mut obj = serde_json::Map::new();
    obj.insert("success".into(), serde_json::Value::Bool(false));
    obj.insert("error".into(), serde_json::Value::String(msg.to_string()));
    if !hint.is_empty() {
        obj.insert("hint".into(), serde_json::Value::String(hint.to_string()));
        eprintln!("hint: {hint}");
    }
    println!(
        "{}",
        crate::core::pyjson::dumps_python(&serde_json::Value::Object(obj))
    );
    1
}

/// `_resolve_gateway_url(explicit, sid, default)` → `(url, source)`，source ∈ flag|env|prev|default。
fn resolve_gateway_url(explicit: &str, sid: &str) -> (String, &'static str) {
    if !explicit.is_empty() {
        return (explicit.to_string(), "flag");
    }
    let v = crate::core::config::env_val("AIMAIL_GW_URL", "");
    if !v.is_empty() {
        return (v, "env");
    }
    if !sid.is_empty() {
        if let Some(cfg) = crate::core::config::load_gateway_config(sid) {
            if !cfg.gateway_url.is_empty() {
                return (cfg.gateway_url.clone(), "prev");
            }
        }
    }
    (
        crate::core::config::GATEWAY_URL_DEFAULT.to_string(),
        "default",
    )
}

/// Pointer ownership hard gate (owner 2026-10-18): `{home}/{pointer_file}` already points to **another system still in local
/// configuration** ⇒ refuse to overwrite.
///
/// Why it must be blocked: the registration chain rewrites the pointer to the target sid (SDK `_write_pointer` unconditionally overwrites),
/// while `system_home` is also written to this home (`setup.rs` directly overwrites the parameter value) ⇒ two systems
/// fight for the same home, the original system silently loses the pointer on its own home, and there is no error reported.
///
/// Allow two cases: the pointer points exactly to the target sid; the system pointed to by the pointer no longer exists in local configuration (orphan pointer).
fn pointer_conflict_in(aimail_home: &Path, system_home: &Path, target_sid: &str) -> Option<String> {
    let ptr = system_home.join(crate::core::contract::pointer_file());
    let text = std::fs::read_to_string(&ptr).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let owner = v.get("system_id").and_then(Value::as_str).unwrap_or("");
    if owner.is_empty() || owner == target_sid {
        return None;
    }
    let cfg = crate::core::config::gateway_config_path_in(aimail_home, owner);
    if !cfg.is_file() {
        // Orphan pointer (system has disappeared from this machine) ⇒ allow takeover
        return None;
    }
    Some(format!(
        "{} is already bound to another system {owner} (local config remains) —— refusing to overwrite (multiple systems sharing one home is not allowed).\n  If you really want to rebind: first run `aimail uninstall -s {owner} -H {}`, or manually delete that pointer file, then retry",
        ptr.display(),
        system_home.display()
    ))
}

/// `sid_from_system_home(home)`: platform pointer file (`{home}/<pointer_file>`) takes **priority** (authoritative),
/// then falls back to scanning `systems/*/` for a unique match; zero or multiple ⇒ empty (**no guessing**).
fn sid_from_system_home(system_home: &std::path::Path) -> String {
    // 1) Pointer priority
    let ptr = system_home.join(crate::core::contract::pointer_file());
    if let Ok(text) = std::fs::read_to_string(&ptr) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
            let sid = v
                .get("system_id")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            if !sid.is_empty() && crate::core::config::gateway_config_path(&sid).is_file() {
                return sid;
            }
        }
    }
    // 2) Scan for unique match
    let target = system_home.to_string_lossy().to_string();
    let mut hit = Vec::new();
    if let Ok(rd) = std::fs::read_dir(crate::core::config::systems_root_in(
        &crate::core::home::aimail_home(),
    )) {
        for e in rd.flatten() {
            let sid = e.file_name().to_string_lossy().to_string();
            if let Some(cfg) = crate::core::config::load_gateway_config(&sid) {
                let sh = cfg.system_home.clone();
                if !sh.is_empty()
                    && crate::hosts::identity::normalize_platform_home(std::path::Path::new(&sh))
                        .to_string_lossy()
                        == target
                {
                    hit.push(sid);
                }
            }
        }
    }
    if hit.len() == 1 {
        hit.remove(0)
    } else {
        String::new()
    }
}

/// home source fallback: `AGENT_HOME` env (expand_user), aligned with the identity chain of welcome/ping/check (C-7).
/// flag (`--home`) takes priority over it; it takes priority over `--system-id` reverse lookup.
fn agent_home_env() -> Option<std::path::PathBuf> {
    std::env::var("AGENT_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(|s| home::expand_user(&s))
}

fn ensure_system(a: &Args) -> i32 {
    use crate::core::config;
    let mut sys_name = a.system_name.clone();
    let manager = if !a.manager.is_empty() {
        a.manager.clone()
    } else {
        config::env_val("AIMAIL_MANAGER_ADDRESS", "")
    };
    let mut prod_code = a.product_code.clone();
    let mut adm_key = a.admin_key.clone();
    let want_domain = if !a.domain.is_empty() {
        a.domain.clone()
    } else {
        config::env_val("AIMAIL_DOMAIN", "")
    };
    let mut sid = a.system_id.clone();

    // home resolution: --home flag > AGENT_HOME env (C-7, aligned with welcome) > --system-id reverse lookup
    let system_home: std::path::PathBuf = if !a.home.is_empty() {
        crate::hosts::identity::normalize_platform_home(std::path::Path::new(&a.home))
    } else if let Some(ah) = agent_home_env() {
        ah
    } else if sid.is_empty() {
        return err_json(
            "--home is required (or AGENT_HOME env / --system-id backed by local config)",
            "",
        );
    } else {
        let sh = config::load_gateway_config(&sid)
            .map(|c| c.system_home)
            .unwrap_or_default()
            .trim()
            .to_string();
        if sh.is_empty() {
            return err_json(
                &format!("--system-id {sid} has no local config on this machine"),
                "",
            );
        }
        std::path::PathBuf::from(sh)
    };
    if !system_home.exists() {
        return err_json(
            &format!("home dir not found: {}", system_home.display()),
            "",
        );
    }

    // Reuse priority: home belongs to a unique system → reuse; .env code is only a fallback for true new creation
    if sid.is_empty() && prod_code.is_empty() && adm_key.is_empty() {
        let s2 = sid_from_system_home(&system_home);
        if !s2.is_empty() {
            sid = s2;
            eprintln!("reuse owning system: {sid}");
        }
    }
    if sid.is_empty() && prod_code.is_empty() && adm_key.is_empty() {
        prod_code = config::env_val("AIMAIL_PRODUCT_CODE", "");
        if sys_name.is_empty() {
            sys_name = config::env_val("AIMAIL_SYSTEM_NAME", "");
        }
    }
    // C-1: bare install without -k flag uses AIMAIL_ADMIN_KEY env as the last fallback (local reuse/product code priority;
    // env is weaker than flag —— it might be a leftover from a previous export, so it is placed after the reuse scan)
    if adm_key.is_empty() {
        adm_key = config::env_val("AIMAIL_ADMIN_KEY", "");
    }

    // Credential assembly: reuse falls back to this system's cfg.gateway_url (#15); new creation/product code path falls back to AIMAIL_GW_URL env (C-1);
    // stdout is single-line JSON ⇒ announcements go to stderr
    let (gw_url, gw_src) = resolve_gateway_url(&a.gateway_url, &sid);
    if gw_src == "prev" {
        eprintln!("gateway_url inherited from local config: {gw_url}");
    } else if gw_src == "default" {
        eprintln!("gateway_url: no local value, falling back to default {gw_url}");
    }
    // New system (product code) path: domain belongs to product ⇒ do not forward machine-level .env domain for it (otherwise shared domain product 409)
    let new_system_domain = if !prod_code.is_empty() {
        a.domain.clone()
    } else {
        want_domain.clone()
    };
    // P8(2026-10-17): INTEGRATE_* process environment handshake removed (env Vec was never passed to any child process = dead assembly;
    // worker already uses SetupArgs explicit parameters); credential three-choice validation retained (error message/exit code is machine-side ABI).
    if prod_code.is_empty() && adm_key.is_empty() {
        if !sid.is_empty() {
            let key = config::load_gateway_config(&sid)
                .map(|c| c.admin_key)
                .unwrap_or_default();
            if key.is_empty() {
                return err_json(
                    &format!("system {sid} has no local admin_key"),
                    "pass -k <admin-key> or -c <product-code>",
                );
            }
            eprintln!("reuse admin_key for {sid}");
        } else {
            return err_json(
                "no credential to activate with",
                "export AIMAIL_GW_URL + AIMAIL_PRODUCT_CODE then retry (or run bootstrap), or pass -c <code> / -k <admin-key>",
            );
        }
    }

    // ── Activate/reuse worker (Rust port of `setup_system.py`, executed **in-process**)──────────────
    // Why in-process instead of spawning python: worker is the CLI's own implementation (activation is uniquely implemented in CLI),
    // after rustification it is handled by this binary; SDK side only retains whoami/create_api_key/activate_system type
    // "signature + HTTP endpoint" calls (owner 2026-10-04 ruling: rust native implementation, no new SDK surface).
    let wa = crate::core::setup::SetupArgs {
        gateway_url: gw_url.clone(),
        system_id: sid.clone(),
        admin_key: if prod_code.is_empty() {
            adm_key.clone()
        } else {
            String::new()
        },
        product_code: prod_code.clone(),
        system_name: sys_name.clone(),
        domain: new_system_domain.clone(),
        save_raw_snapshots: matches!(
            config::env_val("AIMAIL_SAVE_SNAPSHOTS", "yes")
                .to_lowercase()
                .as_str(),
            "yes" | "true" | "1"
        ),
        manager_address: manager.clone(),
        system_home: system_home.to_string_lossy().to_string(),
        name_explicit: !a.system_name.is_empty(),
    };
    let data = crate::core::setup::setup(&wa);
    if !data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        // Copy the failure announcement of the child process contract: worker prints `display` (indented JSON) + `__ERROR__:<err>` then exit 1,
        // Python side `check_output` raises error ⇒ `_err("system setup failed", stdout.strip()[-400:])`.
        // In-process execution has no child process, here **replay** the same string to keep ABI verbatim consistent.
        let err = data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error");
        let display = crate::core::setup::display_json(&data);
        let worker_stdout = format!("{display}\n__ERROR__:{err}");
        let tail: String = {
            let t = worker_stdout.trim();
            let chars: Vec<char> = t.chars().collect();
            let start = chars.len().saturating_sub(400);
            chars[start..].iter().collect()
        };
        return err_json("system setup failed", &tail);
    }
    let sid2 = data
        .get("system_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if sid2.is_empty() {
        // Python also claims "just-written systems" by mtime (a fallback for when parsing **subprocess stdout** fails);
        // In in-process execution, system_id must come from the worker itself, so this window logic is unavailable —— report an error directly.
        return err_json("setup finished without a system_id", "");
    }
    let cfg = config::load_gateway_config(&sid2);
    eprintln!("system configured: {sid2}");
    // Domain preset/creation (aligned with human path `install_human`'s `ensure_domain`): admin_key reuse path
    // (Path A) does **not create domain** during activation (Path B product code creates domain during activation), and plugin host bootstrap (`--system-only`
    // callback) goes through Path A —— without this step, the plugin-side default agent registration (`agent@<domain>`) will collide
    // "Bare domain not found" (2026-10-15 L3 dsh-plugin grid empirical). The domain of the product code path belongs to the product,
    // not created here individually (consistent with `ensure_domain` comment scope: only domains from explicit/reuse paths).
    // `adm_key` is the **system-level** key resolved this time (before downgrade); if the reuse path is already a domain-level key, creation will
    // 403 ⇒ `ensure_domain` only warns, does not block (graceful degradation; binding cannot be registered when the domain is missing).
    if prod_code.is_empty() && !want_domain.is_empty() && !adm_key.is_empty() {
        ensure_domain(&gw_url, &adm_key, &sid2, &want_domain, true);
    }
    let gw_out = cfg
        .as_ref()
        .map(|c| c.gateway_url.clone())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| gw_url.clone());
    let domain_out = cfg.as_ref().map(|c| c.domain.clone()).unwrap_or_default();
    let name_out = cfg
        .as_ref()
        .map(|c| c.system_name.clone())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| sys_name.clone());
    let path_out = if prod_code.is_empty() {
        "admin_key"
    } else {
        "activation"
    };
    let mut obj = serde_json::Map::new();
    obj.insert("success".into(), serde_json::Value::Bool(true));
    obj.insert("system_id".into(), serde_json::Value::String(sid2));
    obj.insert("gateway_url".into(), serde_json::Value::String(gw_out));
    obj.insert("domain".into(), serde_json::Value::String(domain_out));
    obj.insert("system_name".into(), serde_json::Value::String(name_out));
    obj.insert("path".into(), serde_json::Value::String(path_out.into()));
    println!(
        "{}",
        crate::core::pyjson::dumps_python(&serde_json::Value::Object(obj))
    );
    0
}

// ════════════════════════════════════════════════════════════════════════════
// Human path (`cli/aimail:836-1072`): activate/reuse → domain preset → bridge (not ported, P2) → platform wiring → routing (not ported)
// ════════════════════════════════════════════════════════════════════════════

fn ok(msg: &str) {
    println!(
        "  {}{}{} {}",
        crate::core::style::GREEN,
        crate::core::style::CHECK,
        crate::core::style::NC,
        msg
    );
}

fn fail(msg: &str) -> i32 {
    println!(
        "  {}{}{} {}",
        crate::core::style::RED,
        crate::core::style::CROSS,
        crate::core::style::NC,
        msg
    );
    1
}

fn warn(msg: &str) {
    println!(
        "  {}{}{} {}",
        crate::core::style::YELLOW,
        crate::core::style::CROSS,
        crate::core::style::NC,
        msg
    );
}

/// domain preset/creation (`cli/aimail:983-1004`): only domains on the explicit/reuse path; the product code new-creation path is not included here.
///
/// `to_stderr`: `--system-only` (L1 ABI, stdout is the carrier of the "exactly one line of JSON" contract) must go to stderr
/// for announcements; the human path (`install_human`) keeps stdout human-readable announcements (`ok`/`warn` via println!).
fn ensure_domain(gw_url: &str, admin_key: &str, sid: &str, want_domain: &str, to_stderr: bool) {
    let say = |msg: &str, is_ok: bool| {
        if to_stderr {
            eprintln!("{msg}");
        } else if is_ok {
            ok(msg);
        } else {
            warn(msg);
        }
    };
    let path = format!("/api/v1/admin/systems/{sid}/domains");
    let headers = crate::core::sig::signed_headers(admin_key, "GET", &path, None, "");
    let url = format!("{}{}", gw_url.trim_end_matches('/'), path);
    let (status, body) = crate::core::http::json_req(&url, &headers, None, Some("GET"), 15);
    if !(200..300).contains(&status) {
        say(&format!("domain check failed: {body}"), false);
        return;
    }
    // Python side `list_system_domains`: get `data` from dict, otherwise original value; non-list ⇒ []
    let list = match &body {
        serde_json::Value::Array(_) => Some(body.clone()),
        serde_json::Value::Object(o) => o.get("data").cloned().filter(|v| v.is_array()),
        _ => None,
    };
    let Some(list) = list else {
        say(&format!("domain check failed: {body}"), false);
        return;
    };
    let want_lc = want_domain.to_lowercase();
    let present = list
        .as_array()
        .map(|a| {
            a.iter().any(|d| {
                d.get("domain")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_lowercase() == want_lc)
                    .unwrap_or(false)
            })
        })
        .unwrap_or(false);
    if present {
        say(&format!("domain present: {want_domain}"), true);
        return;
    }
    let body_req = serde_json::json!({
        "id": format!("d{}", crate::core::time::now_secs()),
        "domain": want_domain.trim().to_lowercase(),
    });
    let headers2 = crate::core::sig::signed_headers(
        admin_key,
        "POST",
        &path,
        Some(body_req.to_string().as_bytes()),
        "",
    );
    let (st2, body2) = crate::core::http::json_req(
        &url,
        &headers2,
        Some(body_req.to_string().as_bytes()),
        Some("POST"),
        15,
    );
    if !(200..300).contains(&st2) {
        let err = body2.get("error").and_then(|v| v.as_str()).unwrap_or("");
        let detail = body2.get("detail").and_then(|v| v.as_str()).unwrap_or("");
        say(
            format!("domain create failed: {err} {detail}").trim_end(),
            false,
        );
    } else {
        say(&format!("domain created: {want_domain}"), true);
    }
}

fn install_human(a: &Args) -> i32 {
    let mut sys_name = if a.system_name.is_empty() {
        String::new()
    } else {
        a.system_name.clone()
    };
    let manager = if a.manager.is_empty() {
        config::env_val("AIMAIL_MANAGER_ADDRESS", "")
    } else {
        a.manager.clone()
    };
    let mut prod_code = a.product_code.clone();
    let mut adm_key = a.admin_key.clone();
    let want_domain = if a.domain.is_empty() {
        config::env_val("AIMAIL_DOMAIN", "")
    } else {
        a.domain.clone()
    };
    let mut sid = a.system_id.clone();

    // Target resolution: --home flag > AGENT_HOME env (C-7, aligned with welcome) > --system-id reverse lookup
    let system_home = if !a.home.is_empty() {
        crate::hosts::identity::normalize_platform_home(&home::expand_user(&a.home))
    } else if let Some(ah) = agent_home_env() {
        println!("  --home resolved from the AGENT_HOME env var: {}", ah.to_string_lossy());
        ah
    } else if sid.is_empty() {
        return fail("install requires --home, the AGENT_HOME env var, or --system-id to look up --home from the local config");
    } else {
        let sh = home::system_home_from_sid(&sid);
        if sh.is_empty() {
            return fail(&format!(
                "--system-id {sid} has no local config to look up --home from (is this system not on this machine?)"
            ));
        }
        let p = PathBuf::from(&sh);
        println!("  --home looked up from --system-id: {}", p.to_string_lossy());
        p
    };
    if sid.is_empty() && prod_code.is_empty() && adm_key.is_empty() {
        // Duplicate install: home belongs to a unique system ⇒ auto-reuse (the code in .env may have already been consumed)
        let sid2 = sid_from_system_home(system_home.as_path());
        if !sid2.is_empty() {
            sid = sid2;
            println!("  Reusing owning system: {sid} (reverse lookup via --home)");
        }
    }
    if sid.is_empty() && prod_code.is_empty() && adm_key.is_empty() {
        prod_code = config::env_val("AIMAIL_PRODUCT_CODE", "");
        if sys_name.is_empty() {
            sys_name = config::env_val("AIMAIL_SYSTEM_NAME", "");
        }
    }
    // C-1: For bare install without -k flag, AIMAIL_ADMIN_KEY env is the last fallback (local reuse/product code takes priority;
    // env is weaker than flag —— possibly a leftover from a previous export, so placed after the reuse scan)
    if adm_key.is_empty() {
        adm_key = config::env_val("AIMAIL_ADMIN_KEY", "");
    }
    if !system_home.exists() {
        return fail(&format!(
            "home directory does not exist: {}",
            system_home.to_string_lossy()
        ));
    }
    if !a.container_home.is_empty() && a.container.is_empty() {
        return fail("--container-home requires --container <name>");
    }
    let platform = match crate::hosts::identity::platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => {
            let p = crate::hosts::identity::resolve_platform(&system_home);
            if p.is_empty() {
                return fail(&format!(
                    "cannot determine platform: {} directory has no markers and no aimail pointer (specify --platform explicitly)",
                    system_home.to_string_lossy()
                ));
            }
            p
        }
        Err(msg) => return fail(&msg),
    };
    // Hard gate for pointer ownership: home already belongs to another live system ⇒ early exit (before any writes)
    if let Some(msg) = pointer_conflict_in(&crate::core::home::aimail_home(), &system_home, &sid) {
        return fail(&msg);
    }
    let mut line = format!(
        "  install platform={platform} system_home={} system_id={}",
        system_home.to_string_lossy(),
        if sid.is_empty() {
            "(new)".to_string()
        } else {
            sid.clone()
        }
    );
    if !a.container.is_empty() {
        line.push_str(&format!(" container={}", a.container));
    }
    println!("{line}");

    // Activate/reuse credential assembly (env contract): reuse falls back to this system's cfg.gateway_url (#15);
    // new/product code path falls back to AIMAIL_GW_URL env (C-1)
    let (gw_url, gw_src) = resolve_gateway_url(&a.gateway_url, &sid);
    if gw_src == "prev" {
        ok(&format!(
            "gateway_url inherited from local config: {gw_url} (reusing {sid}; -g / AIMAIL_GW_URL takes priority)"
        ));
    } else if gw_src == "default" {
        warn(&format!(
            "gateway_url has no local value, falling back to default {gw_url} (-g / AIMAIL_GW_URL not provided)"
        ));
    }
    let new_system_domain = if !prod_code.is_empty() {
        a.domain.clone()
    } else {
        want_domain.clone()
    };

    // Credential three-way choice: product code / explicit admin-key / reuse from existing config
    // P8(2026-10-17): Removed INTEGRATE_* process environment handshake —— values enter worker via explicit SetupArgs parameters;
    // only manager → AIMAIL_MANAGER_ADDRESS write-back is kept (SDK child process inherits process environment and reads directly, contract name, Q1 ruling).
    let mut env_admin_key = String::new();
    if !prod_code.is_empty() {
        // Product code path: no local admin_key (backfilled from activation response, see below)
    } else if !adm_key.is_empty() {
        env_admin_key = adm_key.clone();
    } else if !sid.is_empty() {
        if let Some(c) = config::load_gateway_config(&sid) {
            if !c.admin_key.is_empty() {
                env_admin_key = c.admin_key.clone();
            }
        }
    }
    if !manager.is_empty() {
        std::env::set_var("AIMAIL_MANAGER_ADDRESS", &manager);
    }

    // Activate/reuse worker (in-process; same as `--system-only`)
    let wa = setup::SetupArgs {
        gateway_url: gw_url.clone(),
        system_id: sid.clone(),
        admin_key: env_admin_key.clone(),
        product_code: prod_code.clone(),
        system_name: sys_name.clone(),
        domain: new_system_domain.clone(),
        save_raw_snapshots: matches!(
            config::env_val("AIMAIL_SAVE_SNAPSHOTS", "yes")
                .to_lowercase()
                .as_str(),
            "yes" | "true" | "1"
        ),
        manager_address: manager.clone(),
        system_home: system_home.to_string_lossy().to_string(),
        name_explicit: !a.system_name.is_empty(),
    };
    let data = setup::setup(&wa);
    if !data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        // Isomorphic with subprocess contract: the worker's `__ERROR__:<text>` is the failure reason itself
        let err = data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("setup_system failed");
        return fail(err);
    }
    ok("system configured");
    let mut sid2 = sid.clone();
    if let Some(s) = data.get("system_id").and_then(|v| v.as_str()) {
        if !s.is_empty() {
            sid2 = s.to_string();
        }
    }
    if !sid2.is_empty() && sid2 != a.system_id {
        ok(&format!("system_id: {sid2}"));
    }
    // Backfill activation artifacts (new system activation path has no explicit -k);
    // admin_key/domain backfilled into local variables (P8: no longer writing to process env, consumers below read directly)
    let mut system_domain = new_system_domain.clone();
    let mut cfg2 = config::load_gateway_config(&sid2);
    if let Some(c) = cfg2.take() {
        if !c.admin_key.is_empty() {
            env_admin_key = c.admin_key.clone();
        }
        if !c.domain.is_empty() {
            system_domain = c.domain.clone();
        }
        cfg2 = Some(c);
    }

    // 1b) domain preset/creation (explicit/reuse path only)
    if !new_system_domain.is_empty() && !sid2.is_empty() {
        ensure_domain(&gw_url, &env_admin_key, &sid2, &want_domain, false);
    }

    // Inbound webhook determination (owner finalized 2026-10-13, single determination function): at this moment credentials and config are both
    // in place (product code path activated, admin_key backfilled), full probe possible ⇒ provide "best known" tri-state.
    // Fail ⇒ loud abort (bridge required but not deployable / GW unreachable), never silent degradation.
    // Probe uses **system-level raw key** (`probe-webhook` requires system scope; domain-level agent key may not have it).
    let probe_key = {
        let raw = crate::core::config::read_system_raw_key(&sid2);
        if raw.is_empty() {
            env_admin_key.clone()
        } else {
            raw
        }
    };
    let plan = crate::core::webhookplan::plan_inbound(&gw_url, &probe_key);
    match &plan {
        crate::core::webhookplan::WebhookPlan::Fail { reason } => {
            return fail(&format!("inbound webhook plan failed: {reason}"));
        }
        crate::core::webhookplan::WebhookPlan::BridgePush { bridge_host } => {
            ok(&format!(
                "inbound plan: S1 bridge push -> {bridge_host} (external bridge)"
            ));
        }
        crate::core::webhookplan::WebhookPlan::BridgePull => {
            ok("inbound plan: S2 bridge pull (local bridge)");
        }
        crate::core::webhookplan::WebhookPlan::Direct => {
            ok("inbound plan: S3 direct push (local gateway / agent reachable)");
        }
    }
    // Write tri-state back to config (S1 Some(url) / S2 Some("") / S3 None key absent).
    // install provides "best known", inbound up callback (`address --inbound-live --inbound-url`) is the confirmed value.
    if !sid2.is_empty() {
        let wh = plan.webhook_host();
        if let Err(e) = config::update_gateway_config_in(&home::aimail_home(), &sid2, |c| {
            c.webhook_host = wh.clone();
        }) {
            warn(&format!("write inbound webhook_host failed: {e}"));
        }
    }

    // 2) Bridge: dispatch by determination state (S1 external bridge not deployed / S2 local deployment and start bridge / S3 no bridge)
    match &plan {
        crate::core::webhookplan::WebhookPlan::BridgePush { bridge_host } => {
            // S1: push via external bridge —— do not deploy local bridge, webhook_host already points to BRIDGE_HOST.
            // Route pre-registration (POST /routes to external bridge admin) is best-effort, see ensure_inbound_routes.
            ok(&format!(
                "bridge: external (S1) {bridge_host} — no local bridge deployed"
            ));
        }
        crate::core::webhookplan::WebhookPlan::BridgePull => {
            // S2: Deploy local bridge (pull) —— binary in place → config merge → start bridge
            let cfg_wh_host = config::load_gateway_config(&sid2)
                .map(|c| c.webhook_host.unwrap_or_default())
                .unwrap_or_default();
            let rc = crate::core::bridge_deploy::deploy(&crate::core::bridge_deploy::DeploySpec {
                gateway_url: gw_url.clone(),
                admin_key: env_admin_key.clone(),
                system_id: sid2.clone(),
                domain: system_domain.clone(),
                webhook_mode_bridge: true, // S2 = pull mode
                announce_arg: String::new(),
                cfg_webhook_host: cfg_wh_host,
            });
            if rc == 0 {
                ok("bridge deployed");
            } else {
                warn(&format!(
                    "bridge deploy failed (exit {}) -- see the deploy output",
                    rc
                ));
            }
        }
        crate::core::webhookplan::WebhookPlan::Direct => {
            ok("bridge: S3 direct — no bridge needed");
        }
        crate::core::webhookplan::WebhookPlan::Fail { .. } => unreachable!(),
    }

    // 3) Platform adaptation (registry install_steps action table driven)
    let mut ctx: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
    let cfg_for_steps = cfg2
        .as_ref()
        .map(|c| serde_json::Value::Object(c.to_json()))
        .unwrap_or_else(|| {
            serde_json::json!({
                "system_id": sid2.clone(),
                "gateway_url": gw_url,
                "admin_key": env_admin_key,
                "domain": "",
                "system_name": sys_name,
                "manager_address": manager,
                "system_home": system_home.to_string_lossy().to_string(),
            })
        });
    ctx.insert(
        "home".into(),
        serde_json::json!(system_home.to_string_lossy().to_string()),
    );
    ctx.insert("sid".into(), serde_json::json!(sid2));
    // Single source of truth binding (contract §4.1(2)): domain discovery interpreter stored in process local state (P8: process environment writes removed),
    // python gate/registry reads via python_bin(), step subprocesses explicitly inject via bound_python_env,
    // same interpreter as step 0's {python} (C-4);
    // container guard (docker exec returns container-internal paths) is unified within bind_domain.
    let _py = crate::hosts::get(&platform)
        .map(|h| crate::hosts::runtime::python(h, &system_home, &a.container))
        .unwrap_or_default();
    let core_dir = crate::hosts::runtime::sdk_root(&_py).unwrap_or_default();
    ctx.insert("python".into(), serde_json::json!(_py));
    ctx.insert("manager".into(), serde_json::json!(manager));
    // `{sdk}` / `{scripts}` **not resolved here** (owner 2026-10-12): SDK is not installed yet at this point
    // (only present after sdk_install); resolving it earlier is guaranteed empty ⇒ previously it emitted misleading
    // "source not found" for **all** hosts (including node), and deerflow's `{sdk}/…/*.sh` would expand to an empty root (observed exit 127).
    // Changed to **lazy resolution per step** inside `core::steps::run_steps`: only platforms that actually reference `{sdk}` (or need to run the registration step) trigger it.
    ctx.insert("scripts".into(), serde_json::json!(""));
    ctx.insert("sdk".into(), serde_json::json!(""));
    ctx.insert("cfg".into(), cfg_for_steps);
    ctx.insert("all_agents".into(), serde_json::json!(a.all_agents));
    ctx.insert("agents".into(), serde_json::json!(a.agents));
    let rcfg = cfg2.as_ref().map(|c| c.to_json()).unwrap_or_default();
    let g = |k: &str| -> String {
        rcfg.get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    let _c = if a.container.is_empty() {
        g("container")
    } else {
        a.container.clone()
    };
    ctx.insert(
        "runtime".into(),
        serde_json::json!(if _c.is_empty() {
            g("runtime")
        } else {
            "docker".to_string()
        }),
    );
    ctx.insert("container".into(), serde_json::json!(_c));
    ctx.insert(
        "container_home".into(),
        serde_json::json!(if g("container_home").is_empty() {
            system_home.to_string_lossy().to_string()
        } else {
            g("container_home")
        }),
    );

    // System-level cfg record: `platform` (true source for transport dispatch —— node/python selection per contract §4.1(2);
    // this file is written only by CLI, see contract §2) + runtime record when explicit `--container` is used (idempotent overwrite of same value).
    if !sid2.is_empty() {
        let p = config::systems_root_in(&home::aimail_home())
            .join(&sid2)
            .join("aimail_gateway.json");
        match std::fs::read_to_string(&p)
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        {
            Some(serde_json::Value::Object(mut o)) => {
                o.insert("platform".into(), serde_json::json!(platform));
                let record_container = !a.container.is_empty();
                if record_container {
                    o.insert("runtime".into(), serde_json::json!("docker"));
                    o.insert("container".into(), serde_json::json!(a.container));
                    o.insert(
                        "container_home".into(),
                        serde_json::json!(if a.container_home.is_empty() {
                            system_home.to_string_lossy().to_string()
                        } else {
                            a.container_home.clone()
                        }),
                    );
                }
                if let Err(e) = config::write_private_json(&p, &serde_json::Value::Object(o)) {
                    warn(&format!("system cfg record failed: {e}"));
                } else if record_container {
                    ok(&format!(
                        "container runtime recorded: docker/{}",
                        a.container
                    ));
                }
            }
            _ => warn("system cfg record skipped: config unreadable"),
        }
    }

    if let Err(e) = steps::run_install_steps(&platform, &mut ctx, &core_dir) {
        return fail(&e);
    }

    // Routing side: bridge routes for each address are ensured after platform steps (bridge not ported ⇒ explicit warning)
    // Route reconciliation (`_ensure_inbound_routes`): best-effort, never changes rc
    crate::core::bridge_wire::ensure_inbound_routes(&sid2);

    let tail = if a.system_id.is_empty() {
        String::new()
    } else {
        format!("--system-id {}", a.system_id)
    };
    println!(
        "{}",
        format!("  install done. Suggestion: aimail check {tail}").trim_end()
    );
    0
}

#[cfg(test)]
mod gate_tests {
    use super::*;

    fn tmp() -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!(
            "aimail-gate-{}-{}",
            std::process::id(),
            rand_suffix()
        ));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }
    fn rand_suffix() -> u128 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    }
    fn write(path: &Path, body: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, body).unwrap();
    }

    #[test]
    fn pointer_owned_by_another_live_system_is_rejected() {
        let ah = tmp(); // fake AIMAIL_HOME
        let home = tmp(); // fake platform root
        write(
            &home.join(crate::core::contract::pointer_file()),
            r#"{"system_id":"s1","email":"a@d.test"}"#,
        );
        // s1's local system config exists ⇒ alive
        write(
            &crate::core::config::gateway_config_path_in(&ah, "s1"),
            r#"{"system_id":"s1","system_home":"/x"}"#,
        );
        let msg = pointer_conflict_in(&ah, &home, "s2").expect("must reject");
        assert!(msg.contains("already bound to another system s1"), "{msg}");
        assert!(
            msg.contains("aimail uninstall -s s1"),
            "provide executable exit: {msg}"
        );
        // target is s1 itself ⇒ allow (duplicate install)
        assert!(pointer_conflict_in(&ah, &home, "s1").is_none());
    }

    #[test]
    fn orphan_pointer_and_empty_target_are_allowed() {
        let ah = tmp();
        let home = tmp();
        write(
            &home.join(crate::core::contract::pointer_file()),
            r#"{"system_id":"gone","email":"a@d.test"}"#,
        );
        // no local config with s='gone' ⇒ orphan ⇒ allow takeover
        assert!(pointer_conflict_in(&ah, &home, "s2").is_none());
        // new system (target empty) lands in someone else's alive home ⇒ reject
        write(
            &crate::core::config::gateway_config_path_in(&ah, "gone"),
            r#"{"system_id":"gone"}"#,
        );
        assert!(pointer_conflict_in(&ah, &home, "").is_some());
    }
}
