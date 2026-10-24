//! L0 config-layer probe — the re-implementation of `cli/check_status.py:983-1185`.
//!
//! Three parts:
//! 1. [`l0_configs`] system-level environment file completeness + `system_home` + pointer ownership (`:989-1023`);
//! 2. [`agentmail_json`] per-agent binding file nine-field completeness + internal consistency (`:1055-1090`);
//! 3. [`bridge_completeness`] bridge TOML structure + pull entries + routes coverage (`:1093-1184`).
//!
//! Read **raw JSON** (not [`crate::core::config::GatewayConfig`]): the Python side interprets these places by
//! dict semantics (`if sid and gw:` checks "dict is not empty"), using raw Value matches correctly.

use crate::core::check::Check;
use crate::core::contract;
use crate::core::probe;
use crate::core::{config, home};
use crate::hosts::Host;
use serde_json::Value;
use std::path::{Path, PathBuf};

/// Root directories on both sides required by the probe (explicitly passed in => tests can point to fixtures, not relying on process environment).
#[derive(Debug, Clone)]
pub struct Ctx {
    pub aimail_home: PathBuf,
    pub user_home: PathBuf,
}

impl Ctx {
    /// Get the real root from the process environment (used by the command interface).
    pub fn from_env() -> Self {
        Self {
            aimail_home: home::aimail_home(),
            user_home: home::user_home(),
        }
    }
}

/// Nine required fields for binding files (verbatim list from `check_status.py:983-986`).
pub const BINDING_REQUIRED: &[&str] = &[
    "email",
    "gateway_url",
    "domain",
    "system_id",
    "system_name",
    "manager_address",
    "api_key",
    "webhook_url",
    "webhook_secret",
];

/// `_read_gw_cfg`: read system-level environment file, missing/unreadable/non-JSON => None.
pub fn read_gw_cfg(ctx: &Ctx, sid: &str) -> Option<Value> {
    let path = config::gateway_config_path_in(&ctx.aimail_home, sid);
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn field_str<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or("")
}

fn is_falsy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => true,
        Some(Value::String(s)) => s.is_empty(),
        Some(Value::Bool(b)) => !b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        _ => false,
    }
}

/// Platform root directory (registry `home_dir`, default `.{name}`) — same algorithm as `_pointer_platforms_for_sid`.
fn platform_root(user_home: &Path, name: &str) -> PathBuf {
    match crate::hosts::get(name).map(|h| h.home_dir()) {
        Some(dir) => user_home.join(dir),
        None => user_home.join(format!(".{name}")),
    }
}

