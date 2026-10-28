//! Rust re-implementation of `cli/setup_system.py` — **L1 only activates/reuses the worker** (P1 chokepoint).
//!
//! Contract (depends on `cmd_ensure_system` in `cli/aimail:1076-1240`):
//! · SDK callback entry, **never** performs platform wiring, **never** deploys bridge (skipping them is the key to breaking the install↔plugin call cycle);
//! · Two paths: Path A = `admin_key` (reuse/reset of activated system); Path B = `product_code` (new system activation);
//! · Returns Python-shaped dict (`{success, error?, system_id?, path?, admin_key?}`), caller decides how to broadcast.
//!
//! Copy points (easy to miss, each from Python original):
//! 1 `webhook_host` three-state: single source = `webhookplan` (owner 2026-10-13) — written after install probing
//!   (S1 url / S2 empty string / S3 key absent); admin_key path (reset semantics) here **only inherits existing cfg**
//!   (measured probed IPv6 address once overwrote NAT public value), does not write tri-state;
//! 2 Path A `whoami` identity alignment: platform level key **rejected** (must stay in gateway), system_id can be adopted from key,
//!   mismatch then error; when `whoami` unavailable only warn **do not assume**;
//! 3 Path A "empty args inherit existing values" (reset semantics) + after write-back, **restore** business fields in prev not overwritten
//!   (once lost business fields due to missing this protection);
//! 4 `_downgrade_to_domain_admin_key`: whoami pre-check (already agent/domain level ⇒ no downgrade) + existing domain key in config
//!   domain key => reuse without rotation + no domain to narrow => keep system-level key + failure message contains "privilege level"/"at or above"
//!   ⇒ treated as "no downgrade needed" (**must not** persist restricted key as system key);
//! 5 System level key **persisted immediately upon acquisition** (`.system_raw_key.key`), regardless of subsequent downgrade success; restricted key not persisted.

use serde_json::{json, Map, Value};

use crate::core::{config, home};

/// Raw system level key file (`setup_system._persist_system_raw_key`).
pub const SYSTEM_RAW_KEY_FILE: &str = ".system_raw_key.key";

#[derive(Default, Clone)]
pub struct SetupArgs {
    pub gateway_url: String,
    pub system_id: String,
    pub admin_key: String,
    pub product_code: String,
    pub system_name: String,
    pub domain: String,
    pub save_raw_snapshots: bool,
    pub manager_address: String,
    pub system_home: String,
    /// Explicitly provided `-n/--system-name` (reset semantics: decides whether to inherit old system_name).
    pub name_explicit: bool,
}

/// Python side logging standard (copy as-is, do not "casually" upgrade):
/// · Default root level is **WARNING** ⇒ `logger.info/debug` **not output**;
/// · No handler goes to lastResort ⇒ **bare message** (no `LEVEL:name:` prefix) printed to stderr;
/// · Always stderr (stdout is the contract carrier for "exactly one line of JSON", must not be polluted by logs).
fn log(level: &str, msg: &str) {
    if level == "WARNING" || level == "ERROR" {
        eprintln!("{msg}");
    }
}

fn err(msg: &str) -> Value {
    json!({ "success": false, "error": msg })
}

