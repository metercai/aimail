//! 平台动作序列执行器（`cli/aimail:427-678` 的 Rust 复刻）—— `platforms.json` 的
//! `install_steps` / `uninstall_steps` 表驱动，**CLI 零平台知识**。
//!
//! kind：`print` | `warn` | `spawn` | `sdk_install` | `register_default` | `register_all`
//! · `when` 前置条件（path_exists / command_exists / sid_set / cfg_complete / runtime / runtime_not）
//! · `spawn`：`skip_if`（探针说"已在场"就跳过）· `on_missing`（fail/skip）· `on_error`（warn/fail）
//!
//! 两条**硬门**照抄且不可被异常兜底吞掉（F9 教训）：需要注册/写白名单的步（`sdk_install`
//! 默认、`register_default`、`register_all`）在进任何 try 之前先判 manager 非空 ⇒ 空即 rc≠0。

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::core::{platforms, register, sdkcall, setup, style};

const PROBE_TIMEOUT: Duration = Duration::from_secs(30);
const SDK_INSTALL_TIMEOUT: Duration = Duration::from_secs(300);

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

/// `_tmpl`：`{k}` 逐键替换（ctx 的所有键）。
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

/// `_when_ok`：`when` 条件求值。
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
            // 其余键忽略（与 Python 同：未知 when 键不参与判定）
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
    std::env::var("PATH")
        .unwrap_or_default()
        .split(':')
        .any(|d| !d.is_empty() && Path::new(d).join(bin).is_file())
}

/// `_probe_present`：`skip_if` 探针（跑 argv，输出含 match（忽略大小写）⇒ "已在场"）。
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
    let mut c = Command::new(&argv[0]);
    c.args(&argv[1..]);
    let _ = PROBE_TIMEOUT; // Python 侧 probe 带 30s 上限；这里靠子进程自然退出
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

/// `_sdk_install`：调 SDK 自足安装入口（按名调**已发布**函数，`manager` 只在其签名接受时才传）。
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
            "sdk_install: 注册表未给平台 '{kind}' 配置 fn(SDK 入口缺失)"
        ));
        return 1;
    }
    let kwargs = if manager.is_empty() {
        json!({})
    } else {
        json!({"__if_accepted__": {"manager": manager}})
    };
    match sdkcall::call_positional(
        "install",
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
            warn(&format!("pysdk 入口调用失败: {}", e.display_like_python()));
            1
        }
    }
}