/// Which platforms' pointer files reference this sid (`check_status.py:1026-1052`; **no deduplication**, consistent with Python).
pub fn pointer_platforms_for_sid(ctx: &Ctx, sid: &str) -> Vec<String> {
    let mut candidates: Vec<(String, PathBuf)> = Vec::new();
    for name in crate::hosts::order() {
        let root = platform_root(&ctx.user_home, name);
        let ptr_name = contract::pointer_file();
        let root_ptr = root.join(ptr_name);
        if root_ptr.is_file() {
            candidates.push((name.to_string(), root_ptr));
        }
        if crate::hosts::pointer::kind_of(name) == "root_or_profiles" {
            let profiles = root.join("profiles");
            if profiles.is_dir() {
                let mut hits: Vec<PathBuf> = std::fs::read_dir(&profiles)
                    .into_iter()
                    .flatten()
                    .flatten()
                    .map(|e| e.path().join(ptr_name))
                    .filter(|p| p.is_file())
                    .collect();
                hits.sort();
                for p in hits {
                    candidates.push((name.to_string(), p));
                }
            }
        }
    }
    candidates
        .into_iter()
        .filter(|(_, ptr)| ptr.is_file())
        .filter(|(_, ptr)| {
            std::fs::read_to_string(ptr)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("system_id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .as_deref()
                == Some(sid)
        })
        .map(|(name, _)| name)
        .collect()
}

/// L0: system-level environment file completeness + `system_home` + pointer ownership.
pub fn l0_configs(c: &mut Check, sid: &str, ctx: &Ctx) {
    let Some(gw) = read_gw_cfg(ctx, sid) else {
        c.add(
            "config",
            "gateway_json",
            false,
            &format!("{} missing/unreadable", config::GATEWAY_CONFIG_NAME),
            &format!(
                "Run: aimail install --home <platform-root> --system-id {}",
                if sid.is_empty() { "<sid>" } else { sid }
            ),
        );
        return;
    };

    // 1. Connection field completeness
    let missing: Vec<&str> = ["gateway_url", "admin_key"]
        .iter()
        .filter(|k| is_falsy(gw.get(**k)))
        .copied()
        .collect();
    c.add(
        "config",
        "complete",
        missing.is_empty(),
        &if missing.is_empty() {
            "gateway_url + admin_key present".to_string()
        } else {
            format!("missing: {}", missing.join(", "))
        },
        &format!("Run: aimail install --system-id {sid}"),
    );

    // 2. system_home exists and directory is valid
    let sh = field_str(&gw, "system_home");
    if sh.is_empty() {
        c.add(
            "config",
            "system_home",
            false,
            "system_home MISSING (stats platform label → [?])",
            &format!("Run: aimail install --home <platform-root> --system-id {sid}"),
        );
    } else if !Path::new(sh).is_dir() {
        c.add(
            "config",
            "system_home",
            false,
            &format!("system_home dir does not exist: {sh}"),
            &format!("Fix path or re-run: aimail install --home <platform-root> --system-id {sid}"),
        );
    } else {
        c.add("config", "system_home", true, sh, "");
    }

    // 3. pointer ownership (any platform pointer referencing this sid is sufficient)
    let hits = pointer_platforms_for_sid(ctx, sid);
    c.add(
        "config",
        "pointer",
        !hits.is_empty(),
        &if hits.is_empty() {
            format!(
                "no platform {} pointer references this system",
                contract::pointer_file()
            )
        } else {
            format!("pointer: {}", hits.join(","))
        },
        &format!("Re-run platform install or aimail repair --system-id {sid}"),
    );
}

/// L0 extension: per-agent binding file completeness + internal consistency.
pub fn agentmail_json(c: &mut Check, sid: &str, ctx: &Ctx) {
    let sysdir = ctx.aimail_home.join("systems").join(sid);
    if !sysdir.is_dir() {
        return;
    }
    let gw = read_gw_cfg(ctx, sid);
    for sub in sorted_subdirs(&sysdir) {
        let sub_name = file_name_of(&sub);
        let aj = sub.join(contract::binding_file());
        if !aj.is_file() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&aj) else {
            continue;
        };
        let d: Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(e) => {
                c.add(
                    "agent",
                    "config-json",
                    false,
                    &format!("{sub_name}: parse error: {e}"),
                    "Re-run `openclaw aimail register`",
                );
                continue;
            }
        };
        let name = {
            let a = field_str(&d, "agent_id");
            if a.is_empty() {
                sub_name.clone()
            } else {
                a.to_string()
            }
        };

        let missing: Vec<&str> = BINDING_REQUIRED
            .iter()
            .filter(|k| is_falsy(d.get(**k)))
            .copied()
            .collect();
        c.add(
            "agent",
            "config-complete",
            missing.is_empty(),
            &if missing.is_empty() {
                format!("{name}: all {} fields present", BINDING_REQUIRED.len())
            } else {
                format!("{name}: missing fields: {}", missing.join(", "))
            },
            "Re-run `openclaw aimail register`(api_key/webhook_secret cannot be rebuilt locally)",
        );

        // Internal consistency: system_id / gateway_url / domain vs email
        let mut issues: Vec<String> = Vec::new();
        let d_sid = field_str(&d, "system_id");
        if !d_sid.is_empty() && d_sid != sid {
            issues.push(format!(
                "system_id {}… != {}…",
                take_chars(d_sid, 16),
                take_chars(sid, 16)
            ));
        }
        let d_gw = field_str(&d, "gateway_url");
        if !d_gw.is_empty() {
            if let Some(g) = gw.as_ref() {
                let g_gw = field_str(g, "gateway_url");
                if !g_gw.is_empty() && d_gw.trim_end_matches('/') != g_gw.trim_end_matches('/') {
                    issues.push(format!(
                        "gateway_url differs from {}",
                        config::GATEWAY_CONFIG_NAME
                    ));
                }
            }
        }
        let email = field_str(&d, "email");
        let domain = field_str(&d, "domain");
        if !email.is_empty() && !domain.is_empty() && !email.ends_with(&format!("@{domain}")) {
            issues.push(format!("email domain != domain field ({domain})"));
        }
        c.add(
            "agent",
            "config-consistency",
            issues.is_empty(),
            &if issues.is_empty() {
                format!("{name}: consistent")
            } else {
                format!("{name}: {}", issues.join("; "))
            },
            "Re-run `openclaw aimail register`",
        );
    }
}

