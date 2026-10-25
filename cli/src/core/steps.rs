//! Platform action sequence executor (Rust reimplementation of `cli/aimail:427-678`) — platform registry (typed constants)'
//! `install_steps` / `uninstall_steps` table-driven, **CLI has zero platform knowledge**.
//!
//! kind：`print` | `warn` | `spawn` | `sdk_install` | `register_default` | `register_all`
//! · `when` preconditions (path_exists / command_exists / sid_set / cfg_complete / runtime / runtime_not)
//! · `spawn`: `skip_if` (skip if probe says "already present") · `on_missing` (fail/skip) · `on_error` (warn/fail)
//!
//! Two **hard gates** are copied verbatim and must not be swallowed by exception fallbacks (F9 lesson): steps that require registration/whitelist writing (`sdk_install`
//! Default, `register_default`, `register_all`) check that manager is non-null before entering any try ⇒ if null, rc≠0.

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::core::{register, sdkcall, setup, style};

const PROBE_TIMEOUT: Duration = Duration::from_secs(30);
const SDK_INSTALL_TIMEOUT: Duration = Duration::from_secs(300);

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

/// `_tmpl`: `{k}` per-key replacement (all keys in ctx).
pub fn tmpl(text: &str, ctx: &Map<String, Value>) -> String {
    let mut out = text.to_string();
    for (k, v) in ctx {
        let s = match v {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        out = out.replace(&format!("{{{k}}}"), &s);
    }
    out
}

fn ctx_str(ctx: &Map<String, Value>, key: &str) -> String {
    ctx.get(key)
        .map(|v| match v {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        })
        .unwrap_or_default()
}

/// `_when_ok`: `when` condition evaluation.
pub fn when_ok(when: &Value, ctx: &Map<String, Value>) -> bool {
    let Some(obj) = when.as_object() else {
        return true;
    };
    for (key, val) in obj {
        let truthy = val.as_bool().unwrap_or(false);
        match key.as_str() {
            "path_exists" => {
                if !Path::new(&tmpl(val.as_str().unwrap_or(""), ctx)).exists() {
                    return false;
                }
            }
            "command_exists" => {
                let bin = tmpl(val.as_str().unwrap_or(""), ctx);
                if !command_in_path(&bin) {
                    return false;
                }
            }
            "sid_set" => {
                if !ctx_str(ctx, "sid").is_empty() != truthy {
                    return false;
                }
            }
            "cfg_complete" => {
                let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
                let complete = !setup::pget(&cfg, "gateway_url").is_empty()
                    && !setup::pget(&cfg, "admin_key").is_empty()
                    && !ctx_str(ctx, "sid").is_empty();
                if complete != truthy {
                    return false;
                }
            }
            "runtime" => {
                if ctx_str(ctx, "runtime") != val.as_str().unwrap_or("") {
                    return false;
                }
            }
            "runtime_not" if ctx_str(ctx, "runtime") == val.as_str().unwrap_or("") => return false,
            // Ignore other keys (same as Python: unknown when keys do not participate in the decision)
            _ => {}
        }
    }
    true
}

fn command_in_path(bin: &str) -> bool {
    if bin.is_empty() {
        return false;
    }
    if Path::new(bin).is_absolute() {
        return Path::new(bin).is_file();
    }
    let path = std::env::var("PATH").unwrap_or_default();
    let sep = if cfg!(windows) { ';' } else { ':' };
    for d in path.split(sep) {
        if d.is_empty() {
            continue;
        }
        // Windows: executables are extended by PATHEXT (npm global installs land as <name>.cmd,
        // bare name is_file is always false); unix has no extension.
        if cfg!(windows) {
            for ext in path_exts() {
                if Path::new(d).join(format!("{}{}", bin, ext)).is_file() {
                    return true;
                }
            }
        } else if Path::new(d).join(bin).is_file() {
            return true;
        }
    }
    false
}

/// Windows executable extension list (PATHEXT environment variable, default .COM;.EXE;.BAT;.CMD).
#[cfg(windows)]
fn path_exts() -> Vec<String> {
    std::env::var("PATHEXT")
        .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into())
        .split(';')
        .map(|e| e.trim().to_string())
        .filter(|e| !e.is_empty())
        .collect()
}

