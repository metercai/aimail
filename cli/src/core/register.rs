//! 平台注册执行器（`cli/aimail:2174-2458` 的 Rust 复刻）—— 注册表驱动、CLI 零平台协议。
//!
//! 两种粒度：
//! · `register_agent`  = 单个 agent（`register_default` / `reset` 默认路径）
//! · `register_all`    = 全量（`--all-agents`；注册表 `register_all` 块）
//!
//! 边界（硬）：**规则不在本文件**——定名/别名归一/直达判定由 SDK 的 `plan_address_name`
//! 给计划（经 `core::sdkcall` 按名调用已发布函数），云端改名链由 SDK 的 `rename_address`
//! 一次完成；本文件只做"读注册表 → 填模板 → 起子进程 → 按结果播报/失败"。
//!
//! `_finish_registration` 只播报**生效地址**，**不**推桥路由：路由是平台侧单一契约
//! （pysdk `register_bridge_route` / TS `mail-core`），第二个写者会造成双真源。

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::core::{platforms, sdkcall, setup, style};

const REGISTRAR_TIMEOUT: Duration = Duration::from_secs(120);

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

/// 候选平台根：调用方给的 home → cfg.system_home → `~/.<home_dir>`。
fn candidate_homes(platform: &str, cfg: &Value, platform_home: &str) -> Vec<String> {
    let home_dir = platforms::home_dir(platform).unwrap_or("");
    let fallback = crate::core::home::user_home()
        .join(format!(".{home_dir}"))
        .to_string_lossy()
        .to_string();
    [
        platform_home.to_string(),
        setup::pget(cfg, "system_home"),
        fallback,
    ]
    .into_iter()
    .filter(|h| !h.is_empty())
    .collect()
}

/// `_resolve_node_entry`：在候选 home 上解析 node 入口（模板 `{home}` → 首个命中）。
fn resolve_node_entry(tmpl: &str, homes: &[String]) -> String {
    for h in homes {
        let cand = tmpl.replace("{home}", h);
        if cand.contains('*') {
            if let Ok(hits) = glob::glob(&cand) {
                let mut v: Vec<PathBuf> = hits.flatten().collect();
                v.sort();
                if let Some(first) = v.first() {
                    return first.to_string_lossy().to_string();
                }
            }
        } else if Path::new(&cand).is_file() {
            return cand;
        }
    }
    String::new()
}

/// `_run_registrar`：子进程调用平台注册器 → (rc, stdout+stderr)。
        if std::env::var("AIMAIL_DEBUG_PLAN").is_ok() {
            eprintln!("[dbg] register platform={} plan={:?} reg_as={} needs_rename={} argv={:?}", platform, plan, plan_reg_as, needs_rename, argv);
        }
fn run_registrar(argv: &[String], env: &[(String, String)]) -> (i32, String) {
    let mut c = Command::new(&argv[0]);
    c.args(&argv[1..]);
    c.envs(env.iter().cloned());
    match c.output() {
        Ok(o) => {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            s.push_str(&String::from_utf8_lossy(&o.stderr));
            (o.status.code().unwrap_or(-1), s)
        }
        Err(e) => (127, format!("注册器不可执行: {e}")),
    }
}

fn tail(s: &str, n: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    let start = chars.len().saturating_sub(n);
    chars[start..].iter().collect()
}