/// L0 extension: **Inbound webhook plan drift** (`webhook_host` tri-state vs decision function single source of truth, offline).
///
/// `webhook_host` is owned by the **decision function** (install writes, check verifies, repair does not write). We do not re-run
/// Detect, do not re-judge state — only call [`webhookplan::plan_drift`] to judge "whether the persisted three-state is the decision function **offline**
/// falsify" (S2 empty string meets local GW / no bridge deployable; S1 value unparseable). Network facts (B bridge alive /
/// R direct connection reachable) not checked: check is reused ~20 times in the repair write path, and network state fluctuates with process start/stop,
/// checking it would be a false red. `plan_drift` returns `None` (no contradiction) ⇒ **no record emitted** (silent pass, no noise).
pub fn webhook_plan_drift(c: &mut Check, sid: &str, ctx: &Ctx) {
    let Some(gw) = read_gw_cfg(ctx, sid) else {
        return; // Missing configuration is reported by `gateway_json` (L0 first step)
    };
    let stored: Option<String> = match gw.get("webhook_host") {
        Some(Value::String(s)) => Some(s.clone()),
        Some(_) => return, // Non-string (bad shape) ⇒ other checks catch it
        None => None,
    };
    let gw_is_local = crate::core::gateway::is_local_gateway(field_str(&gw, "gateway_url"));
    let bridge_deployable = crate::core::bridge_deploy::bridge_deployable();
    let Some(why) =
        crate::core::webhookplan::plan_drift(stored.as_deref(), gw_is_local, bridge_deployable)
    else {
        return; // No contradiction ⇒ no record emitted
    };
    c.add(
        "config",
        "webhook-plan-drift",
        false,
        &why,
        &format!(
            "Re-run the install plan: aimail install --system-id {sid} (it re-derives the inbound webhook state); if the gateway topology really changed, re-planning is the fix"
        ),
    );
}

