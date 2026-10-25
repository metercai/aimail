//! `aimail reset` — the Rust re-implementation of `cli/aimail:1241-1348`.
//!
//! Semantics: **admin-key path only**, resets the current actual local configuration (no re-activation, does not touch cloud key).
//! Order: home (reverse lookup possible) → platform detection → sid (pointer reverse lookup possible) → read admin-key (read-only) → read prev to get defaults
//! → **run activation worker in-process** (reuses `core::setup`, same path as `--system-only`) → idempotent re-run of registration chain
//! (best-effort semantics: failure only warns, does not interrupt) → route reconciliation (**bridge not ported ⇒ explicit warning**) → suggestion line.

use serde_json::{json, Value};

use crate::core::{config, register, setup, style};
use crate::hosts::Host;

pub struct Args {
    pub home: String,
    pub system_id: String,
    pub platform: String,
    pub gateway_url: String,
    pub system_name: String,
    pub manager: String,
    pub all_agents: bool,
}

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn fail(msg: &str) -> i32 {
    println!("  {}{}{} {}", style::RED, style::CROSS, style::NC, msg);
    1
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

pub fn run(a: &Args) -> i32 {
    let mut sid = a.system_id.clone();
    // home: if not provided, reverse lookup local config by --system-id (empty check by truthiness, do not use Path's truthiness)
    let system_home = if a.home.is_empty() {
        if sid.is_empty() {
            return fail("reset requires --home (system home / platform root), or pass --system-id to look it up");
        }
        let sh = crate::core::home::system_home_from_sid(&sid);
        if sh.is_empty() {
            return fail(&format!(
                "--system-id {sid} has no local config to look up --home from (is this system not on this machine?)"
            ));
        }
        let p = std::path::PathBuf::from(&sh);
        println!(
            "  --home looked up from --system-id: {}",
            p.to_string_lossy()
        );
        p
    } else {
        crate::hosts::identity::normalize_platform_home(&crate::core::home::expand_user(&a.home))
    };
    if !system_home.exists() {
        return fail(&format!(
            "home directory does not exist: {}",
            system_home.to_string_lossy()
        ));
    }
    // --platform explicit override (registry validation) takes priority, otherwise determined by home characteristics
    let platform = match crate::hosts::identity::platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => crate::hosts::identity::resolve_platform(&system_home),
        Err(msg) => return fail(&msg),
    };
    if platform.is_empty() {
        return fail("cannot determine platform");
    }
    if sid.is_empty() {
        sid = crate::hosts::pointer::sid_for_name(&crate::core::home::user_home(), &platform);
    }
    if sid.is_empty() {
        return fail("Cannot determine system_id (use --system-id to specify)");
    }

    // admin-key: read-only (system-level raw key file preferred, then existing config)
    let raw_key_path = config::systems_root_in(&crate::core::home::aimail_home())
        .join(&sid)
        .join(setup::SYSTEM_RAW_KEY_FILE);
    let mut admin_key = std::fs::read_to_string(&raw_key_path)
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if admin_key.is_empty() {
        if let Some(c) = config::load_gateway_config(&sid) {
            admin_key = c.admin_key;
        }
    }
    if admin_key.is_empty() {
        return fail(&format!(
            "No admin-key ({} or config) —— reset only uses admin-key path",
            raw_key_path.to_string_lossy()
        ));
    }

    // Default parameters: CLI explicit override > prev (system_name only accepts explicit + prev, does not take .env: identity field)
    let prev = config::load_gateway_config(&sid);
    let pget = |k: &str| -> String {
        prev.as_ref()
            .and_then(|c| match k {
                "gateway_url" => Some(c.gateway_url.clone()),
                "system_name" => Some(c.system_name.clone()),
                "manager_address" => Some(c.manager_address.clone()),
                "system_home" => Some(c.system_home.clone()),
                _ => None,
            })
            .unwrap_or_default()
    };
    let gw_url = if !a.gateway_url.is_empty() {
        a.gateway_url.clone()
    } else {
        let e = config::env_val("AIMAIL_GW_URL", "");
        if !e.is_empty() {
            e
        } else {
            pget("gateway_url")
        }
    };
    let sys_name = if !a.system_name.is_empty() {
        a.system_name.clone()
    } else {
        pget("system_name")
    };
    let manager = if !a.manager.is_empty() {
        a.manager.clone()
    } else {
        let e = config::env_val("AIMAIL_MANAGER_ADDRESS", "");
        if !e.is_empty() {
            e
        } else {
            pget("manager_address")
        }
    };

    println!("  reset platform={platform} system_id={sid}");
    // P8(2026-10-17): In-process INTEGRATE_* env handshake removed, credentials via SetupArgs explicit parameters;
    // Only keep manager → AIMAIL_MANAGER_ADDRESS write-back (SDK subprocess inherits process env and reads directly, contract name).
    if !manager.is_empty() {
        std::env::set_var("AIMAIL_MANAGER_ADDRESS", &manager);
    }

    let wa = setup::SetupArgs {
        gateway_url: gw_url.clone(),
        system_id: sid.clone(),
        admin_key: admin_key.clone(),
        product_code: String::new(),
        system_name: sys_name.clone(),
        domain: config::env_val("AIMAIL_DOMAIN", ""),
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
    let r = setup::setup(&wa);
    if !r.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
        return fail("setup_system(reset) failed");
    }
    ok("config parameters reset (admin-key path; least-privilege key re-derived)");

    // Idempotent re-run of registration chain (registry-driven; best-effort semantics: failure only warns, does not block reset)
    let cfg: Value = config::load_gateway_config(&sid)
        .map(|c| Value::Object(c.to_json()))
        .unwrap_or_else(|| json!({}));
    // Platform value priority is **system-level cfg recorded `platform`** (written by `aimail install`, authoritative);
    // `--platform` / detection may fail in shared domain scenarios ⇒ the platform passed to the registration chain must be the same single source of truth as transport.
    let recorded = cfg
        .get("platform")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let platform = if recorded.is_empty() {
        platform
    } else {
        recorded
    };
    // In-domain host interpreter binding (contract §4.1(2) single source of truth) —— **must be before resolving SDK root**:
    // Otherwise, use the CLI's own `python3` to find the SDK, and the installed SDK in the host venv will be judged as "not installed".
    let _ = crate::core::sdkroot::bind_domain(
        &setup::pget(&cfg, "container"),
        &system_home.to_string_lossy(),
    );
    // transport dispatch (contract §4.1(2)): **only python host** needs SDK root (the gate's interpreter has it built-in);
    // The op entry for the node host (node_entry) is directly reached by the registry `ops.argv` => must not hard exit due to missing pysdk.
    let core_dir = if crate::hosts::get(&platform)
        .map(|h| h.op_template().is_some())
        .unwrap_or(false)
    {
        std::path::PathBuf::new()
    } else {
        crate::core::sdkroot::resolve_or_placeholder().path
    };
    let home_str = system_home.to_string_lossy().to_string();
    if a.all_agents {
        if let Err(e) = register::register_all(&platform, &cfg, &manager, &home_str, &core_dir) {
            warn(&format!(
                "registration chain re-run: registration failed (see above); exception: {e}"
            ));
        }
    } else {
        let agent = "agent".to_string(); // Naming belongs to SDK (default agent), CLI no longer looks up table
        if let Err(e) =
            register::register_agent(&platform, &agent, &cfg, &manager, &home_str, &core_dir)
        {
            warn(&format!(
                "registration chain re-run: registration failed (see above); exception: {e}"
            ));
        } else {
            ok("registration chain re-run (idempotent)");
        }
    }
    // Route reconciliation: equivalent of `_ensure_inbound_routes` (best-effort, never changes rc)
    crate::core::bridge_wire::ensure_inbound_routes(&sid);
    println!("  Suggestion: aimail check --system-id {sid}");
    0
}