/// `setup(**kwargs)`（`setup_system.py:466-630`）。
pub fn setup(a: &SetupArgs) -> Value {
    if a.gateway_url.is_empty() {
        return err("gateway_url is required");
    }
    // ── Path A: admin_key (reuse/reset for activated systems) ──────────────────────────────
    if !a.admin_key.is_empty() {
        let mut system_id = a.system_id.clone();
        let me = crate::core::gateway::whoami(&a.gateway_url, &a.admin_key, "");
        let me_empty = matches!(&me, Value::Object(m) if m.is_empty());
        if !me_empty {
            let cat = me
                .get("category")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let scope = me
                .get("scope")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let ksid = me
                .get("system_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            if cat == "platform" || scope == "platform" {
                return err(
                    "the key is the gateway-side admin key (platform scope) — it must stay on the \
                     gateway and is not used for agent integration. Use the agent-side system key \
                     file (<storage dir>/<system id>.system.key, printed once when the gateway \
                     starts) with -k.",
                );
            }
            if !ksid.is_empty() && system_id.is_empty() {
                log("INFO", &format!("system_id taken from the key: {ksid}"));
                system_id = ksid;
            } else if !ksid.is_empty() && ksid != system_id {
                return err(&format!(
                    "key/system mismatch: this key belongs to system {ksid} but system_id {system_id} \
                     was given — pass the key of {system_id}, or use the key of {ksid} without \
                     --system-id."
                ));
            }
        } else {
            log(
                "WARNING",
                &format!(
                    "could not verify the admin key against {} (gateway unreachable or key unknown) — continuing",
                    a.gateway_url
                ),
            );
        }
        if system_id.is_empty() {
            return err("system_id is required for admin_key path");
        }

        // reset semantics: when existing config is present, empty parameters inherit existing values (only rewrite core connection parameters)
        let prev = config::load_gateway_config(&system_id)
            .map(|c| c.to_json())
            .unwrap_or_default();
        let pget = |k: &str| -> String {
            prev.get(k)
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        };
        let explicit_name = a.name_explicit;
        let prev_has_snaps = prev.contains_key("save_raw_snapshots");
        let snaps = if a.save_raw_snapshots || !prev_has_snaps {
            a.save_raw_snapshots
        } else {
            prev.get("save_raw_snapshots")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        };
        let domain = if a.domain.is_empty() {
            let d = pget("domain");
            if d.is_empty() {
                "admin.local".to_string()
            } else {
                d
            }
        } else {
            a.domain.clone()
        };
        let system_name = if explicit_name {
            a.system_name.clone()
        } else {
            let p = pget("system_name");
            if p.is_empty() {
                a.system_name.clone()
            } else {
                p
            }
        };
        let cfg = config::GatewayConfig {
            gateway_url: a.gateway_url.clone(),
            admin_key: a.admin_key.clone(),
            system_id: system_id.clone(),
            system_name,
            save_raw_snapshots: snaps,
            domain,
            manager_address: if a.manager_address.is_empty() {
                pget("manager_address")
            } else {
                a.manager_address.clone()
            },
            // webhook_host tri-state: single source of truth = webhookplan (written after install probe); reset only inherits existing values
            // (prev key absent => None=S3; prev key present with empty string => Some(value), preserve S2 empty string on reset).
            webhook_host: prev
                .get("webhook_host")
                .and_then(Value::as_str)
                .map(|s| s.to_string()),
            system_home: if a.system_home.is_empty() {
                pget("system_home")
            } else {
                a.system_home.clone()
            },
            ..Default::default()
        };
        if let Err(e) = config::save_gateway_config_in(&home::aimail_home(), &system_id, &cfg) {
            return err(&format!("failed to write config: {e}"));
        }
        // Restore business fields from prev that were not overwritten (general protection)
        restore_prev_keys(&system_id, &prev);

        // Note: the domain passed for downgrade is the one **without default value applied** (Python `domain or prev.get("domain","")`);
        // What is written to cfg is the one with "admin.local" default -- they differ, do not merge
        // (Empirical: merging causes "no domain" systems to incorrectly downgrade with a domain key).
        let downgrade_domain = if a.domain.is_empty() {
            pget("domain")
        } else {
            a.domain.clone()
        };
        let agent_key = downgrade_to_domain_admin_key(
            &a.gateway_url,
            &a.admin_key,
            &system_id,
            &downgrade_domain,
            &pget("admin_key"),
        );
        return json!({
            "success": true,
            "system_id": system_id,
            "path": "admin_key",
            "admin_key": agent_key,
        });
    }

    // ── Path B: product_code (new system activation) ─────────────────────────────────────
    if !a.product_code.is_empty() {
        let mut r = init_system(
            &a.gateway_url,
            &a.product_code,
            &a.system_id,
            &a.system_name,
            &a.domain,
            a.save_raw_snapshots,
            &a.manager_address,
            &a.system_home,
        );
        if r.get("success").and_then(Value::as_bool).unwrap_or(false) {
            if let Some(o) = r.as_object_mut() {
                o.insert("path".into(), Value::String("activation".into()));
            }
        }
        return r;
    }

    err("Either admin_key or product_code is required")
}

/// After `_save_gateway_config`, restore keys from prev that were not overwritten (`setup_system.py:590-605`).
fn restore_prev_keys(system_id: &str, prev: &Map<String, Value>) {
    let path = config::gateway_config_path(system_id);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return;
    };
    let Ok(Value::Object(mut cfg)) = serde_json::from_str::<Value>(&text) else {
        return;
    };
    let written: [&str; 9] = [
        "gateway_url",
        "admin_key",
        "system_id",
        "system_name",
        "save_raw_snapshots",
        "domain",
        "manager_address",
        "webhook_host",
        "system_home",
    ];
    for (k, v) in prev {
        if !cfg.contains_key(k) && !written.contains(&k.as_str()) {
            cfg.insert(k.clone(), v.clone());
        }
    }
    let text = serde_json::to_string_pretty(&Value::Object(cfg)).unwrap_or_default();
    let _ = std::fs::write(&path, text);
}