/// L0 extension: Bridge configuration completeness + pull entries + routes coverage.
pub fn bridge_completeness(c: &mut Check, sid: &str, ctx: &Ctx) {
    let cfg = ctx.aimail_home.join("bridge").join("aimail_bridge.toml");
    if !cfg.exists() {
        return; // direct-connect is legal, check_bridge (L2) will report
    }
    let td: toml::Value = match std::fs::read_to_string(&cfg)
        .map_err(|e| e.to_string())
        .and_then(|text| toml::from_str::<toml::Value>(&text).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            c.add(
                "bridge",
                "config-complete",
                false,
                &format!("toml parse error: {e}"),
                "Check aimail_bridge.toml syntax",
            );
            return;
        }
    };

    let mode = td.get("mode").and_then(toml::Value::as_str).unwrap_or("");
    let mode_ok = mode == "push" || mode == "pull";
    c.add(
        "bridge",
        "config-mode",
        mode_ok,
        &if mode_ok {
            format!("mode={mode}")
        } else {
            format!("invalid mode: '{mode}'")
        },
        "mode must be push or pull",
    );
    if mode != "pull" {
        return;
    }

    let systems: Vec<Value> = td
        .get("pull")
        .and_then(|p| p.get("systems"))
        .and_then(toml::Value::as_array)
        .map(|arr| arr.iter().map(toml_to_json).collect())
        .unwrap_or_default();
    let gw = read_gw_cfg(ctx, sid);
    let gw_truthy = gw.as_ref().map(json_object_nonempty).unwrap_or(false);
    if !sid.is_empty() && gw_truthy {
        let entry = systems
            .iter()
            .find(|x| field_str(x, "system_id") == sid)
            .cloned();
        match entry {
            None => c.add(
                "bridge",
                "pull-entry",
                false,
                &format!("no pull.systems entry for {sid}"),
                &format!("Run: aimail bridge --system-id {sid}"),
            ),
            Some(e) => {
                let g = gw.as_ref().expect("checked non-empty");
                let key_match = field_str(&e, "admin_key") == field_str(g, "admin_key");
                c.add(
                    "bridge",
                    "pull-entry",
                    key_match,
                    &format!(
                        "pull entry present, admin_key {}",
                        if key_match { "match" } else { "MISMATCH" }
                    ),
                    &format!("Re-run: aimail bridge --system-id {sid}"),
                );
            }
        }
    }

    // routes coverage: every local system agent's email has an entry in the delivery path table
    let routes_file = ctx.aimail_home.join("bridge").join("aimail_routes.toml");
    if !routes_file.exists() {
        return;
    }
    let Ok(routes_text) = std::fs::read_to_string(&routes_file) else {
        return;
    };
    let mut routes: Vec<(String, String)> = Vec::new();
    for line in routes_text.lines() {
        let line = line.trim();
        if line.contains('=') && !line.starts_with('#') {
            if let Some((k, v)) = line.split_once('=') {
                routes.push((
                    k.trim().trim_matches('"').to_string(),
                    v.trim()
                        .trim_matches('"')
                        .trim_matches('\'')
                        .trim_matches(',')
                        .to_string(),
                ));
            }
        }
    }
    let sysdir = ctx.aimail_home.join("systems").join(sid);
    if !sysdir.is_dir() {
        return;
    }
    for sub in sorted_subdirs(&sysdir) {
        let sub_name = file_name_of(&sub);
        let aj = sub.join(contract::binding_file());
        let Ok(text) = std::fs::read_to_string(&aj) else {
            continue;
        };
        let Ok(d) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let email = field_str(&d, "email").to_string();
        if email.is_empty() {
            continue;
        }
        let name = {
            let a = field_str(&d, "agent_id");
            if a.is_empty() {
                sub_name
            } else {
                a.to_string()
            }
        };
        let declared = field_str(&d, "webhook_url").to_string();
        let Some((_, target)) = routes.iter().find(|(k, _)| *k == email) else {
            c.add(
                "bridge",
                "routes-entry",
                false,
                &format!("{name}: no route for {email} (pull delivery dead)"),
                &format!("Run: aimail bridge --system-id {sid}"),
            );
            continue;
        };
        let target = target.clone();
        let host = url_host(&target);
        let local = host == "127.0.0.1" || host == "localhost" || host == "::1";
        let (target_ok, _) = probe::probe_endpoint(&target, probe::DEFAULT_TIMEOUT_SECS);
        if !target_ok && !local {
            c.add(
                "bridge",
                "routes-target",
                true,
                &format!("{name}: route target remote (not probeable locally): {target}"),
                "",
            );
            continue;
        }
        let declared_ok = if declared.is_empty() {
            false
        } else {
            probe::probe_endpoint(&declared, probe::DEFAULT_TIMEOUT_SECS).0
        };
        if !target_ok {
            c.add(
                "bridge",
                "routes-target",
                false,
                &format!("{name}: route target dead: {target}"),
                "Check the agent platform is running; do NOT auto-edit routes",
            );
        } else if !declared.is_empty() && !declared_ok {
            c.add(
                "bridge",
                "routes-target",
                false,
                &format!(
                    "{name}: declared webhook_url dead: {declared} (route target alive: {target})"
                ),
                &format!("Run: aimail repair --system-id {sid}"),
            );
        } else if !declared.is_empty()
            && target.trim_end_matches('/') != declared.trim_end_matches('/')
        {
            c.add(
                "bridge",
                "routes-target",
                true,
                &format!(
                    "{name}: paths differ but both alive (route={target} vs declared={declared}) \
                     — repair will align"
                ),
                &format!("Run: aimail repair --system-id {sid}"),
            );
        } else {
            c.add(
                "bridge",
                "routes-target",
                true,
                &format!("{name}: route target alive & consistent"),
                "",
            );
        }
    }
}