#[cfg(not(windows))]
fn path_exts() -> Vec<String> {
    Vec::new()
}

/// Program name ⇒ actual executable file path (W2-Windows: CreateProcess does not resolve PATHEXT,
/// npm global `dsh` lands as `dsh.cmd` ⇒ bare name spawn always Err; L3 windows cell empirical
/// "`dsh` CLI not found" while git-bash side `command -v dsh` is visible). unix returns as-is.
fn resolve_program(bin: &str) -> String {
    if bin.contains('\\') || bin.contains('/') || bin.contains('.') {
        return bin.to_string();
    }
    for ext in path_exts() {
        if command_in_path_ext(bin, &ext) {
            return format!("{}{}", bin, ext);
        }
    }
    bin.to_string()
}

/// Whether bin+ext exists in PATH (same criteria as the Windows branch of command_in_path, single source of truth).
fn command_in_path_ext(bin: &str, ext: &str) -> bool {
    let path = std::env::var("PATH").unwrap_or_default();
    let sep = if cfg!(windows) { ';' } else { ':' };
    path.split(sep)
        .any(|d| !d.is_empty() && Path::new(d).join(format!("{}{}", bin, ext)).is_file())
}

/// `_probe_present`: `skip_if` probe (runs argv, output contains match (case-insensitive) ⇒ "already present").
pub fn probe_present(spec: &Value, ctx: &Map<String, Value>) -> bool {
    let argv: Vec<String> = spec
        .get("argv")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|x| tmpl(x.as_str().unwrap_or(""), ctx))
                .collect()
        })
        .unwrap_or_default();
    let m = spec
        .get("match")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_lowercase();
    if argv.is_empty() || m.is_empty() {
        return false;
    }
    let mut c = Command::new(resolve_program(&argv[0]));
    c.args(&argv[1..]);
    let _ = PROBE_TIMEOUT; // Python side probe has a 30s limit; here we rely on the subprocess exiting naturally
    match c.output() {
        Ok(o) => {
            if !o.status.success() {
                return false;
            }
            let mut s = String::from_utf8_lossy(&o.stdout).to_lowercase();
            s.push_str(&String::from_utf8_lossy(&o.stderr).to_lowercase());
            s.contains(&m)
        }
        Err(_) => false,
    }
}

/// `_sdk_install`: calls the SDK self-contained install entry (calls **published** function by name, `manager` is only passed if its signature accepts it).
fn sdk_install(
    kind: &str,
    fn_name: &str,
    system_home: &str,
    sid: &str,
    manager: &str,
    core_dir: &Path,
) -> i32 {
    if fn_name.is_empty() {
        warn(&format!(
            "sdk_install: registry did not configure fn for platform '{kind}' (SDK entry missing)"
        ));
        return 1;
    }
    let kwargs = if manager.is_empty() {
        json!({})
    } else {
        json!({"__if_accepted__": {"manager": manager}})
    };
    match sdkcall::call_positional(
        "aimail.install",
        fn_name,
        &[
            Value::String(system_home.to_string()),
            Value::String(sid.to_string()),
        ],
        &kwargs,
        core_dir,
        SDK_INSTALL_TIMEOUT,
        &[],
    ) {
        Ok(v) => v.as_i64().unwrap_or(0) as i32,
        Err(e) => {
            warn(&format!(
                "pysdk entry call failed: {}",
                e.display_like_python()
            ));
            1
        }
    }
}

/// Selection of `-a/--agent`: name = host internal agent name, one-to-one; ignore mismatches.
///
/// Host can enumerate agents (`agent_keys` non-empty) ⇒ take intersection; cannot enumerate (single agent host) ⇒ `-a`
/// cannot be validated, fall back to main agent and warn (keep install usable, do not silently drop binding).
struct AgentPick {
    agents: Vec<String>,
    ignored: Vec<String>,
    no_enumeration: bool,
}

