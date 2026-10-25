//! L3/L4 five-platform adaptation surface — replica of `cli/check_status.py:185-799`.
//!
//! Four things per platform: `detect` / `list_agents` / `check_config`(L3) / `check_hook`(L4).
//! `deerflow` is `None` in Python's `PLATFORMS` (host is usually remote, L3/L4 handled by host-side SDK
//! reconcile) ⇒ here we also **do not provide an adapter**, relying on [`run_l3_l4`] to go through the registry
//! `agent_check_remote` prompt branch.
//!
//! Platform knowledge like ports/paths/names has two sources: **host modules** (`hosts/`, exported via `platform spec --json`) and **contracts**
//! (contract/aimail-contract.json). This module only uses these two, does not duplicate literals
//! (hermes hook route names and inbound paths are taken from the contract; pi is port-agnostic).

use crate::core::check::Check;
use crate::core::{config, contract, http};
use crate::hosts::Host;
use serde_json::Value;
use std::path::{Path, PathBuf};

/// Adapter runtime context: three roots + two sid semantics (copied from Python's two resolution chains).
#[derive(Debug, Clone, Copy)]
pub struct Ctx<'a> {
    pub user_home: &'a Path,
    /// `AGENT_HOME` (env / `--agent-home`); only Hermes uses it for location.
    pub agent_home: &'a Path,
    pub systems_dir: &'a Path,
    /// Resolved platform_sid (anchor in `main()`).
    pub sid: &'a str,
    /// Return value of `_resolve_system_id()`: argv `--system-id` > `AGENT_HOME/pointer` > env.
    pub resolve_sid: &'a str,
}

/// Platform traversal order of `_detect_default_sid` — copied from Python `PLATFORMS` dict **declaration order**
/// (`check_status.py:768-794`: hermes → openclaw → dsh → pi; deerflow's value is `None`
/// will be skipped). Note that it is **different** from the registry `order` (pi/dsh/hermes/openclaw/deerflow),
/// when multiple platforms hit simultaneously, "the first one with a pointer" wins ⇒ order is semantics, cannot be changed to registry order.
pub const DEFAULT_SID_ORDER: &[&str] = &["hermes", "openclaw", "dsh", "pi"];

/// `_resolve_system_id`: argv explicit > `AGENT_HOME/pointer` > env `SYSTEM_ID`.
pub fn resolve_system_id(
    agent_home: &Path,
    argv_sid: Option<&str>,
    env_sid: Option<&str>,
) -> String {
    if let Some(s) = argv_sid {
        if !s.is_empty() {
            return s.to_string();
        }
    }
    let ptr = agent_home.join(contract::pointer_file());
    if ptr.is_file() {
        if let Ok(text) = std::fs::read_to_string(&ptr) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                if let Some(s) = v.get("system_id").and_then(Value::as_str) {
                    if !s.is_empty() {
                        return s.to_string();
                    }
                }
            }
        }
    }
    env_sid.unwrap_or("").to_string()
}

/// `_detect_agent_type`: registry `detect` (dir_name + markers) per-platform determination.
///
/// **Not** the same algorithm as [`crate::hosts::identity::detect_platform_from_home`]: here `dir_name`
/// defaults to falling back to `home_dir` (hermes thus requires the directory to actually be named `.hermes`), while L2r's is
/// directory name optional. Both are copied as-is, not merged.
pub fn detect_agent_type(
    user_home: &Path,
    root: Option<&Path>,
    agent_home_explicit: bool,
) -> String {
    crate::hosts::identity::detect_agent_type(user_home, root, agent_home_explicit).to_string()
}

/// An agent to be checked (fields used differently per platform, defaults left empty — same shape as Python's dict).
#[derive(Debug, Clone, Default)]
pub struct Agent {
    pub name: String,
    pub email: String,
    pub profile_dir: Option<PathBuf>,
    pub agent_dir: Option<PathBuf>,
    pub session_id: String,
    pub preset: String,
    /// dsh/pi: the binding file itself is the config
    pub binding: Option<PathBuf>,
    /// Host config file (hermes's config.yaml / openclaw's openclaw.json)
    pub host_config: Option<PathBuf>,
}

// ── Small utilities ─────────────────────────────────────────────────────

fn read_json(path: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn load_yaml(path: &Path) -> Option<yaml_rust2::Yaml> {
    let text = std::fs::read_to_string(path).ok()?;
    yaml_rust2::YamlLoader::load_from_str(&text)
        .ok()?
        .into_iter()
        .next()
}

/// `yaml.get("a").get("b")` chain (non-mapping ⇒ None, equivalent to Python's `or {}` fallback).
fn yaml_get<'a>(node: &'a yaml_rust2::Yaml, key: &str) -> Option<&'a yaml_rust2::Yaml> {
    node.as_hash()?
        .get(&yaml_rust2::Yaml::String(key.to_string()))
}