// ── Small utilities ─────────────────────────────────────────────────────

fn sorted_subdirs(dir: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    out.sort();
    out
}

fn file_name_of(path: &Path) -> String {
    path.file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn json_object_nonempty(v: &Value) -> bool {
    v.as_object().map(|o| !o.is_empty()).unwrap_or(false)
}

/// TOML → JSON view (only to reuse the same value retrieval code, semantics consistent with tomllib.load dict).
pub fn toml_to_json(v: &toml::Value) -> Value {
    match v {
        toml::Value::String(s) => Value::String(s.clone()),
        toml::Value::Integer(i) => Value::Number((*i).into()),
        toml::Value::Float(f) => serde_json::Number::from_f64(*f)
            .map(Value::Number)
            .unwrap_or(Value::Null),
        toml::Value::Boolean(b) => Value::Bool(*b),
        toml::Value::Datetime(d) => Value::String(d.to_string()),
        toml::Value::Array(a) => Value::Array(a.iter().map(toml_to_json).collect()),
        toml::Value::Table(t) => Value::Object(
            t.iter()
                .map(|(k, val)| (k.clone(), toml_to_json(val)))
                .collect(),
        ),
    }
}

/// Hostname extraction (equivalent to `urllib.parse.urlparse(...).hostname`: lowercase, strip userinfo/port).
fn url_host(url: &str) -> String {
    let after = url.split("://").nth(1).unwrap_or("");
    let authority = after.split(['/', '?', '#']).next().unwrap_or("");
    let host_port = authority.rsplit('@').next().unwrap_or(authority);
    if let Some(rest) = host_port.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_lowercase();
    }
    host_port.split(':').next().unwrap_or("").to_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    /// Create a minimal fixture of "system config + one agent", returns (tmp, ctx, sid).
    fn fixture(tag: &str) -> (TempDir, Ctx, String) {
        let tmp = TempDir::new(tag);
        let aimail_home = tmp.path().join("aimail");
        let user_home = tmp.path().join("home");
        let sid = "s1".to_string();
        let sid_dir = aimail_home.join("systems").join(&sid);
        std::fs::create_dir_all(&sid_dir).unwrap();
        std::fs::create_dir_all(&user_home).unwrap();
        std::fs::write(
            sid_dir.join(config::GATEWAY_CONFIG_NAME),
            br#"{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1",
                 "system_name":"s1","domain":"example.test","save_raw_snapshots":true}"#,
        )
        .unwrap();
        let ctx = Ctx {
            aimail_home,
            user_home,
        };
        (tmp, ctx, sid)
    }

    fn write_agent(ctx: &Ctx, sid: &str, dir: &str, body: &str) {
        let d = ctx.aimail_home.join("systems").join(sid).join(dir);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join(contract::binding_file()), body).unwrap();
    }

    fn details(c: &Check, level: &str, name: &str) -> Vec<String> {
        c.checks
            .iter()
            .filter(|r| r.level == level && r.check == name)
            .map(|r| r.detail.clone())
            .collect()
    }

    #[test]
    fn missing_gateway_config_is_one_record_and_returns() {
        let (_t, ctx, sid) = fixture("l0-missing");
        std::fs::remove_file(
            ctx.aimail_home
                .join("systems")
                .join(&sid)
                .join(config::GATEWAY_CONFIG_NAME),
        )
        .unwrap();
        let mut c = Check::new();
        l0_configs(&mut c, &sid, &ctx);
        assert_eq!(c.checks.len(), 1, "{:?}", c.checks);
        assert_eq!(c.checks[0].check, "gateway_json");
        assert!(!c.checks[0].pass);
        assert!(c.checks[0].detail.ends_with("missing/unreadable"));
    }

    #[test]
    fn complete_system_home_and_pointer_records() {
        let (_t, ctx, sid) = fixture("l0-ok");
        // system_home points to a real directory ⇒ pass; no pointer ⇒ pointer fails
        let sh = ctx.user_home.join(".hermes");
        std::fs::create_dir_all(&sh).unwrap();
        let path = ctx
            .aimail_home
            .join("systems")
            .join(&sid)
            .join(config::GATEWAY_CONFIG_NAME);
        let mut v: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        v["system_home"] = Value::String(sh.to_string_lossy().into_owned());
        std::fs::write(&path, serde_json::to_string_pretty(&v).unwrap()).unwrap();

        let mut c = Check::new();
        l0_configs(&mut c, &sid, &ctx);
        assert_eq!(
            details(&c, "config", "complete"),
            vec!["gateway_url + admin_key present"]
        );
        assert!(c.checks.iter().any(|r| r.check == "system_home" && r.pass));
        assert!(c.checks.iter().any(|r| r.check == "pointer" && !r.pass));
    }

    #[test]
    fn pointer_hit_is_reported_by_platform_name() {
        let (_t, ctx, sid) = fixture("l0-ptr");
        let hermes = ctx.user_home.join(".hermes");
        std::fs::create_dir_all(&hermes).unwrap();
        std::fs::write(
            hermes.join(contract::pointer_file()),
            format!(r#"{{"system_id":"{sid}","email":"a@x.test"}}"#),
        )
        .unwrap();
        let hits = pointer_platforms_for_sid(&ctx, &sid);
        assert_eq!(hits, vec!["hermes".to_string()]);
        let mut c = Check::new();
        l0_configs(&mut c, &sid, &ctx);
        assert_eq!(details(&c, "config", "pointer"), vec!["pointer: hermes"]);
    }

    #[test]
    fn binding_file_missing_fields_and_inconsistency_are_reported() {
        let (_t, ctx, sid) = fixture("l0-bind");
        write_agent(
            &ctx,
            &sid,
            "a_example.test",
            r#"{"email":"a@example.test","gateway_url":"http://other.test","domain":"example.test","system_id":"s1","system_name":"s1","api_key":"k","agent_id":"a"}"#,
        );
        let mut c = Check::new();
        agentmail_json(&mut c, &sid, &ctx);
        let complete = details(&c, "agent", "config-complete");
        assert_eq!(complete.len(), 1);
        assert!(
            complete[0].contains("missing fields: manager_address, webhook_url, webhook_secret"),
            "{complete:?}"
        );
        let consistency = details(&c, "agent", "config-consistency");
        assert!(
            consistency[0].contains("gateway_url differs from"),
            "{consistency:?}"
        );
    }

    #[test]
    fn binding_file_parse_error_is_reported_and_scan_continues() {
        let (_t, ctx, sid) = fixture("l0-parse");
        write_agent(&ctx, &sid, "broken", "{not json");
        write_agent(&ctx, &sid, "ok_dir", r#"{"email":"b@example.test"}"#);
        let mut c = Check::new();
        agentmail_json(&mut c, &sid, &ctx);
        assert_eq!(details(&c, "agent", "config-json").len(), 1);
        assert_eq!(
            details(&c, "agent", "config-complete").len(),
            1,
            "Bad file does not block subsequent"
        );
    }

    #[test]
    fn bridge_config_mode_is_checked_and_pull_entry_compared() {
        let (_t, ctx, sid) = fixture("l0-bridge");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(
            bridge.join("aimail_bridge.toml"),
            "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"OTHER\"\n",
        )
        .unwrap();
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        assert_eq!(details(&c, "bridge", "config-mode"), vec!["mode=pull"]);
        let entry = details(&c, "bridge", "pull-entry");
        assert_eq!(entry, vec!["pull entry present, admin_key MISMATCH"]);
    }

    #[test]
    fn bridge_invalid_mode_stops_before_pull_checks() {
        let (_t, ctx, sid) = fixture("l0-bridge-bad");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(bridge.join("aimail_bridge.toml"), "mode = \"weird\"\n").unwrap();
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        assert_eq!(
            details(&c, "bridge", "config-mode"),
            vec!["invalid mode: 'weird'"]
        );
        assert!(details(&c, "bridge", "pull-entry").is_empty());
    }

    #[test]
    fn missing_route_entry_marks_pull_delivery_dead() {
        let (_t, ctx, sid) = fixture("l0-routes");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(
            bridge.join("aimail_bridge.toml"),
            "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"k\"\n",
        )
        .unwrap();
        std::fs::write(
            bridge.join("aimail_routes.toml"),
            "other@example.test = \"http://127.0.0.1:1/x\"\n",
        )
        .unwrap();
        write_agent(
            &ctx,
            &sid,
            "a_example.test",
            r#"{"email":"a@example.test","webhook_url":"http://127.0.0.1:1/hook","agent_id":"a"}"#,
        );
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        let entry = details(&c, "bridge", "routes-entry");
        assert_eq!(
            entry,
            vec!["a: no route for a@example.test (pull delivery dead)"]
        );
    }

    #[test]
    fn dead_local_route_target_fails_without_auto_edit_hint() {
        let (_t, ctx, sid) = fixture("l0-dead-target");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(
            bridge.join("aimail_bridge.toml"),
            "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"k\"\n",
        )
        .unwrap();
        // 127.0.0.1:1 will definitely not connect, and is a local address ⇒ goes to FAIL branch (not remote WARN)
        std::fs::write(
            bridge.join("aimail_routes.toml"),
            "a@example.test = \"http://127.0.0.1:1/x\"\n",
        )
        .unwrap();
        write_agent(
            &ctx,
            &sid,
            "a_example.test",
            r#"{"email":"a@example.test","webhook_url":"http://127.0.0.1:1/hook","agent_id":"a"}"#,
        );
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        let target = details(&c, "bridge", "routes-target");
        assert_eq!(target, vec!["a: route target dead: http://127.0.0.1:1/x"]);
        let rec = c
            .checks
            .iter()
            .find(|r| r.check == "routes-target")
            .expect("record");
        assert_eq!(
            rec.fix,
            "Check the agent platform is running; do NOT auto-edit routes"
        );
    }

    #[test]
    fn url_host_matches_urlparse_hostname_semantics() {
        assert_eq!(url_host("http://127.0.0.1:8080/x"), "127.0.0.1");
        assert_eq!(url_host("http://LocalHost/X"), "localhost");
        assert_eq!(url_host("http://user:pw@host.test:9/x"), "host.test");
        assert_eq!(url_host("http://[::1]:8080/x"), "::1");
        assert_eq!(url_host("not-a-url"), "");
    }

    // ── webhook_plan_drift (plan drift, offline single source of truth)────────────────────────────

    /// Create a gateway config with `webhook_host` tri-state (gateway_url given by param).
    fn gw_cfg_with_webhook(tag: &str, gateway_url: &str, webhook: Option<&str>) -> (TempDir, Ctx) {
        let tmp = TempDir::new(tag);
        let aimail_home = tmp.path().join("aimail");
        let user_home = tmp.path().join("home");
        std::fs::create_dir_all(aimail_home.join("systems").join("s1")).unwrap();
        std::fs::create_dir_all(&user_home).unwrap();
        let mut v = serde_json::json!({
            "gateway_url": gateway_url,
            "admin_key": "k",
            "system_id": "s1",
            "system_name": "s1",
            "domain": "example.test"
        });
        if let Some(w) = webhook {
            v["webhook_host"] = Value::String(w.to_string());
        }
        std::fs::write(
            aimail_home
                .join("systems")
                .join("s1")
                .join(config::GATEWAY_CONFIG_NAME),
            serde_json::to_string_pretty(&v).unwrap(),
        )
        .unwrap();
        (
            tmp,
            Ctx {
                aimail_home,
                user_home,
            },
        )
    }

    /// Gate `bridge_deployable` (BR) two empty roots: `AIMAIL_HOME` (no bridge binary) +
    /// `AIMAIL_PROG_DIR` (no bridge zip) ⇒ BR=false. Process env is only modified within this test
    /// (gate `--test-threads=1`, same style as existing set_var tests in repair.rs).
    fn empty_roots(tag: &str) -> (TempDir, TempDir) {
        let ah = TempDir::new(&format!("{tag}-ah"));
        let pd = TempDir::new(&format!("{tag}-pd"));
        (ah, pd)
    }

    #[test]
    fn drift_check_emits_record_when_s2_empty_and_gateway_local() {
        // 127.0.0.1 = local GW (L true, offline decidable) + S2 empty string ⇒ decision function will only judge S3 ⇒ drift.
        let _g = crate::core::testutil::ENV_LOCK.lock().unwrap();
        let (ah, pd) = empty_roots("drift-local");
        std::env::set_var("AIMAIL_HOME", ah.path());
        std::env::set_var("AIMAIL_PROG_DIR", pd.path());
        let (_t, ctx) = gw_cfg_with_webhook("drift-local-cfg", "http://127.0.0.1:8080", Some(""));
        let mut c = Check::new();
        webhook_plan_drift(&mut c, "s1", &ctx);
        std::env::remove_var("AIMAIL_HOME");
        std::env::remove_var("AIMAIL_PROG_DIR");
        let rec = c
            .checks
            .iter()
            .find(|r| r.check == "webhook-plan-drift")
            .expect("drift record");
        assert!(!rec.pass);
        assert!(rec.detail.contains("direct-push"), "{}", rec.detail);
        assert!(rec.fix.contains("aimail install"), "{}", rec.fix);
    }

    #[test]
    fn drift_check_silent_when_no_contradiction() {
        // S3 key absent ⇒ offline unfalsifiable ⇒ silent (no record).
        let _g = crate::core::testutil::ENV_LOCK.lock().unwrap();
        let (ah, pd) = empty_roots("drift-absent");
        std::env::set_var("AIMAIL_HOME", ah.path());
        std::env::set_var("AIMAIL_PROG_DIR", pd.path());
        let (_t, ctx) = gw_cfg_with_webhook("drift-absent-cfg", "http://10.1.2.3:8080", None);
        let mut c = Check::new();
        webhook_plan_drift(&mut c, "s1", &ctx);
        std::env::remove_var("AIMAIL_HOME");
        std::env::remove_var("AIMAIL_PROG_DIR");
        assert!(
            c.checks.iter().all(|r| r.check != "webhook-plan-drift"),
            "S3 absence should not emit drift record: {:?}",
            c.checks
        );
    }
}