fn pick_agents(requested: &[String], keys: &[String]) -> AgentPick {
    if requested.is_empty() {
        return AgentPick {
            agents: vec!["agent".to_string()],
            ignored: Vec::new(),
            no_enumeration: false,
        };
    }
    if keys.is_empty() {
        return AgentPick {
            agents: vec!["agent".to_string()],
            ignored: Vec::new(),
            no_enumeration: true,
        };
    }
    let mut agents: Vec<String> = Vec::new();
    let mut ignored: Vec<String> = Vec::new();
    for r in requested {
        if keys.iter().any(|k| k == r) {
            if !agents.iter().any(|a| a == r) {
                agents.push(r.clone());
            }
        } else if !ignored.iter().any(|a| a == r) {
            ignored.push(r.clone());
        }
    }
    AgentPick {
        agents,
        ignored,
        no_enumeration: false,
    }
}

/// Execute a series of actions (table provided by caller: registry read or test synthesis). Returns Err = `_fail` semantics (caller rc=1).
pub fn run_steps(
    platform: &str,
    steps: &[Value],
    ctx: &mut Map<String, Value>,
    core_dir: &Path,
) -> Result<(), String> {
    // SDK root (`{sdk}`) is **lazily resolved per step** (owner 2026-10-12): it does not exist before install (only exists after sdk_install),
    // Only platforms that reference `{sdk}` or need to run the registration step require it ⇒ node host does not trigger, no more false positives; deerflow's `{sdk}`
    // can also get the **post-install** true root (originally expanded to empty root = true root cause of exit 127).
    let mut sdk_dir: PathBuf = core_dir.to_path_buf();
    let mut sdk_ready =
        !sdk_dir.as_os_str().is_empty() && crate::core::sdkroot::is_sdk_root(&sdk_dir);
    // If the `core_dir` provided by the caller is already valid (re-run after install/installed environment), immediately backfill template values ——
    // otherwise `{sdk}` will expand to empty (install.rs no longer resolves early).
    if sdk_ready {
        ctx.insert(
            "sdk".into(),
            serde_json::json!(sdk_dir.to_string_lossy().to_string()),
        );
    }
    let mut sdk_tried = false;
    let mut sdk_warned = false;
    // Only python host needs SDK root (registrar python_script lands inside SDK package; node host registration goes through op gate, not needed)
    let py_host = crate::hosts::get(platform)
        .map(|h| crate::hosts::exec::needs_sdk_root(h))
        .unwrap_or(false);
    for st in steps {
        let when = st.get("when").cloned().unwrap_or(json!({}));
        if when.as_object().map(|o| !o.is_empty()).unwrap_or(false) && !when_ok(&when, ctx) {
            continue;
        }
        let kind = st.get("kind").and_then(Value::as_str).unwrap_or("");
        if !sdk_ready && !sdk_tried {
            let body = serde_json::to_string(st).unwrap_or_default();
            if body.contains("{sdk}")
                || (py_host && matches!(kind, "register_default" | "register_all"))
            {
                sdk_tried = true;
                match crate::core::sdkroot::resolve_with(&ctx_str(ctx, "python")) {
                    Ok(r) => {
                        sdk_ready = true;
                        sdk_dir = r.path.clone();
                        ctx.insert(
                            "sdk".into(),
                            serde_json::json!(sdk_dir.to_string_lossy().to_string()),
                        );
                    }
                    Err(e) => {
                        if !sdk_warned {
                            // Only report (loudly) when there **are** steps that need it, and only report once
                            eprintln!("  ✗ {e}");
                            sdk_warned = true;
                        }
                    }
                }
            }
        }
        match kind {
            "print" => println!(
                "{}",
                tmpl(st.get("text").and_then(Value::as_str).unwrap_or(""), ctx)
            ),
            "warn" => warn(&tmpl(
                st.get("text").and_then(Value::as_str).unwrap_or(""),
                ctx,
            )),
            "sdk_install" => {
                // P1 hard gate: registration/whitelist writing steps do not accept empty manager (checked before try)
                if st
                    .get("needs_manager")
                    .and_then(Value::as_bool)
                    .unwrap_or(true)
                {
                    let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
                    let m = setup::resolve_mgr(&ctx_str(ctx, "manager"), &cfg);
                    if m.is_empty() {
                        return Err(format!(
                            "manager missing (not given via arg or env) — install sdk_install({}) register/allowlist does not accept empty values (owner contract 2026-09-30).\n  provide a value: -m/--manager <addr>, or env {}",
                            st.get("fn").and_then(Value::as_str).unwrap_or(""),
                            setup::MANAGER_ENV_NAMES.join(" / ")
                        ));
                    }
                    ctx.insert("manager".into(), Value::String(m));
                }
                let target = st
                    .get("target")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(platform);
                let fn_name = st.get("fn").and_then(Value::as_str).unwrap_or("");
                let rc = sdk_install(
                    target,
                    fn_name,
                    &ctx_str(ctx, "home"),
                    &ctx_str(ctx, "sid"),
                    &ctx_str(ctx, "manager"),
                    &sdk_dir,
                );
                let ok_t = st
                    .get("ok_text")
                    .and_then(Value::as_str)
                    .unwrap_or("sdk install done");
                if rc != 0 && st.get("on_error").and_then(Value::as_str).unwrap_or("warn") == "fail"
                {
                    let mut ectx = ctx.clone();
                    ectx.insert("rc".into(), Value::String(rc.to_string()));
                    let tmpl_text = st
                        .get("fail_hint")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| format!("sdk install({fn_name}) failed(exit {{rc}})"));
                    return Err(format!(
                        "{}\n  python={} sdk={} cmd={}",
                        tmpl(&tmpl_text, &ectx),
                        ectx.get("python").and_then(Value::as_str).unwrap_or(""),
                        ectx.get("sdk").and_then(Value::as_str).unwrap_or(""),
                        ectx.get("cmd").and_then(Value::as_str).unwrap_or("")
                    ));
                }
                let msg = tmpl(ok_t, ctx);
                if rc == 0 {
                    ok(&msg);
                } else {
                    ok(&format!("{msg} — see warnings"));
                }
            }
            "spawn" => {
                let argv: Vec<String> = st
                    .get("argv")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .map(|x| tmpl(x.as_str().unwrap_or(""), ctx))
                            .collect()
                    })
                    .unwrap_or_default();
                if argv.is_empty() {
                    continue;
                }
                if let Some(spec) = st.get("skip_if") {
                    if spec.as_object().map(|o| !o.is_empty()).unwrap_or(false)
                        && probe_present(spec, ctx)
                    {
                        let text = st
                            .get("skipped_text")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| {
                                format!("already present, skipping: {}", argv.join(" "))
                            });
                        ok(&tmpl(&text, ctx));
                        continue;
                    }
                }
                let mut cmd = Command::new(resolve_program(&argv[0]));
                cmd.args(&argv[1..]);
                if let Some(env) = st.get("env").and_then(Value::as_object) {
                    for (k, v) in env {
                        cmd.env(k, tmpl(v.as_str().unwrap_or(""), ctx));
                    }
                }
                // P8(2026-10-17): Process environment no longer inherits bound interpreter (in-process set_var removed)——
                // Step subprocess explicitly injects process-local bound value (only subprocess reader = deer-flow
                // ${AIMAIL_PYTHON} in install-skill.sh / install-mcp.sh; not bound ⇒ do not inject).
                if let Some((k, v)) = crate::core::sdkroot::bound_python_env() {
                    cmd.env(&k, &v);
                }
                match cmd.status() {
                    Err(_) => {
                        let mode = st
                            .get("on_missing")
                            .and_then(Value::as_str)
                            .unwrap_or("fail");
                        let hint = st
                            .get("fail_hint")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("`{}` not executable", argv[0]));
                        if mode == "fail" {
                            println!(
                                "  [step-fail] python={} sdk={}",
                                ctx.get("python").and_then(Value::as_str).unwrap_or(""),
                                ctx.get("sdk").and_then(Value::as_str).unwrap_or("")
                            );
                            return Err(format!(
                                "{}\n  python={} sdk={}",
                                tmpl(&hint, ctx),
                                ctx.get("python").and_then(Value::as_str).unwrap_or(""),
                                ctx.get("sdk").and_then(Value::as_str).unwrap_or("")
                            ));
                        }
                        let wh = st
                            .get("warn_hint")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("`{}` missing, skipping", argv[0]));
                        warn(&tmpl(&wh, ctx));
                    }
                    Ok(status) => {
                        let rc = status.code().unwrap_or(-1);
                        if rc != 0 {
                            let mut ectx = ctx.clone();
                            ectx.insert("rc".into(), Value::String(rc.to_string()));
                            let msg = tmpl(
                                st.get("warn_hint")
                                    .and_then(Value::as_str)
                                    .map(|s| s.to_string())
                                    .unwrap_or_else(|| format!("`{}` exit {rc}", argv.join(" ")))
                                    .as_str(),
                                &ectx,
                            );
                            if st.get("on_error").and_then(Value::as_str).unwrap_or("warn")
                                == "fail"
                            {
                                return Err(msg);
                            }
                            warn(&msg);
                        } else if let Some(t) = st.get("ok_text").and_then(Value::as_str) {
                            let m = tmpl(t, ctx);
                            ok(&m);
                        }
                    }
                }
            }
            "register_all" => {
                if !ctx
                    .get("all_agents")
                    .and_then(Value::as_bool)
                    .unwrap_or(false)
                {
                    continue;
                }
                let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
                let m = setup::resolve_mgr(&ctx_str(ctx, "manager"), &cfg);
                if m.is_empty() {
                    return Err(format!(
                        "manager missing (not given via arg or env) — install register_all({platform}) register/allowlist does not accept empty values (owner contract 2026-09-30).\n  provide a value: -m/--manager <addr>, or env {}",
                        setup::MANAGER_ENV_NAMES.join(" / ")
                    ));
                }
                ctx.insert("manager".into(), Value::String(m.clone()));
                match register::register_all(platform, &cfg, &m, &ctx_str(ctx, "home"), &sdk_dir) {
                    Ok(()) => {
                        let t = st
                            .get("ok_text")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("{platform} full registration complete"));
                        ok(&tmpl(&t, ctx));
                    }
                    Err(e) => {
                        let wh = st
                            .get("warn_hint")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| {
                                format!(
                                    "{platform} full registration failed (see above)——can retry later with reset --all-agents"
                                )
                            });
                        warn(&format!("{}; exception: {e}", tmpl(&wh, ctx)));
                    }
                }
            }

            "register_default" => {
                let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
                let m = setup::resolve_mgr(&ctx_str(ctx, "manager"), &cfg);
                if m.is_empty() {
                    return Err(format!(
                        "manager missing (not given via arg or env) — install register_default({platform}) register/allowlist does not accept empty values (owner contract 2026-09-30).\n  provide a value: -m/--manager <addr>, or env {}",
                        setup::MANAGER_ENV_NAMES.join(" / ")
                    ));
                }
                ctx.insert("manager".into(), Value::String(m.clone()));
                let home = ctx_str(ctx, "home");
                // `-a/--agent`: name = host internal agent name, one-to-one; ignore if mismatched.
                let requested: Vec<String> = ctx
                    .get("agents")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect()
                    })
                    .unwrap_or_default();
                let keys = crate::core::detect::detect_static(Path::new(&home), Some(platform))
                    .map(|i| i.agent_keys)
                    .unwrap_or_default();
                let pick = pick_agents(&requested, &keys);
                for name in &pick.ignored {
                    warn(&format!(
                        "{platform}: agent '{name}' is not in the agent list for this platform, ignored"
                    ));
                }
                if pick.no_enumeration && !requested.is_empty() {
                    warn(&format!(
                        "{platform}: this host cannot enumerate agents, -a not applicable —— register as main agent"
                    ));
                }
                let mut done = 0usize;
                let mut last_err = String::new();
                for agent in &pick.agents {
                    match register::register_agent(platform, agent, &cfg, &m, &home, &sdk_dir) {
                        Ok(()) => done += 1,
                        Err(e) => last_err = e.to_string(),
                    }
                }
                if done > 0 {
                    ok(&format!(
                        "{platform} agent registered + pointer written ({done}/{} agent)",
                        pick.agents.len()
                    ));
                } else if pick.agents.is_empty() {
                    warn(&format!(
                        "{platform}: none of the specified agents are on this platform, no addresses registered (all ignored)"
                    ));
                } else {
                    let wh = st
                        .get("warn_hint")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| format!("{platform} agent registration failed (see above)——retry later"));
                    warn(&format!("{}; exception: {last_err}", tmpl(&wh, ctx)));
                }
            }
            other => {
                return Err(format!(
                "install_steps: unknown kind '{other}' ({platform}) — host table and executor disagree (refusing half-install)"
            ))
            }
        }
    }
    Ok(())
}