/// List contains value (Python's `x in tools`: list compares elements, map compares keys).
fn yaml_contains(node: &yaml_rust2::Yaml, token: &str) -> bool {
    match node {
        yaml_rust2::Yaml::Array(a) => a.iter().any(|e| e.as_str() == Some(token)),
        yaml_rust2::Yaml::Hash(h) => h.keys().any(|k| k.as_str() == Some(token)),
        yaml_rust2::Yaml::String(s) => s == token,
        _ => false,
    }
}

fn binding_for(ctx: &Ctx, email: &str) -> Option<PathBuf> {
    if ctx.resolve_sid.is_empty() || email.is_empty() {
        return None;
    }
    Some(
        ctx.systems_dir
            .join(ctx.resolve_sid)
            .join(config::clean_agent_dir_name(email))
            .join(contract::binding_file()),
    )
}

fn api_key_from(binding: &Option<PathBuf>) -> String {
    binding
        .as_ref()
        .filter(|p| p.is_file())
        .and_then(|p| read_json(p))
        .and_then(|v| v.get("api_key").and_then(Value::as_str).map(str::to_string))
        .unwrap_or_default()
}

/// Copy like `name_apikey` / `register` shared by three platforms.
fn name_apikey_detail(name: &str, email: &str, api_key: &str) -> String {
    format!(
        "{name}: {}{}",
        if email.is_empty() { "no email" } else { email },
        if api_key.is_empty() {
            ", api_key MISSING"
        } else {
            ", api_key ✓"
        }
    )
}

// ── Hermes ─────────────────────────────────────────────────────

pub fn hermes_list_agents(ctx: &Ctx) -> Vec<Agent> {
    let mut agents = Vec::new();
    let ptr = ctx.agent_home.join(contract::pointer_file());
    let is_profile_dir = ptr.is_file() && !ctx.agent_home.join("profiles").is_dir();

    if is_profile_dir {
        agents.push(Agent {
            name: ctx
                .agent_home
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
            email: read_json(&ptr)
                .and_then(|v| v.get("email").and_then(Value::as_str).map(str::to_string))
                .unwrap_or_default(),
            profile_dir: Some(ctx.agent_home.to_path_buf()),
            host_config: Some(ctx.agent_home.join("config.yaml")),
            ..Default::default()
        });
        return agents;
    }

    // Root layout: default root profile
    if ptr.is_file() {
        if let Some(v) = read_json(&ptr) {
            agents.push(Agent {
                name: "default".to_string(),
                email: v
                    .get("email")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                profile_dir: Some(ctx.agent_home.to_path_buf()),
                host_config: Some(ctx.agent_home.join("config.yaml")),
                ..Default::default()
            });
        }
    }

    let profiles = ctx.agent_home.join("profiles");
    if profiles.is_dir() {
        let mut dirs: Vec<PathBuf> = std::fs::read_dir(&profiles)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        dirs.sort();
        for pdir in dirs {
            let pptr = pdir.join(contract::pointer_file());
            let mut email = read_json(&pptr)
                .and_then(|v| v.get("email").and_then(Value::as_str).map(str::to_string))
                .unwrap_or_default();
            if email.is_empty() {
                // No pointer: check if config.yaml's platform_toolsets has the aimail tool marker
                let cfg_p = pdir.join("config.yaml");
                if cfg_p.exists() {
                    if let Some(doc) = load_yaml(&cfg_p) {
                        if let Some(pt) = yaml_get(&doc, "platform_toolsets") {
                            for seg in ["webhook", "cli"] {
                                if let Some(tools) = yaml_get(pt, seg) {
                                    if yaml_contains(tools, contract::agent_toolset_name()) {
                                        email = "?".to_string();
                                        break;
                                    }
                                }
                            }
                        }
                    }
                }
                if email.is_empty() {
                    continue; // Irrelevant profile, skip (avoid noise)
                }
            }
            agents.push(Agent {
                name: pdir
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                email,
                profile_dir: Some(pdir.clone()),
                host_config: Some(pdir.join("config.yaml")),
                ..Default::default()
            });
        }
    }
    agents
}