/// `_rename_after_reg`：注册(默认名)后 rename 收口到目标地址名（改名链全在 SDK 内）。
fn rename_after_reg(
    cfg: &Value,
    default_name: &str,
    reg_name: &str,
    sid: &str,
    domain: &str,
    core_dir: &Path,
) -> Result<(), String> {
    let sys_name = setup::pget(cfg, "system_name");
    let base = if default_name == "agent" {
        "agent".to_string()
    } else {
        default_name.to_string()
    };
    let old_email = if sys_name.is_empty() {
        format!("{base}@{domain}")
    } else {
        format!("{base}.{sys_name}@{domain}")
    };
    let res = sdkcall::call(
        "aimail_base",
        "rename_address",
        &json!({"system_id": sid, "old_email": old_email, "new_name": reg_name, "cfg": cfg}),
        core_dir,
        REGISTRAR_TIMEOUT,
        &[],
    )
    .map_err(|e| {
        format!(
            "注册后 rename {old_email} 失败: {}",
            e.display_like_python()
        )
    })?;
    if res
        .get("unchanged")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Ok(());
    }
    if res.get("merged").and_then(Value::as_bool).unwrap_or(false) {
        warn(&format!(
            "本地目录 {} 已存在,合并内容字段(保留两个目录)",
            res.get("dir").and_then(Value::as_str).unwrap_or("")
        ));
    }
    if !res
        .get("migrated")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        warn("本地迁移失败(云端已改名,重跑 install 可重建本地)");
    }
    ok(&format!(
        "  地址名已改: {} → {}",
        res.get("old_email").and_then(Value::as_str).unwrap_or(""),
        res.get("new_email").and_then(Value::as_str).unwrap_or("")
    ));
    let sig_state = res
        .get("signal")
        .and_then(|s| s.get("state"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if sig_state != "notified" {
        warn(&format!(
            "  上线信号未送达({}): {} — 路由将在下次 agent 启动/inbound-live 时对账",
            if sig_state.is_empty() {
                "skipped"
            } else {
                sig_state
            },
            res.get("signal")
                .and_then(|s| s.get("detail"))
                .and_then(Value::as_str)
                .unwrap_or("")
        ));
    }
    Ok(())
}

/// `_finish_registration`：只播报生效地址 + 自检提示（路由不在这里推）。
fn finish_registration(platform: &str, agent: &str, email: &str, sid: &str) {
    ok(&format!(
        "agent '{agent}' registered -> {email} (platform {platform})"
    ));
    println!("  verify: aimail address -s {sid} -a {agent}");
}

/// `_register_agent_now`（单 agent）。
pub fn register_agent(
    platform: &str,
    agent: &str,
    cfg: &Value,
    reg_name: &str,
    manager: &str,
    platform_home: &str,
    core_dir: &Path,
) -> Result<(), String> {
    let sid = setup::pget(cfg, "system_id");
    let _home = setup::pget(cfg, "system_home");
    let domain = setup::pget(cfg, "domain");
    let system_name = setup::pget(cfg, "system_name");
    let aliases = platforms::aliases(platform);
    let pdef = platforms::platform(platform).cloned().unwrap_or(json!({}));
    let rdef = pdef.get("register").cloned().unwrap_or(json!({}));

    // 定名计划：规则全在 SDK（本函数只把"映射层数据"作为输入送进去）
    let mut register_argv: Vec<Value> = Vec::new();
    for key in ["argv", "args"] {
        if let Some(arr) = rdef.get(key).and_then(Value::as_array) {
            register_argv.extend(arr.iter().cloned());
        }
    }
    let plan = sdkcall::call(
        "aimail_base",
        "plan_address_name",
        &json!({
            "requested_name": reg_name,
            "agent_id": agent,
            "domain": domain,
            "system_name": system_name,
            "aliases": aliases,
            "register_argv": register_argv,
        }),
        core_dir,
        REGISTRAR_TIMEOUT,
        &[],
    )
    .map_err(|e| match e {
        crate::core::sdk::AbiError::Call { msg, .. } => msg,
        other => other.display_like_python(),
    })?;
    let plan_reg_as = plan
        .get("reg_as")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let plan_email = plan
        .get("email")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let plan_target_email = plan
        .get("target_email")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let needs_rename = plan
        .get("needs_rename")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    // manager 解析链（参数 → cfg → env 三名互认），仍为空即**硬门**（不许以 '' 注册）
    let mgr = setup::resolve_mgr(manager, cfg);
    if mgr.is_empty() {
        return Err(format!(
            "缺 manager(参数/env 均未给)—— {platform} 注册/白名单不接受空值(owner 契约 2026-09-30)。\n  给值: -m/--manager <addr>,或 env {}",
            setup::MANAGER_ENV_NAMES.join(" / ")
        ));
    }

    let cand_homes = candidate_homes(platform, cfg, platform_home);
    let tgt_home = cand_homes.first().cloned().unwrap_or_default();
    let fill = |text: &str| -> String {
        text.replace("{sid}", &sid)
            .replace("{agent}", agent)
            .replace("{manager}", &mgr)
            .replace("{home}", &tgt_home)
            .replace("{email}", &plan_email)
            .replace("{name}", &plan_reg_as)
    };
    let kind = rdef.get("kind").and_then(Value::as_str).unwrap_or("");

    match kind {
        "python_module" => {
            // hermes: 适配层 import 调用（module/fn/profile 规则表驱动）
            let mut extra_paths: Vec<String> = Vec::new();
            if let Some(arr) = rdef.get("pythonpath").and_then(Value::as_array) {
                for rel in arr {
                    let p = crate::core::sdkroot::entry_path(core_dir, rel.as_str().unwrap_or(""));
                    if p.is_dir() {
                        extra_paths.push(p.to_string_lossy().to_string());
                    }
                }
            }
            let home_path = PathBuf::from(&tgt_home);
            let prof = rdef.get("profile").cloned().unwrap_or(json!({}));
            let root_when: Vec<String> = prof
                .get("root_when")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default();
            let home_name = home_path
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            let profile_dir = if root_when.iter().any(|w| w == agent) || agent == home_name {
                home_path.to_string_lossy().to_string()
            } else {
                let pd = home_path.join("profiles").join(agent);
                if !pd.is_dir() {
                    return Err(format!(
                        "hermes profile 目录不存在: {}",
                        pd.to_string_lossy()
                    ));
                }
                pd.to_string_lossy().to_string()
            };
            let mut cfg2 = cfg.clone();
            if !mgr.is_empty() {
                if let Some(o) = cfg2.as_object_mut() {
                    o.insert("manager_address".into(), Value::String(mgr.clone()));
                }
            }
            // 模块名**用注册表原值**：Python 侧候选链是 `aimail.<module>` → `module`，
            // 适配层实际就在 `pysdk/<platform>/<module>.py`（该目录经 PYTHONPATH 注入）。
            // 曾经的 `aimail_{module}` 会拼成 `aimail_aimail_hermes` ⇒ ImportError（L2 实测）。
            let module = rdef
                .get("module")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let fn_name = rdef.get("fn").and_then(Value::as_str).unwrap_or("");
            let mut env: Vec<(String, String)> = Vec::new();
            if !extra_paths.is_empty() {
                env.push(("PYTHONPATH".into(), extra_paths.join(":")));
            }
            // Python 侧会把 `aimail` logger 临时压到 CRITICAL —— 适配层内部日志不该污染 CLI 输出
            if let Err(e) = sdkcall::call_positional(
                &module,
                fn_name,
                &[
                    Value::String(reg_name.to_string()),
                    Value::String(profile_dir),
                    cfg2,
                ],
                &json!({}),
                core_dir,
                REGISTRAR_TIMEOUT,
                &env,
            ) {
                return Err(e.display_like_python());
            }
            finish_registration(platform, agent, &plan_target_email, &sid);
            Ok(())
        }
        "host_command" | "python_script" | "node_entry" => {
            let argv: Vec<String> = match kind {
                "host_command" => {
                    let mut v: Vec<String> = rdef
                        .get("argv")
                        .and_then(Value::as_array)
                        .map(|a| a.iter().map(|x| fill(x.as_str().unwrap_or(""))).collect())
                        .unwrap_or_default();
                    if mgr.contains('@') {
                        if let Some(ma) = rdef.get("manager_arg").and_then(Value::as_array) {
                            v.extend(ma.iter().map(|x| fill(x.as_str().unwrap_or(""))));
                        }
                    }
                    v
                }
                "python_script" => {
                    let rel = rdef.get("python").and_then(Value::as_str).unwrap_or("");
                    // 注册表条目 = **SDK 根相对**（C 后不含 pysdk/）⇒ 直接与 SDK 根拼
                    let script = crate::core::sdkroot::entry_path(core_dir, rel);
                    if !script.is_file() {
                        return Err(format!(
                            "{platform} 注册器缺失: {}(SDK 未随本机安装)",
                            script.to_string_lossy()
                        ));
                    }
                    let mut v = vec![setup::python_bin(), script.to_string_lossy().to_string()];
                    if let Some(args) = rdef.get("args").and_then(Value::as_array) {
                        v.extend(args.iter().map(|x| fill(x.as_str().unwrap_or(""))));
                    }
                    v
                }
                _ => {
                    let node = resolve_node_entry(
                        rdef.get("node_path").and_then(Value::as_str).unwrap_or(""),
                        &cand_homes,
                    );
                    if node.is_empty() {
                        return Err(format!(
                            "{platform} 平台包未安装(node 入口缺失)\n  {}",
                            rdef.get("fail_hint").and_then(Value::as_str).unwrap_or("")
                        ));
                    }
                    let mut v = vec!["node".to_string(), node];
                    if let Some(args) = rdef.get("args").and_then(Value::as_array) {
                        v.extend(args.iter().map(|x| fill(x.as_str().unwrap_or(""))));
                    }
                    v
                }
            };
            let env: Vec<(String, String)> = Vec::new();
            let (rc, out) = run_registrar(&argv, &env);
            if rc != 0 {
                let hint = rdef.get("fail_hint").and_then(Value::as_str).unwrap_or("");
                let n = if kind == "python_script" { 400 } else { 300 };
                return Err(format!(
                    "{platform} 注册失败(exit {rc}): {}\n  {hint}",
                    tail(&out, n)
                ));
            }
            if needs_rename {
                rename_after_reg(cfg, &plan_reg_as, reg_name, &sid, &domain, core_dir)?;
            }
            finish_registration(platform, agent, &plan_target_email, &sid);
            Ok(())
        }
        other => Err(format!(
            "未知注册委托 kind '{other}'(platform {platform})——注册表损坏"
        )),
    }
}

/// 从注册表取 `register` 块的 `default_name`（空则 `"agent"`）。
pub fn default_agent_name(platform: &str) -> String {
    let dn = platforms::platform(platform)
        .and_then(|p| p.get("register"))
        .and_then(|r| r.get("default_name"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if dn.is_empty() {
        "agent".to_string()
    } else {
        dn.to_string()
    }
}

/// `_run_register_all`：注册表 `register_all` 块驱动的全量注册（未定义 ⇒ 单 agent 平台）。
pub fn register_all(
    platform: &str,
    cfg: &Value,
    mgr: &str,
    platform_home: &str,
    core_dir: &Path,
) -> Result<(), String> {
    let pdef = platforms::platform(platform).cloned().unwrap_or(json!({}));
    let rall = pdef.get("register_all").cloned();
    let sid = setup::pget(cfg, "system_id");
    let home_dir = platforms::home_dir(platform).unwrap_or("");
    let home = {
        let h = setup::pget(cfg, "system_home");
        if h.is_empty() {
            crate::core::home::user_home()
                .join(format!(".{home_dir}"))
                .to_string_lossy()
                .to_string()
        } else {
            h
        }
    };
    let Some(rall) = rall else {
        let dn = default_agent_name(platform);
        register_agent(platform, &dn, cfg, &dn, mgr, platform_home, core_dir)?;
        ok(&format!(
            "{platform} 单 agent 平台(register_all 无定义)——默认 agent 已注册"
        ));
        return Ok(());
    };
    let fill = |t: &str| -> String {
        t.replace("{sid}", &sid)
            .replace("{home}", &home)
            .replace("{mgr}", mgr)
    };
    let kind = rall.get("kind").and_then(Value::as_str).unwrap_or("");
    let argv: Vec<String> = match kind {
        "python_script" => {
            let script = crate::core::sdkroot::entry_path(
                core_dir,
                rall.get("python").and_then(Value::as_str).unwrap_or(""),
            );
            if !script.is_file() {
                warn(&format!(
                    "{platform} 全量注册器缺失: {}(SDK 未随本机安装)",
                    script.to_string_lossy()
                ));
                return Ok(());
            }
            let mut v = vec![setup::python_bin(), script.to_string_lossy().to_string()];
            if let Some(args) = rall.get("args").and_then(Value::as_array) {
                v.extend(args.iter().map(|x| fill(x.as_str().unwrap_or(""))));
            }
            v
        }
        "host_command" => rall
            .get("argv")
            .and_then(Value::as_array)
            .map(|a| a.iter().map(|x| fill(x.as_str().unwrap_or(""))).collect())
            .unwrap_or_default(),
        "node_entry" => {
            let homes = candidate_homes(platform, cfg, platform_home);
            let node = resolve_node_entry(
                rall.get("node_path").and_then(Value::as_str).unwrap_or(""),
                &homes,
            );
            if node.is_empty() {
                warn(&format!(
                    "{platform} 平台包未装(register-all node 入口缺失)\n  {}",
                    rall.get("fail_hint").and_then(Value::as_str).unwrap_or("")
                ));
                return Ok(());
            }
            let mut v = vec!["node".to_string(), node];
            if let Some(args) = rall.get("args").and_then(Value::as_array) {
                v.extend(args.iter().map(|x| fill(x.as_str().unwrap_or(""))));
            }
            v
        }
        other => {
            warn(&format!(
                "register_all 未知 kind '{other}'({platform})——注册表损坏"
            ));
            return Ok(());
        }
    };
    let mut env: Vec<(String, String)> = Vec::new();
    if let Some(m) = rall.get("env").and_then(Value::as_object) {
        for (k, v) in m {
            env.push((k.clone(), fill(v.as_str().unwrap_or(""))));
        }
    }
    let (rc, out) = run_registrar(&argv, &env);
    if rc != 0 {
        warn(&format!(
            "{platform} 全量注册 exit {rc}: {}{}\n  {}",
            tail(&out, 400),
            "",
            rall.get("fail_hint").and_then(Value::as_str).unwrap_or("")
        ));
        return Ok(());
    }
    ok(&format!(
        "{platform} register_all 完成(SDK 注册枚举到的全部 agent)"
    ));
    Ok(())
}

/// 供 `reset`/`install` 复用的 cfg → ctx 形状（`Map` 便于后续扩展）。
pub fn ctx_of(cfg: &Value) -> Map<String, Value> {
    cfg.as_object().cloned().unwrap_or_default()
}