/// Registry-driven entry: reads the registry (typed constants) `install_steps` (empty table ⇒ return directly).
pub fn run_install_steps(
    platform: &str,
    ctx: &mut Map<String, Value>,
    core_dir: &Path,
) -> Result<(), String> {
    let steps = crate::hosts::get(platform)
        .map(|h| crate::hosts::exec::install_steps_json(h))
        .unwrap_or_default();
    if steps.is_empty() {
        return Ok(());
    }
    run_steps(platform, &steps, ctx, core_dir)
}

// ════════════════════════════════════════════════════════════════════════════
// Uninstall cleanup executor (`cli/aimail:688-727`): kind = print|warn|sdk_uninstall|rm_pointer|rm_dir
// (The `spawn` step for hermes in the registry also falls into the "unknown kind" warning on the Python side — copy as-is, do not fix on the fly)
// ════════════════════════════════════════════════════════════════════════════

/// `_cleanup_home`: the home provided by the caller (if it exists) or cfg.system_home.
fn cleanup_home(ctx: &Map<String, Value>) -> String {
    let h = ctx_str(ctx, "home");
    if !h.is_empty() && Path::new(&h).is_dir() {
        return h;
    }
    let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
    setup::pget(&cfg, "system_home")
}

/// `_sdk_uninstall`: calls the SDK self-contained uninstall entry (calls **published** functions by name).
fn sdk_uninstall(kind: &str, fn_name: &str, home: &str, sid: &str, core_dir: &Path) -> i32 {
    if fn_name.is_empty() {
        warn(&format!(
            "sdk_uninstall: registry did not configure fn for platform '{kind}' (SDK entry missing)"
        ));
        return 1;
    }
    match sdkcall::call_positional(
        "aimail.install",
        fn_name,
        &[
            Value::String(home.to_string()),
            Value::String(sid.to_string()),
        ],
        &json!({}),
        core_dir,
        SDK_INSTALL_TIMEOUT,
        &[],
    ) {
        Ok(v) => v.as_i64().unwrap_or(0) as i32,
        Err(e) => {
            warn(&format!("pysdk import failed: {}", e.display_like_python()));
            1
        }
    }
}