/// `_persist_system_raw_key` (idempotent; existing **different** value => keep old value).
pub fn persist_system_raw_key(system_id: &str, key: &str) {
    if key.is_empty() {
        return;
    }
    let dir = home::aimail_home().join("systems").join(system_id);
    let p = dir.join(SYSTEM_RAW_KEY_FILE);
    if let Err(e) = std::fs::create_dir_all(&dir) {
        log("WARNING", &format!("failed to persist raw system key: {e}"));
        return;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
    }
    if p.is_file() {
        if let Ok(cur) = std::fs::read_to_string(&p) {
            if !cur.trim().is_empty() {
                log(
                    "DEBUG",
                    &format!("raw system key already on disk for {system_id}"),
                );
                return;
            }
        }
    }
    match config::write_private_text(&p, &format!("{key}\n")) {
        Ok(()) => log("INFO", &format!("raw system key saved ({})", p.display())),
        Err(e) => log("WARNING", &format!("failed to persist raw system key: {e}")),
    }
}

/// `_write_admin_key`: replace `admin_key` in existing config (keep other fields, rewrite to disk).
pub fn write_admin_key(cfg_path: &std::path::Path, key: &str) {
    let Ok(text) = std::fs::read_to_string(cfg_path) else {
        return;
    };
    let Ok(Value::Object(mut cfg)) = serde_json::from_str::<Value>(&text) else {
        return;
    };
    cfg.insert("admin_key".into(), Value::String(key.to_string()));
    let _ = std::fs::write(
        cfg_path,
        serde_json::to_string_pretty(&Value::Object(cfg)).unwrap_or_default(),
    );
}

/// `_downgrade_to_domain_admin_key`（`setup_system.py:76-202`）。
///
/// Success ⇒ new domain-level key (and write back to config); no downgrade/failure needed ⇒ **original system-level key** (never persist a restricted key as a system key).
pub fn downgrade_to_domain_admin_key(
    gateway_url: &str,
    system_admin_key: &str,
    system_id: &str,
    domain: &str,
    existing_key: &str,
) -> String {
    // 0) whoami pre-check: already at restricted level ⇒ no downgrade needed (gateway will reject with "cannot create scopes at level 1 or above")
    let me = crate::core::gateway::whoami(gateway_url, system_admin_key, system_id);
    let me_nonempty = matches!(&me, Value::Object(m) if !m.is_empty());
    if me_nonempty {
        let scope = me
            .get("scope")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase();
        let cat = me
            .get("category")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase();
        let email = me
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if cat == "agent" || cat == "agent_admin" || scope == "agent" || scope == "agent_admin" {
            log("INFO", "key already agent-scoped — downgrade not needed");
            return system_admin_key.to_string();
        }
        if cat == "system" || (scope == "system" && email.is_empty()) {
            // whoami proves this is a system-level key (empty email) ⇒ persist immediately ("persist upon acquisition" contract)
            persist_system_raw_key(system_id, system_admin_key);
        }
        if cat == "domain" && !email.is_empty() && email == domain {
            log(
                "INFO",
                &format!("key already domain-scoped ({email}) — downgrade not needed"),
            );
            return system_admin_key.to_string();
        }
    }

    // 0b) Config already stores **this domain's** domain-level key ⇒ reuse without rotation
    if !domain.is_empty() && !existing_key.is_empty() && existing_key != system_admin_key {
        let ex = crate::core::gateway::whoami(gateway_url, existing_key, "");
        let ex_ok = ex
            .get("category")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase()
            == "domain"
            && ex.get("email").and_then(Value::as_str).unwrap_or("") == domain;
        if ex_ok {
            log(
                "INFO",
                &format!("config already holds a domain-scoped key for {domain} — keeping it (no rotation)"),
            );
            write_admin_key(&config::gateway_config_path(system_id), existing_key);
            return existing_key.to_string();
        }
    }

    if domain.is_empty() {
        log(
            "INFO",
            &format!(
                "system {system_id} has no domain — skipping the least-privilege downgrade (the \
                 domain-category key needs the bare domain); the config keeps the system-level key"
            ),
        );
        return system_admin_key.to_string();
    }

    let scopes = vec!["system".to_string()];
    let result = crate::core::gateway::create_api_key(
        gateway_url,
        system_admin_key,
        system_id,
        domain,
        &scopes,
        "domain",
    );
    let raw = result
        .get("raw_key")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if raw.is_empty() {
        let e = result.get("error").and_then(Value::as_str).unwrap_or("");
        let d = result.get("detail").and_then(Value::as_str).unwrap_or("");
        let err_text = format!("{e} {d}").to_lowercase();
        if err_text.contains("privilege level") || err_text.contains("at or above") {
            log(
                "INFO",
                "key already agent/domain-scoped — downgrade not needed",
            );
            return system_admin_key.to_string();
        }
        log(
            "ERROR",
            &format!(
                "domain-scoped key NOT created ({e} {d}) — FALLING BACK TO SYSTEM KEY: the agent \
                 runtime keeps system-level privileges instead of the intended least-privilege \
                 scope (registration still works — a system key is unrestricted). Re-run `aimail \
                 repair` / install once the gateway accepts it."
            ),
        );
        return system_admin_key.to_string();
    }

    persist_system_raw_key(system_id, system_admin_key);
    write_admin_key(&config::gateway_config_path(system_id), &raw);
    log(
        "INFO",
        &format!("domain-scoped admin key created and saved (domain={domain})"),
    );
    raw
}

