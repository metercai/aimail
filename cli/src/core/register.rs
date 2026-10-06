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

use crate::core::{platforms, setup, style};

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

    // manager 解析链（参数 → cfg → env 三名互认），仍为空即**硬门**（不许以 '' 注册）
    let mgr = setup::resolve_mgr(manager, cfg);
    if mgr.is_empty() {
        return Err(format!(
            "缺 manager(参数/env 均未给)—— {platform} 注册/白名单不接受空值(owner 契约 2026-09-30)。\n  给值: -m/--manager <addr>,或 env {}",
            setup::MANAGER_ENV_NAMES.join(" / ")
        ));
    }

    // ── CLI 域：宿主路径解析（注册表存的是模板/通配路径，只有 CLI 知道宿主布局）──
    let cand_homes = candidate_homes(platform, cfg, platform_home);
    let kind = rdef.get("kind").and_then(Value::as_str).unwrap_or("");
    let mut spec = json!({
        "kind": kind,
        "args_template": rdef.get("args").cloned().unwrap_or(json!([])),
        "argv": rdef.get("argv").cloned().unwrap_or(json!([])),
        "manager_arg": rdef.get("manager_arg").cloned().unwrap_or(json!([])),
        "fail_hint": rdef.get("fail_hint").cloned().unwrap_or(json!("")),
    });
    {
        let o = spec.as_object_mut().unwrap();
        match kind {
            "python_module" => {
                // hermes：适配层目录（PYTHONPATH）+ profile 目录（规则表驱动）
                let mut extra: Vec<String> = Vec::new();
                if let Some(arr) = rdef.get("pythonpath").and_then(Value::as_array) {
                    for rel in arr {
                        let q =
                            crate::core::sdkroot::entry_path(core_dir, rel.as_str().unwrap_or(""));
                        if q.is_dir() {
                            extra.push(q.to_string_lossy().to_string());
                        }
                    }
                }
                let home_path = PathBuf::from(cand_homes.first().cloned().unwrap_or_default());
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
                    .map(|x| x.to_string_lossy().to_string())
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
                o.insert(
                    "module".into(),
                    rdef.get("module").cloned().unwrap_or(json!("")),
                );
                o.insert("fn".into(), rdef.get("fn").cloned().unwrap_or(json!("")));
                o.insert("pythonpath".into(), json!(extra));
                o.insert("profile_dir".into(), json!(profile_dir));
                o.insert("python".into(), json!(setup::python_bin()));
            }
            "python_script" => {
                let rel = rdef.get("python").and_then(Value::as_str).unwrap_or("");
                let script = crate::core::sdkroot::entry_path(core_dir, rel);
                if !script.is_file() {
                    return Err(format!(
                        "{platform} 注册器缺失: {}（SDK 未随本机安装）",
                        script.to_string_lossy()
                    ));
                }
                o.insert("script".into(), json!(script.to_string_lossy().to_string()));
                o.insert("python".into(), json!(setup::python_bin()));
            }
            "node_entry" => {
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
                o.insert("node_path".into(), json!(node));
                o.insert("node_bin".into(), json!("node"));
            }
            "host_command" => {}
            other => {
                return Err(format!(
                    "未知注册委托 kind '{other}'(platform {platform})——注册表损坏"
                ));
            }
        }
    }

    // ── 契约 v1.0 §4.1：定名 + 注册器执行 + 装配期改名 + 落绑定 —— 全在 SDK 门 assemble 内 ──
    let res = crate::core::sdk::sdk_ops_call(
        "assemble",
        &json!({
            "system_id": sid,
            "system_cfg": cfg.clone(),
                                    "domain": domain,
            "system_name": system_name,
            "aliases": aliases,
            "manager_address": mgr,
            "home": cand_homes.first().cloned().unwrap_or_default(),
            "register_spec": spec,
        }),
        // SDK 根用 CLI 解析结果（= 旧 core_dir 语义：repo→pip），与其余调用点同源
        &crate::core::sdkroot::resolve_or_repo_candidate().path,
        REGISTRAR_TIMEOUT,
        &[],
    )
    .map(|e| e.get("result").cloned().unwrap_or(e))
    .map_err(|e| match e {
        crate::core::sdk::AbiError::Call { msg, .. } => msg,
        other => other.display_like_python(),
    })?;
    let target_email = res
        .get("email")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    finish_registration(platform, agent, &target_email, &sid);
    Ok(())
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
        register_agent(platform, platform, cfg, mgr, platform_home, core_dir)?;
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