/// 执行一串动作（表由调用方给：注册表读或测试合成）。返回 Err = `_fail` 语义（调用方 rc=1）。
pub fn run_steps(
    platform: &str,
    steps: &[Value],
    ctx: &mut Map<String, Value>,
    core_dir: &Path,
) -> Result<(), String> {
    for st in steps {
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
            "sdk_install" => {
                // P1 硬门：注册/写白名单的步不接受空 manager（判在 try 之前）
                if st
                    .get("needs_manager")
                    .and_then(Value::as_bool)
                    .unwrap_or(true)
                {
                    let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
                    let m = setup::resolve_mgr(&ctx_str(ctx, "manager"), &cfg);
                    if m.is_empty() {
                        return Err(format!(
                            "缺 manager(参数/env 均未给)—— install sdk_install({}) 的注册/白名单不接受空值(owner 契约 2026-09-30)。\n  给值: -m/--manager <addr>,或 env {}",
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
                    core_dir,
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
                        .unwrap_or_else(|| format!("sdk install({fn_name}) 失败(exit {{rc}})"));
                    return Err(tmpl(&tmpl_text, &ectx));
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
                let mut cmd = Command::new(&argv[0]);
                cmd.args(&argv[1..]);
                if let Some(env) = st.get("env").and_then(Value::as_object) {
                    for (k, v) in env {
                        cmd.env(k, tmpl(v.as_str().unwrap_or(""), ctx));
                    }
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
                            .unwrap_or_else(|| format!("`{}` 不可执行", argv[0]));
                        if mode == "fail" {
                            return Err(tmpl(&hint, ctx));
                        }
                        let wh = st
                            .get("warn_hint")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("`{}` 缺失,跳过", argv[0]));
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
                        "缺 manager(参数/env 均未给)—— install register_all({platform}) 的注册/白名单不接受空值(owner 契约 2026-09-30)。\n  给值: -m/--manager <addr>,或 env {}",
                        setup::MANAGER_ENV_NAMES.join(" / ")
                    ));
                }
                ctx.insert("manager".into(), Value::String(m.clone()));
                match register::register_all(platform, &cfg, &m, &ctx_str(ctx, "home"), core_dir) {
                    Ok(()) => {
                        let t = st
                            .get("ok_text")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| format!("{platform} 全量注册完成"));
                        ok(&tmpl(&t, ctx));
                    }
                    Err(e) => {
                        let wh = st
                            .get("warn_hint")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| {
                                format!(
                                    "{platform} 全量注册失败(见上)——可稍后 reset --all-agents 重试"
                                )
                            });
                        warn(&format!("{}; 异常: {e}", tmpl(&wh, ctx)));
                    }
                }
            }
            "register_default" => {
                let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
                let m = setup::resolve_mgr(&ctx_str(ctx, "manager"), &cfg);
                if m.is_empty() {
                    return Err(format!(
                        "缺 manager(参数/env 均未给)—— install register_default({platform}) 的注册/白名单不接受空值(owner 契约 2026-09-30)。\n  给值: -m/--manager <addr>,或 env {}",
                        setup::MANAGER_ENV_NAMES.join(" / ")
                    ));
                }
                ctx.insert("manager".into(), Value::String(m.clone()));
                let agent = register::default_agent_name(platform);
                match register::register_agent(
                    platform, &agent, &cfg, &m, &ctx_str(ctx, "home"), 
                    core_dir, 
                ) {
                    Ok(()) => ok(&format!("{platform} agent registered + pointer written")),
                    Err(e) => {
                        let wh = st
                            .get("warn_hint")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| {
                                format!("{platform} agent 注册失败(见上)——稍后重试")
                            });
                        warn(&format!("{}; 异常: {e}", tmpl(&wh, ctx)));
                    }
                }
            }
            other => warn(&format!(
                "install_steps 未知 kind '{other}'({platform})——跳过"
            )),
        }
    }
    Ok(())
}

/// 注册表驱动入口：读 `platforms.json` 的 `install_steps`（空表 ⇒ 直接返回）。
pub fn run_install_steps(
    platform: &str,
    ctx: &mut Map<String, Value>,
    core_dir: &Path,
) -> Result<(), String> {
    let steps = platforms::install_steps(platform).to_vec();
    if steps.is_empty() {
        return Ok(());
    }
    run_steps(platform, &steps, ctx, core_dir)
}

// ════════════════════════════════════════════════════════════════════════════
// 卸载清理执行器（`cli/aimail:688-727`）：kind = print|warn|sdk_uninstall|rm_pointer|rm_dir
// （注册表里 hermes 的 `spawn` 步在 Python 侧同样落到"未知 kind"告警 —— 照抄，不顺手补）
// ════════════════════════════════════════════════════════════════════════════

/// `_cleanup_home`：调用方给的 home（存在）或 cfg.system_home。
fn cleanup_home(ctx: &Map<String, Value>) -> String {
    let h = ctx_str(ctx, "home");
    if !h.is_empty() && Path::new(&h).is_dir() {
        return h;
    }
    let cfg = ctx.get("cfg").cloned().unwrap_or(json!({}));
    setup::pget(&cfg, "system_home")
}

/// `_sdk_uninstall`：调 SDK 自足卸载入口（按名调**已发布**函数）。
fn sdk_uninstall(kind: &str, fn_name: &str, home: &str, sid: &str, core_dir: &Path) -> i32 {
    if fn_name.is_empty() {
        warn(&format!(
            "sdk_uninstall: 注册表未给平台 '{kind}' 配置 fn(SDK 入口缺失)"
        ));
        return 1;
    }
    match sdkcall::call_positional(
        "install",
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

/// 执行卸载清理动作表。
pub fn run_uninstall_steps(
    platform: &str,
    ctx: &mut Map<String, Value>,
    core_dir: &Path,
) -> Result<(), String> {
    let steps = platforms::uninstall_steps(platform);
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
                    warn(&format!("{target} SDK uninstall exit {rc}(见上)"));
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
            other => warn(&format!(
                "uninstall_steps 未知 kind '{other}'({platform})——跳过"
            )),
        }
    }
    Ok(())
}