/// `init_system`（Path B，`setup_system.py:377-459`）。
#[allow(clippy::too_many_arguments)]
pub fn init_system(
    gateway_url: &str,
    product_code: &str,
    system_id: &str,
    system_name: &str,
    domain: &str,
    save_raw_snapshots: bool,
    manager_address: &str,
    system_home: &str,
) -> Value {
    if product_code.is_empty() {
        return err("product_code is required");
    }
    let client = crate::core::gateway::GatewayClient::new(gateway_url, "", "", 30);
    let mut body = Map::new();
    body.insert("code".into(), Value::String(product_code.to_string()));
    if !system_name.is_empty() {
        body.insert("system_name".into(), Value::String(system_name.to_string()));
    }
    if !domain.is_empty() {
        body.insert("domain".into(), Value::String(domain.to_string()));
    }
    let result = client.post("/api/v1/activate-system", &Value::Object(body));
    let status = result.get("status").cloned().unwrap_or(json!(0));
    // Success criteria copied verbatim: success ∈ {true,"true","ok"} / status ∈ {activated,200,201} / has raw_key
    let is_ok = matches!(result.get("success"), Some(Value::Bool(true)))
        || matches!(
            result.get("success").and_then(Value::as_str),
            Some("true") | Some("ok")
        )
        || matches!(
            status.as_str().map(|s| s.to_lowercase()).as_deref(),
            Some("activated") | Some("200") | Some("201")
        )
        || result
            .get("raw_key")
            .and_then(Value::as_str)
            .map(|s| !s.is_empty())
            .unwrap_or(false);
    if !is_ok {
        let e = result
            .get("error")
            .and_then(Value::as_str)
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("Activation failed (HTTP {status})"));
        return json!({ "success": false, "error": e, "status": status });
    }
    let admin_key = result
        .get("raw_key")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    // System-level default manager from the activation response (type 1/2
    // activation codes persist the code's delivery address as the system
    // default manager, same mechanism as the type-3 address code). Used as
    // the manager default when the caller did not set one explicitly —
    // an explicit AIMAIL_MANAGER_ADDRESS / -m always wins.
    let resp_manager = result
        .get("manager_address")
        .and_then(Value::as_str)
        .filter(|m| !m.is_empty())
        .unwrap_or("")
        .to_string();
    let created_system_id = result
        .get("system_id")
        .and_then(Value::as_str)
        .unwrap_or(system_id)
        .to_string();
    let created_domain = result
        .get("domain")
        .and_then(Value::as_str)
        .unwrap_or(domain)
        .to_string();
    if admin_key.is_empty() {
        return json!({ "success": false, "error": "No admin_key returned from server", "status": status });
    }
    persist_system_raw_key(&created_system_id, &admin_key);
    let sys_name_final = if system_name.is_empty() {
        result
            .get("system_name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        system_name.to_string()
    };
    let cfg = config::GatewayConfig {
        gateway_url: gateway_url.to_string(),
        admin_key: admin_key.clone(),
        system_id: created_system_id.clone(),
        system_name: sys_name_final.clone(),
        save_raw_snapshots,
        domain: created_domain.clone(),
        // Explicit value wins; otherwise use the activation response's
        // system default manager.
        manager_address: prefer_explicit_manager(manager_address, &resp_manager),
        // webhook_host tri-state: single source of truth = webhookplan (backfilled after install probe); at activation time, key is always absent
        webhook_host: None,
        system_home: if system_home.is_empty() {
            String::new()
        } else {
            home::abs_path(std::path::Path::new(system_home))
                .to_string_lossy()
                .to_string()
        },
        ..Default::default()
    };
    if let Err(e) = config::save_gateway_config_in(&home::aimail_home(), &created_system_id, &cfg) {
        return err(&format!("failed to write config: {e}"));
    }
    log(
        "INFO",
        &format!(
            "Gateway config saved to {}",
            config::gateway_config_path(&created_system_id).display()
        ),
    );
    let agent_key = downgrade_to_domain_admin_key(
        gateway_url,
        &admin_key,
        &created_system_id,
        &created_domain,
        "",
    );
    json!({
        "success": true,
        "system_id": created_system_id,
        "admin_key": agent_key,
        "gateway_url": gateway_url,
        "domain": created_domain,
        "system_name": sys_name_final,
    })
}