pub fn hermes_check_config(c: &mut Check, ctx: &Ctx, agent: &Agent) {
    let binding = binding_for(ctx, &agent.email);
    let api_key = api_key_from(&binding);
    let ok = !agent.email.is_empty() && !api_key.is_empty();
    c.add(
        "agent",
        "name_apikey",
        ok,
        &name_apikey_detail(&agent.name, &agent.email, &api_key),
        "Run: python -m aimail.install install --type hermes (SDK register)",
    );

    let cfg = agent.host_config.as_ref();
    let mut wh_ok = false;
    if let Some(p) = cfg.filter(|p| p.exists()) {
        if let Some(doc) = load_yaml(p) {
            if let Some(wh) = yaml_get(&doc, "platforms").and_then(|p| yaml_get(p, "webhook")) {
                let enabled = yaml_get(wh, "enabled")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                let secret = yaml_get(wh, "extra")
                    .and_then(|e| yaml_get(e, "secret"))
                    .and_then(|s| s.as_str())
                    .map(|s| !s.is_empty())
                    .unwrap_or(false);
                wh_ok = enabled && secret;
            }
        }
    }
    c.add(
        "agent",
        "webhook",
        wh_ok,
        &format!(
            "{}: webhook {}",
            agent.name,
            if wh_ok {
                "enabled + secret ✓"
            } else {
                "MISSING (platforms.webhook)"
            }
        ),
        "Run: python -m aimail.install install --type hermes (SDK config completion)",
    );

    let skill_ok = agent
        .profile_dir
        .as_ref()
        .map(|pd| {
            pd.join("skills")
                .join(contract::agent_skill_name())
                .is_dir()
        })
        .unwrap_or(false);
    c.add(
        "agent",
        "skill",
        skill_ok,
        &format!(
            "{}: skills/{} {}",
            agent.name,
            contract::agent_skill_name(),
            if skill_ok { "✓" } else { "MISSING" }
        ),
        "Re-run: python -m aimail.install install --type hermes --home <hermes-root>",
    );

    let mut ts_ok = false;
    if let Some(p) = cfg.filter(|p| p.exists()) {
        if let Some(doc) = load_yaml(p) {
            if let Some(pt) = yaml_get(&doc, "platform_toolsets") {
                for seg in ["webhook", "cli"] {
                    if let Some(tools) = yaml_get(pt, seg) {
                        if yaml_contains(tools, contract::agent_toolset_name()) {
                            ts_ok = true;
                            break;
                        }
                    }
                }
            }
        }
    }
    c.add(
        "agent",
        "toolset",
        ts_ok,
        &format!(
            "{}: platform_toolsets {}",
            agent.name,
            if ts_ok {
                format!("Contains {} ✓", contract::agent_toolset_name())
            } else {
                "MISSING".to_string()
            }
        ),
        "Run: python -m aimail.install install --type hermes (SDK webhook config)",
    );

    let mut route_ok = false;
    if let Some(pd) = agent.profile_dir.as_ref() {
        let subs = pd.join("webhook_subscriptions.json");
        if subs.exists() {
            if let Some(v) = read_json(&subs) {
                if let Some(map) = v.as_object() {
                    route_ok = map.keys().any(|k| k.to_lowercase().contains("aimail"));
                }
            }
        }
    }
    let reg_ok = !api_key.is_empty() && route_ok;
    c.add(
        "agent",
        "register",
        reg_ok,
        &format!(
            "{}: {}",
            agent.name,
            if reg_ok {
                "registered ✓"
            } else {
                "api_key/route incomplete"
            }
        ),
        "Run: python -m aimail.install install --type hermes",
    );
}

pub fn hermes_check_hook(c: &mut Check, _ctx: &Ctx, agent: &Agent) {
    let mut port = 8646u64;
    if let Some(p) = agent.host_config.as_ref().filter(|p| p.exists()) {
        if let Some(doc) = load_yaml(p) {
            if let Some(wh) = yaml_get(&doc, "platforms").and_then(|p| yaml_get(p, "webhook")) {
                let from_extra = yaml_get(wh, "extra")
                    .and_then(|e| yaml_get(e, "port"))
                    .and_then(|v| v.as_i64());
                let direct = yaml_get(wh, "port").and_then(|v| v.as_i64());
                if let Some(v) = direct.or(from_extra) {
                    if v > 0 {
                        port = v as u64;
                    }
                }
            }
        }
    }
    let route_name = contract::hermes_route_name();
    let url = format!("http://127.0.0.1:{port}{}", contract::hermes_inbound_path());
    let payload = serde_json::to_vec(&serde_json::json!({
        "message": "status-check",
        "from": "check_status@localhost",
        "subject": "aimail connectivity probe",
    }))
    .unwrap_or_default();
    match http::raw_req(&url, Some(&payload), Some("application/json"), 5) {
        Ok((status, _)) => {
            if status == 200 {
                c.add(
                    "agent",
                    "hook",
                    true,
                    &format!("POST {route_name} → HTTP {status}"),
                    "",
                );
            } else if status == 401 || status == 403 {
                c.add(
                    "agent",
                    "hook",
                    true,
                    &format!("POST {route_name} → {status} (route active, HMAC required)"),
                    "",
                );
            } else if status == 404 {
                c.add(
                    "agent",
                    "hook",
                    false,
                    &format!("POST {route_name} → 404 route missing"),
                    "Run: aimail install (re-run registration chain) or python -m aimail.install",
                );
            } else {
                c.add(
                    "agent",
                    "hook",
                    false,
                    &format!("POST {route_name} → HTTP {status}"),
                    "",
                );
            }
        }
        Err(e) => c.add(
            "agent",
            "hook",
            false,
            &format!("Cannot reach {url}: {e}"),
            "Start Hermes gateway with --accept-hooks",
        ),
    }
}