/// Executes the uninstall cleanup action table.
pub fn run_uninstall_steps(
    platform: &str,
    ctx: &mut Map<String, Value>,
    core_dir: &Path,
) -> Result<(), String> {
    let steps = crate::hosts::get(platform)
        .map(|h| crate::hosts::exec::uninstall_steps_json(h))
        .unwrap_or_default();
    if steps.is_empty() {
        return Ok(());
    }
    let home = cleanup_home(ctx);
    for st in &steps {
        let when = st.get("when").cloned().unwrap_or(json!({}));
        if when.as_object().map(|o| !o.is_empty()).unwrap_or(false) && !when_ok(&when, ctx) {
            continue;
        }
        let kind = st.get("kind").and_then(Value::as_str).unwrap_or("");
        match kind {
            "print" => println!(
                "{}",
                tmpl(st.get("text").and_then(Value::as_str).unwrap_or(""), ctx)
            ),
            "warn" => warn(&tmpl(
                st.get("text").and_then(Value::as_str).unwrap_or(""),
                ctx,
            )),
            "sdk_uninstall" => {
                let target = st
                    .get("target")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or(platform);
                let rc = sdk_uninstall(
                    target,
                    st.get("fn").and_then(Value::as_str).unwrap_or(""),
                    &home,
                    &ctx_str(ctx, "sid"),
                    core_dir,
                );
                if rc != 0 {
                    warn(&format!("{target} SDK uninstall exit {rc} (see above)"));
                }
            }
            "rm_pointer" => {
                let p = PathBuf::from(tmpl(
                    st.get("path").and_then(Value::as_str).unwrap_or(""),
                    ctx,
                ));
                if p.is_file() {
                    let same_sid = std::fs::read_to_string(&p)
                        .ok()
                        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                        .and_then(|v| {
                            v.get("system_id")
                                .and_then(Value::as_str)
                                .map(|s| s.to_string())
                        })
                        .map(|s| s == ctx_str(ctx, "sid"))
                        .unwrap_or(false);
                    if same_sid {
                        let _ = std::fs::remove_file(&p);
                        let text = st
                            .get("ok_text")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("removed pointer {}", p.to_string_lossy()));
                        let m = tmpl(&text, ctx);
                        ok(&m);
                    }
                }
            }
            "rm_dir" => {
                let p = PathBuf::from(tmpl(
                    st.get("path").and_then(Value::as_str).unwrap_or(""),
                    ctx,
                ));
                if p.is_dir() {
                    let _ = std::fs::remove_dir_all(&p);
                    let text = st
                        .get("ok_text")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| format!("removed {}", p.to_string_lossy()));
                    let m = tmpl(&text, ctx);
                    ok(&m);
                }
            }
            other => {
                return Err(format!(
                "uninstall_steps: unknown kind '{other}' ({platform}) — host table and executor disagree (refusing half-cleanup)"
            ))
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `-a/--agent` selection rule: name = host internal agent name, one-to-one; mismatches are ignored.
    #[test]
    fn pick_agents_selects_by_host_keys_and_ignores_unknown() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        // Not specified ⇒ main agent (behavior unchanged)
        let p = pick_agents(&[], &s(&["web", "writer"]));
        assert_eq!(p.agents, s(&["agent"]));
        assert!(!p.no_enumeration);

        // Hits + misses + duplicates
        let p = pick_agents(
            &s(&["web", "nope", "web", "writer"]),
            &s(&["web", "writer", "main"]),
        );
        assert_eq!(p.agents, s(&["web", "writer"]));
        assert_eq!(p.ignored, s(&["nope"]));

        // All misses ⇒ none registered
        let p = pick_agents(&s(&["ghost"]), &s(&["web"]));
        assert!(p.agents.is_empty());
        assert_eq!(p.ignored, s(&["ghost"]));

        // Host cannot enumerate (single-agent host) ⇒ fall back to main agent and mark it
        let p = pick_agents(&s(&["main"]), &[]);
        assert_eq!(p.agents, s(&["agent"]));
        assert!(p.no_enumeration);
    }

    /// Regression pin (owner 2026-10-12): **Steps on node hosts must not reference `{sdk}` / `{python}`** ——
    /// That would make the "SDK root" required for node hosts, resulting in misleading errors on machines without agent-aimail installed
    /// (Observed: in L2, both dsh/pi showed `✗ installed agent-aimail not found`). Only python hosts need them.
    #[test]
    fn node_hosts_never_reference_sdk_root_placeholders() {
        for id in crate::hosts::order() {
            let py_host = crate::hosts::get(id)
                .map(|h| crate::hosts::exec::needs_sdk_root(h))
                .unwrap_or(false);
            if py_host {
                continue;
            }
            let Some(h) = crate::hosts::get(id) else {
                continue;
            };
            let mut all: Vec<Value> = crate::hosts::exec::install_steps_json(h);
            all.extend(crate::hosts::exec::uninstall_steps_json(h));
            for st in &all {
                let body = serde_json::to_string(st).unwrap_or_default();
                assert!(
                    !body.contains("{sdk}") && !body.contains("{python}"),
                    "{id} is a node host, steps must not reference {{sdk}}/{{python}}: {body}"
                );
            }
        }
    }
}