/// Broadcast shape of `__main__` (`setup_system.py:637-658`): indented JSON **without success/path**.
pub fn display_json(result: &Value) -> String {
    let mut map = match result {
        Value::Object(m) => m.clone(),
        _ => return "{}".to_string(),
    };
    map.remove("success");
    map.remove("path");
    serde_json::to_string_pretty(&Value::Object(map)).unwrap_or_else(|_| "{}".to_string())
}

/// manager env name: `AIMAIL_MANAGER_ADDRESS` is the single contract name.
/// (2026-10-17 owner ruling: historical bare name `AIMAIL_MANAGER` retired,
/// treated as non-existent; INTEGRATE_* names removed with no compatibility.)
pub const MANAGER_ENV_NAMES: [&str; 1] = ["AIMAIL_MANAGER_ADDRESS"];

/// Get string field from cfg (default empty string) -- equivalent to Python `cfg.get(k, "")`.
pub fn pget(cfg: &Value, key: &str) -> String {
    cfg.get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

/// Interpreter: **single source of truth delegation** `core::sdkroot::python_bin` (contract §4.1(2)).
/// This function was once a second fallback chain (`AIMAIL_PYTHON` → `python3`, without even in-domain probing) ⇒ implementation removed, only delegation kept.
pub fn python_bin() -> String {
    crate::core::sdkroot::python_bin()
}

/// `_resolve_mgr`: manager resolution chain = explicit parameter (must contain `@`) → cfg → env `AIMAIL_MANAGER_ADDRESS` (single contract name).
/// Empty string indicates unresolved (caller handles as hard gate: registration/whitelist writing does not accept empty values).
pub fn resolve_mgr(manager: &str, cfg: &Value) -> String {
    if !manager.is_empty() && manager.contains('@') {
        return manager.to_string();
    }
    let v = pget(cfg, "manager_address");
    if !v.is_empty() {
        return v;
    }
    for key in MANAGER_ENV_NAMES {
        let v = crate::core::config::env_val(key, "");
        if !v.is_empty() {
            return v;
        }
    }
    String::new()
}

/// Manager written into the system config at activation: an explicit value
/// (arg / `AIMAIL_MANAGER_ADDRESS`) always wins; when the caller gave none,
/// the activation response's system default manager is used — that default is
/// the activation code's delivery address, persisted server-side at
/// activation (same mechanism as the type-3 address code).
pub fn prefer_explicit_manager(explicit: &str, from_activation: &str) -> String {
    if explicit.is_empty() {
        from_activation.to_string()
    } else {
        explicit.to_string()
    }
}

#[cfg(test)]
mod manager_default_tests {
    use super::prefer_explicit_manager;

    #[test]
    fn explicit_manager_wins_over_activation_default() {
        assert_eq!(
            prefer_explicit_manager("explicit@x.com", "delivery@x.com"),
            "explicit@x.com"
        );
    }

    #[test]
    fn activation_default_used_when_caller_gave_none() {
        assert_eq!(
            prefer_explicit_manager("", "delivery@x.com"),
            "delivery@x.com"
        );
    }

    #[test]
    fn no_source_stays_empty() {
        assert_eq!(prefer_explicit_manager("", ""), "");
    }
}