// ── OpenClaw ───────────────────────────────────────────────────

pub fn openclaw_list_agents(ctx: &Ctx) -> Vec<Agent> {
    let mut agents = Vec::new();
    let oc_home = ctx.user_home.join(".openclaw");
    let agents_dir = oc_home.join("agents");
    let mut sid = read_json(&oc_home.join(contract::pointer_file()))
        .and_then(|v| {
            v.get("system_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default();
    if sid.is_empty() {
        sid = ctx.resolve_sid.to_string();
    }
    if agents_dir.is_dir() {
        let mut dirs: Vec<PathBuf> = std::fs::read_dir(&agents_dir)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        dirs.sort();
        for adir in dirs {
            let mut email = String::new();
            let sysdir = ctx.systems_dir.join(&sid);
            if sysdir.is_dir() {
                let mut subs: Vec<PathBuf> = std::fs::read_dir(&sysdir)
                    .into_iter()
                    .flatten()
                    .flatten()
                    .map(|e| e.path())
                    .filter(|p| p.is_dir())
                    .collect();
                subs.sort();
                for sub in subs {
                    let aj = sub.join(contract::binding_file());
                    if !aj.is_file() {
                        continue;
                    }
                    if let Some(d) = read_json(&aj) {
                        if d.get("agent_id").and_then(Value::as_str)
                            == Some(
                                adir.file_name()
                                    .map(|s| s.to_string_lossy())
                                    .unwrap_or_default()
                                    .as_ref(),
                            )
                        {
                            email = d
                                .get("email")
                                .and_then(Value::as_str)
                                .unwrap_or("")
                                .to_string();
                            break;
                        }
                    }
                }
            }
            agents.push(Agent {
                name: adir
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                email,
                agent_dir: Some(adir),
                host_config: Some(oc_home.join("openclaw.json")),
                ..Default::default()
            });
        }
    }
    agents
}

pub fn openclaw_check_config(c: &mut Check, ctx: &Ctx, agent: &Agent) {
    // sid resolution chain (argv explicit > OpenClaw pointer > default) — copied from Python's three segments
    let mut sid = ctx.resolve_sid.to_string();
    let env_sid = std::env::var("SYSTEM_ID").unwrap_or_default();
    if sid.is_empty() || sid == env_sid {
        let oc_ptr = ctx
            .user_home
            .join(".openclaw")
            .join(contract::pointer_file());
        if oc_ptr.is_file() {
            if let Some(v) = read_json(&oc_ptr) {
                if let Some(s) = v.get("system_id").and_then(Value::as_str) {
                    sid = s.to_string();
                }
            }
        }
    }
    if sid.is_empty() {
        sid = ctx.resolve_sid.to_string();
    }
    let binding = if sid.is_empty() || agent.email.is_empty() {
        None
    } else {
        Some(
            ctx.systems_dir
                .join(&sid)
                .join(config::clean_agent_dir_name(&agent.email))
                .join(contract::binding_file()),
        )
    };
    let api_key = api_key_from(&binding);
    let ok = !agent.email.is_empty() && !api_key.is_empty();
    c.add(
        "agent",
        "name_apikey",
        ok,
        &name_apikey_detail(&agent.name, &agent.email, &api_key),
        "Run `openclaw aimail register` (the agent-aimail plugin)",
    );

    let wh_ok = binding
        .as_ref()
        .filter(|p| p.is_file())
        .and_then(|p| read_json(p))
        .and_then(|v| v.get("webhook_secret").cloned())
        .map(|v| match v {
            Value::String(s) => !s.is_empty(),
            Value::Null => false,
            _ => true,
        })
        .unwrap_or(false);
    c.add(
        "agent",
        "webhook",
        wh_ok,
        &format!(
            "{}: webhook_secret {}",
            agent.name,
            if wh_ok { "✓" } else { "MISSING" }
        ),
        "Re-run `openclaw aimail register`(persists webhook_secret)",
    );

    let skill_ok = ctx
        .user_home
        .join(".openclaw")
        .join("skills")
        .join(contract::agent_skill_name())
        .is_dir();
    c.add(
        "agent",
        "skill",
        skill_ok,
        &format!(
            "{}: skills/{} {}",
            agent.name,
            contract::agent_skill_name(),
            if skill_ok { "✓" } else { "MISSING" }
        ),
        "Reinstall the agent-aimail plugin (skill ships with the plugin)",
    );

    let mut ts_ok = false;
    if let Some(p) = agent.host_config.as_ref().filter(|p| p.exists()) {
        if let Some(oc) = read_json(p) {
            let plugins = oc.get("plugins");
            let entries = plugins
                .and_then(|p| p.get("entries"))
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let allow: Vec<String> = plugins
                .and_then(|p| p.get("allow"))
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default();
            let installed_root = ctx.user_home.join(".openclaw").join("npm").join("projects");
            let installed = std::fs::read_dir(&installed_root)
                .into_iter()
                .flatten()
                .flatten()
                .any(|e| {
                    e.file_name()
                        .to_string_lossy()
                        .starts_with(&format!("{}-aimail", "openclaw"))
                });
            ts_ok = installed
                && (entries.contains_key("agent-aimail")
                    || allow.iter().any(|a| a == "agent-aimail")
                    || entries.is_empty());
        }
    }
    c.add(
        "agent",
        "toolset",
        ts_ok,
        &format!(
            "{}: agent-aimail plugin {}",
            agent.name,
            if ts_ok { "✓" } else { "MISSING" }
        ),
        "openclaw plugins install npm-pack:<agent-aimail.tgz>; restart gateway",
    );

    let reg_ok = !api_key.is_empty();
    c.add(
        "agent",
        "register",
        reg_ok,
        &format!(
            "{}: {}",
            agent.name,
            if reg_ok {
                "registered ✓"
            } else {
                "api_key MISSING"
            }
        ),
        "Run `openclaw aimail register` (the agent-aimail plugin)",
    );
}

pub fn openclaw_check_hook(c: &mut Check, _ctx: &Ctx, agent: &Agent) {
    let mut port = 18789u64;
    if let Some(p) = agent.host_config.as_ref().filter(|p| p.exists()) {
        if let Some(oc) = read_json(p) {
            if let Some(v) = oc
                .get("gateway")
                .and_then(|g| g.get("port"))
                .and_then(Value::as_i64)
            {
                if v > 0 {
                    port = v as u64;
                }
            }
        }
    }
    let path = contract::inbound_path();
    let url = format!("http://127.0.0.1:{port}{path}");
    let payload = serde_json::to_vec(&serde_json::json!({
        "to": ["probe@invalid"],
        "body": "status-check",
    }))
    .unwrap_or_default();
    match http::raw_req(&url, Some(&payload), Some("application/json"), 5) {
        Ok((status, body)) => {
            if status == 200 {
                let head: String = body.chars().take(80).collect();
                c.add(
                    "agent",
                    "hook",
                    true,
                    &format!("POST {path} → HTTP {status} {head}"),
                    "",
                );
            } else if status == 404 {
                c.add(
                    "agent",
                    "hook",
                    false,
                    &format!("POST {path} → 404 (route not registered)"),
                    "Reinstall: openclaw plugins install npm-pack:<agent-aimail.tgz>; restart gateway",
                );
            } else if status == 400 || status == 401 || status == 405 {
                c.add(
                    "agent",
                    "hook",
                    true,
                    &format!("POST {path} → {status} (plugin route active)"),
                    "",
                );
            } else {
                c.add(
                    "agent",
                    "hook",
                    false,
                    &format!("POST {path} → HTTP {status}"),
                    "",
                );
            }
        }
        Err(e) => c.add(
            "agent",
            "hook",
            false,
            &format!("Cannot reach {url}: {e}"),
            &format!("Is the OpenClaw gateway running on :{port} with the agent-aimail plugin installed?"),
        ),
    }
}

// ── dsh ────────────────────────────────────────────────────────

pub fn dsh_list_agents(ctx: &Ctx) -> Vec<Agent> {
    let mut agents = Vec::new();
    let sid = ctx.resolve_sid.to_string();
    if sid.is_empty() {
        return agents;
    }
    let sysdir = ctx.systems_dir.join(&sid);
    if !sysdir.is_dir() {
        return agents;
    }
    let mut subs: Vec<PathBuf> = std::fs::read_dir(&sysdir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    for sub in subs {
        let aj = sub.join(contract::binding_file());
        if !aj.is_file() {
            continue;
        }
        let Some(d) = read_json(&aj) else { continue };
        let email = d
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if email.is_empty() {
            continue;
        }
        let session_id = d
            .get("session_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let local = email.split('@').next().unwrap_or("").to_string();
        let raw_name = if session_id.is_empty() {
            local
        } else {
            session_id.clone()
        };
        let name: String = raw_name.chars().take(8).collect();
        agents.push(Agent {
            name,
            email,
            session_id,
            preset: d
                .get("preset")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            binding: Some(aj),
            ..Default::default()
        });
    }
    agents
}

pub fn dsh_check_config(c: &mut Check, _ctx: &Ctx, agent: &Agent) {
    let d = agent
        .binding
        .as_ref()
        .filter(|p| p.is_file())
        .and_then(|p| read_json(p));
    let get = |k: &str| -> String {
        d.as_ref()
            .and_then(|v| v.get(k))
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_default()
    };
    let api_key = get("api_key");
    let wh_url = get("webhook_url");
    let wh_secret = get("webhook_secret");
    let preset = get("preset");
    let ok = !agent.email.is_empty() && !api_key.is_empty();
    c.add(
        "agent",
        "name_apikey",
        ok,
        &name_apikey_detail(&agent.name, &agent.email, &api_key),
        "Run: aimail address -s <sid> -a agent (or auto-bind inside a dsh session)",
    );

    let wh_ok = !wh_url.is_empty() && !wh_secret.is_empty();
    c.add(
        "agent",
        "webhook",
        wh_ok,
        &format!(
            "webhook_url={}{}",
            if wh_url.is_empty() {
                "(missing)"
            } else {
                wh_url.as_str()
            },
            if wh_secret.is_empty() {
                ", secret MISSING"
            } else {
                ", secret ✓"
            }
        ),
        "registration chain persisted webhook_url + webhook_secret (aimail address / agent-aimail register-cli)",
    );

    // session_id is transient (one disposable session per inbound mail since 0.1.14) ⇒ criterion only recognizes preset
    let sess_ok = !preset.is_empty();
    c.add(
        "agent",
        "session",
        sess_ok,
        &format!(
            "session_id={}, preset={}",
            if agent.session_id.is_empty() {
                "(transient: one disposable session per inbound)"
            } else {
                agent.session_id.as_str()
            },
            if preset.is_empty() {
                "(missing)"
            } else {
                preset.as_str()
            }
        ),
        &format!(
            "{} persists preset; session_id is temporarily bound by the plugin on each inbound round",
            contract::binding_file()
        ),
    );
}

pub fn dsh_check_hook(c: &mut Check, _ctx: &Ctx, agent: &Agent) {
    let wh_url = agent
        .binding
        .as_ref()
        .filter(|p| p.is_file())
        .and_then(|p| read_json(p))
        .and_then(|v| {
            v.get("webhook_url")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default();
    if wh_url.is_empty() {
        c.add(
            "agent",
            "hook",
            false,
            "webhook_url missing",
            "Registration chain persists webhook_url (mail-inbound endpoint)",
        );
        return;
    }
    post_json_probe(
        c,
        &wh_url,
        "Start dsh mail-inbound plugin on the endpoint port",
    );
}

// ── pi ─────────────────────────────────────────────────────────

pub fn pi_list_agents(ctx: &Ctx) -> Vec<Agent> {
    // pi's characteristic detection point = **platform pointer** (POINTER_FILE under ~/.pi), not the bound agent_id.
    // agent_id is the agent "name" (owner decision 2026-10-16) which can be renamed via CLI command, not a platform identity characteristic,
    // use it as the "this is the pi adapter" detection point; renaming it invalidates it. The pointer is a platform-specific landing spot (of install
    // register-cli and auto-bind's identity both write to it, uninstall rm_pointer deletes it), its email
    // i.e., the address of the pi service. systems/{sid}/ is shared by dsh/pi/openclaw ⇒ no longer "scan shared directory and filter by name"
    // (the old is_pi problem). Pointer email → binding_for(email) = standard email key binding path,
    // consistent with pi_check_config / pi_check_hook.
    let mut agents = Vec::new();
    let ptr = ctx.user_home.join(".pi").join(contract::pointer_file());
    let pv = read_json(&ptr);
    let email = pv
        .as_ref()
        .and_then(|v| v.get("email").and_then(Value::as_str))
        .unwrap_or("")
        .to_string();
    // The pointer's system scope must match the current check context (empty = unmarked, tolerated).
    let ptr_sid = pv
        .as_ref()
        .and_then(|v| v.get("system_id").and_then(Value::as_str))
        .unwrap_or("")
        .to_string();
    if email.is_empty() || (!ptr_sid.is_empty() && ptr_sid != ctx.resolve_sid) {
        return agents;
    }
    let email_binding = binding_for(ctx, &email);
    let binding = email_binding
        .filter(|p| p.is_file())
        .unwrap_or_else(|| ptr.clone());
    agents.push(Agent {
        name: "pi".to_string(), // Display label (platform row name), not a detection key
        email,
        binding: Some(binding),
        ..Default::default()
    });
    agents
}

pub fn pi_check_config(c: &mut Check, ctx: &Ctx, agent: &Agent) {
    let binding = binding_for(ctx, &agent.email);
    let api_key = api_key_from(&binding);
    let ok = !agent.email.is_empty() && !api_key.is_empty();
    c.add(
        "agent",
        "name_apikey",
        ok,
        &name_apikey_detail(&agent.name, &agent.email, &api_key),
        "Re-run: aimail install --home ~/.pi (register pi agent)",
    );

    let ptr = ctx.user_home.join(".pi").join(contract::pointer_file());
    let ptr_ok = read_json(&ptr)
        .and_then(|v| {
            v.get("system_id")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .map(|s| s == ctx.resolve_sid)
        .unwrap_or(false)
        && ptr.is_file();
    c.add(
        "agent",
        "pointer",
        ptr_ok,
        if ptr_ok {
            ".pi pointer matches system"
        } else {
            ".pi pointer missing/mismatch"
        },
        "Re-run: aimail install --home ~/.pi",
    );
}

pub fn pi_check_hook(c: &mut Check, ctx: &Ctx, agent: &Agent) {
    let wh_url = agent
        .binding
        .as_ref()
        .filter(|p| p.is_file())
        .and_then(|p| read_json(p))
        .or_else(|| {
            binding_for(ctx, &agent.email)
                .filter(|p| p.is_file())
                .and_then(|p| read_json(&p))
        })
        .and_then(|v| {
            v.get("webhook_url")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .unwrap_or_default();
    if wh_url.is_empty() {
        c.add(
            "agent",
            "hook",
            false,
            &format!("no webhook_url ({})", contract::binding_file()),
            "Re-run: aimail install --home ~/.pi",
        );
        return;
    }
    let host = url_host(&wh_url);
    if host != "127.0.0.1" && host != "localhost" && host != "::1" {
        // Remote host (common for pi platform) not detectable locally ⇒ not counted as FAIL
        c.add(
            "agent",
            "hook",
            true,
            &format!("webhook remote host (not probeable locally): {wh_url}"),
            "",
        );
        return;
    }
    post_json_probe(c, &wh_url, "");
}

/// dsh/pi shared hook probe (POST `{}`, 200/401/403 = receiver active, 404 = not registered).
fn post_json_probe(c: &mut Check, wh_url: &str, fix: &str) {
    match http::raw_req(wh_url, Some(b"{}"), Some("application/json"), 5) {
        Ok((200, _)) => c.add(
            "agent",
            "hook",
            true,
            &format!("POST {wh_url} → HTTP 200"),
            "",
        ),
        Ok((code, _)) if code == 401 || code == 403 => c.add(
            "agent",
            "hook",
            true,
            &format!("POST {wh_url} → {code} (receiver active)"),
            "",
        ),
        Ok((404, _)) => c.add("agent", "hook", false, &format!("POST {wh_url} → 404"), fix),
        Ok((code, _)) => c.add(
            "agent",
            "hook",
            false,
            &format!("POST {wh_url} → HTTP {code}"),
            "",
        ),
        Err(e) => c.add(
            "agent",
            "hook",
            false,
            &format!("Cannot reach {wh_url}: {e}"),
            fix,
        ),
    }
}

fn url_host(url: &str) -> String {
    let after = url.split("://").nth(1).unwrap_or("");
    let authority = after.split(['/', '?', '#']).next().unwrap_or("");
    let host_port = authority.rsplit('@').next().unwrap_or(authority);
    if let Some(rest) = host_port.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_lowercase();
    }
    host_port.split(':').next().unwrap_or("").to_lowercase()
}

// ── L3/L4 drivers (`main()` :1570-1592)────────────────────────────

/// Run L3/L4 for a platform (`agent_type` determined by [`detect_agent_type`]).
///
/// Returns hint lines that need to be printed by the caller (Python side directly `print`s in these two branches,
/// Not in `Check` — keep the same shape, avoid mixing hints into JSON records).
pub fn run_l3_l4(c: &mut Check, ctx: &Ctx, agent_type: &str) -> Option<String> {
    let has_adapter = matches!(agent_type, "hermes" | "openclaw" | "dsh" | "pi");
    if has_adapter {
        let agents = match agent_type {
            "hermes" => hermes_list_agents(ctx),
            "openclaw" => openclaw_list_agents(ctx),
            "dsh" => dsh_list_agents(ctx),
            _ => pi_list_agents(ctx),
        };
        if agents.is_empty() {
            c.add(
                "agent",
                "discovery",
                false,
                &format!(
                    "no agents found for platform '{agent_type}' (system {})",
                    ctx.sid
                ),
                "Check the platform home dir / agents registry",
            );
        }
        for a in &agents {
            match agent_type {
                "hermes" => hermes_check_config(c, ctx, a),
                "openclaw" => openclaw_check_config(c, ctx, a),
                "dsh" => dsh_check_config(c, ctx, a),
                _ => pi_check_config(c, ctx, a),
            }
            match agent_type {
                "hermes" => hermes_check_hook(c, ctx, a),
                "openclaw" => openclaw_check_hook(c, ctx, a),
                "dsh" => dsh_check_hook(c, ctx, a),
                _ => pi_check_hook(c, ctx, a),
            }
        }
        return None;
    }

    let remote = crate::hosts::get(agent_type)
        .map(|h| h.agent_check_remote())
        .unwrap_or(false);
    if remote {
        return Some(format!(
            "⚠ {agent_type} platform: L3/L4 agent checks run via its SDK reconcile on the host"
        ));
    }
    Some(format!(
        "⚠ Unknown agent platform: {agent_type} — skipping agent checks"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::check::Check;
    use crate::core::testutil::TempDir;

    fn ctx_of<'a>(tmp: &'a TempDir, ah: &'a Path, systems: &'a Path, sid: &'a str) -> Ctx<'a> {
        Ctx {
            user_home: tmp.path(),
            agent_home: ah,
            systems_dir: systems,
            sid,
            resolve_sid: sid,
        }
    }

    #[test]
    fn resolve_system_id_prefers_argv_then_pointer_then_env() {
        let tmp = TempDir::new("adapters-sid");
        let ah = tmp.path().join(".hermes");
        std::fs::create_dir_all(&ah).unwrap();
        assert_eq!(resolve_system_id(&ah, Some("argv"), Some("env")), "argv");
        assert_eq!(resolve_system_id(&ah, None, Some("env")), "env");
        std::fs::write(ah.join(contract::pointer_file()), r#"{"system_id":"ptr"}"#).unwrap();
        assert_eq!(resolve_system_id(&ah, None, Some("env")), "ptr");
    }

    #[test]
    fn detect_agent_type_requires_registry_markers() {
        let tmp = TempDir::new("adapters-detect");
        let home = tmp.path();
        assert_eq!(detect_agent_type(home, None, false), "unknown");
        // hermes: dir_name defaults to falling back to home_dir(.hermes) + characteristic marker hermes-agent
        // (C-3: profiles are runtime artifacts, official install does not create them ⇒ not included in detect features)
        std::fs::create_dir_all(home.join(".hermes").join("hermes-agent")).unwrap();
        assert_eq!(
            detect_agent_type(home, None, false),
            "hermes",
            "hermes-agent single characteristic should be identified as hermes"
        );
        // Explicit --agent-home ⇒ direct hermes intent
        assert_eq!(detect_agent_type(home, None, true), "hermes");
    }

    #[test]
    fn dsh_agent_name_is_binding_derived_and_truncated() {
        let tmp = TempDir::new("adapters-dsh");
        let systems = tmp.path().join("aimail/systems");
        let dir = systems.join("s1").join("a_example.test");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join(contract::binding_file()),
            r#"{"email":"verylonglocal@example.test","preset":"p1"}"#,
        )
        .unwrap();
        let ah = tmp.path().join(".hermes");
        std::fs::create_dir_all(&ah).unwrap();
        let ctx = ctx_of(&tmp, &ah, &systems, "s1");
        let agents = dsh_list_agents(&ctx);
        assert_eq!(agents.len(), 1);
        assert_eq!(agents[0].name.chars().count(), 8);
        assert_eq!(agents[0].preset, "p1");

        let mut c = Check::new();
        dsh_check_config(&mut c, &ctx, &agents[0]);
        let session = c.checks.iter().find(|r| r.check == "session").unwrap();
        assert!(session.pass, "Pass if preset is non-empty: {session:?}");
        let apikey = c.checks.iter().find(|r| r.check == "name_apikey").unwrap();
        assert!(!apikey.pass, "no api_key ⇒ failure");
    }

    #[test]
    fn unknown_platform_yields_no_adapter_and_a_hint_line() {
        let tmp = TempDir::new("adapters-unknown");
        let ah = tmp.path().join(".hermes");
        std::fs::create_dir_all(&ah).unwrap();
        let systems = tmp.path().join("aimail/systems");
        let ctx = ctx_of(&tmp, &ah, &systems, "s1");
        let mut c = Check::new();
        let hint = run_l3_l4(&mut c, &ctx, "nope").expect("unknown should have hint line");
        assert!(hint.contains("Unknown agent platform"), "{hint}");
        assert!(
            c.checks.is_empty(),
            "Unknown platform does not produce records: {:?}",
            c.checks
        );

        // deerflow: registry agent_check_remote ⇒ another hint line (remote self-verification)
        let hint2 = run_l3_l4(&mut c, &ctx, "deerflow").expect("deerflow should have hint line");
        assert!(hint2.contains("SDK reconcile"), "{hint2}");
    }
}
