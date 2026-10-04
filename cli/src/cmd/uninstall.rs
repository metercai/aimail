//! `aimail uninstall` —— `cli/aimail:3250-3435` 的 Rust 复刻。
//!
//! 与 `install` 对等：消除 aimail 在 agent 系统上的配置。顺序：
//! sid/home 解析（`resolve_system_id`）→ 幂等短路（数据与指针都不在 ⇒ 视为已卸载）→
//! 平台判定 → **确认**（`-y` 跳过）→ 注销目标网关判定（`-g` > cfg；**刻意不看 AIMAIL_URL、
//! 也不落生产默认**）→ 逐绑定网关注销（SDK 单入口）→ 网关侧兜底（按 sid 反查地址表 +
//! 白名单清理，SDK 单入口）→ 平台 `uninstall_steps`（表驱动）→ 本机数据（mail/系统目录/原始 key）。

use serde_json::{json, Value};
use std::io::Write;

use crate::core::{config, home, platforms, setup, steps, style};

pub struct Args {
    pub system_id: String,
    pub home: String,
    pub gateway_url: String,
    pub yes: bool,
    pub platform: String,
}

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

fn fail(msg: &str) -> i32 {
    println!("  {}{}{} {}", style::RED, style::CROSS, style::NC, msg);
    1
}

/// `resolve_system_id`：显式 sid > 配置 system_home 反查 > 指针归属 > 自动探测。
pub(crate) fn resolve_system_id(
    system_home: &std::path::Path,
    explicit_sid: &str,
) -> (String, String) {
    let platform = if system_home.as_os_str().is_empty() {
        String::new()
    } else {
        platforms::detect_platform_from_home(system_home).to_string()
    };
    if explicit_sid.is_empty() {
        return (String::new(), platform);
    }
    // 配置反查：该 sid 的 cfg.system_home 指向哪个平台
    if let Some(c) = config::load_gateway_config(explicit_sid) {
        if !c.system_home.is_empty() {
            let p = home::abs_path(&home::expand_user(&c.system_home));
            let det = platforms::detect_platform_from_home(&p);
            if det != "unknown" {
                return (explicit_sid.to_string(), det.to_string());
            }
        }
    }
    // 回退：哪个平台指针指向该 sid（注册表 order 驱动）
    let uh = home::user_home();
    for p in platforms::order() {
        if platforms::pointer_sid_for(&uh, p) == explicit_sid {
            return (explicit_sid.to_string(), p.to_string());
        }
    }
    (explicit_sid.to_string(), platform)
}

/// `_is_readable_file`
fn readable_file(p: &std::path::Path) -> bool {
    p.is_file() && std::fs::File::open(p).is_ok()
}

/// `_confirm`
fn confirm(msg: &str, yes: bool) -> bool {
    if yes {
        return true;
    }
    print!("  {msg} [y/N] ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    if std::io::stdin().read_line(&mut line).is_err() {
        return false;
    }
    // EOF（读到 0 字节）等同于 Python 的 EOFError
    if line.is_empty() {
        return false;
    }
    matches!(line.trim().to_lowercase().as_str(), "y" | "yes")
}

/// `list_system_domains`（`aimail_tools._GatewayClient`）：GET 域表 → 数组或 `data` 数组。
fn list_system_domains(gw_url: &str, admin_key: &str, sid: &str) -> Option<Vec<Value>> {
    let path = format!("/api/v1/admin/systems/{sid}/domains");
    let headers = crate::core::sig::signed_headers(admin_key, "GET", &path, None, "");
    let url = format!("{}{}", gw_url.trim_end_matches('/'), path);
    let (status, body) = crate::core::http::json_req(&url, &headers, None, Some("GET"), 15);
    if !(200..300).contains(&status) {
        return None;
    }
    match body {
        Value::Array(a) => Some(a),
        Value::Object(o) => o.get("data").and_then(Value::as_array).cloned(),
        _ => None,
    }
}

pub fn run(a: &Args) -> i32 {
    let mut sid = a.system_id.clone();
    if a.home.is_empty() && sid.is_empty() {
        return fail("uninstall 需要 --system-id(或用 --home 定位平台)");
    }
    let system_home = if !a.home.is_empty() {
        platforms::normalize_platform_home(&home::expand_user(&a.home))
    } else {
        // 有 sid 无 --home：权威 cfg 反查 system_home；无本地配置留空（交给 resolve_system_id）
        let sh0 = config::load_gateway_config(&sid)
            .map(|c| c.system_home)
            .unwrap_or_default();
        if sh0.is_empty() {
            std::path::PathBuf::new()
        } else {
            home::expand_user(&sh0)
        }
    };
    let (sid2, mut platform) = resolve_system_id(&system_home, &sid);
    sid = sid2;
    if sid.is_empty() {
        return fail("uninstall 需要 --system-id(或用 --home 定位平台)");
    }
    // 幂等：系统数据与指针都不在 ⇒ 视为已卸载
    let systems_dir = config::systems_root_in(&home::aimail_home());
    let has_dir = systems_dir.join(&sid).is_dir();
    let uh = home::user_home();
    let has_ptr = platforms::order()
        .iter()
        .any(|p| platforms::pointer_sid_for(&uh, p) == sid);
    if !has_dir && !has_ptr {
        println!("  system {sid} not installed (already uninstalled?)");
        return 0;
    }
    platform = match platforms::platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => {
            if !platform.is_empty() {
                platform
            } else {
                platforms::resolve_platform(&system_home)
            }
        }
        Err(msg) => return fail(&msg),
    };
    if platform.is_empty() {
        return fail(&format!("无法确定平台(system {sid})——用 --home 指定平台根"));
    }

    println!("  uninstall system={sid} platform={platform}");
    if !confirm(
        &format!("确认卸载 system {sid} 的 aimail 对接(含网关注销与本机数据)?"),
        a.yes,
    ) {
        println!("  cancelled");
        return 1;
    }

    // 注销目标网关判定：-g > cfg（**不看 AIMAIL_URL、不落生产默认**）
    let mut gw: Value = config::load_gateway_config(&sid)
        .map(|c| Value::Object(c.to_json()))
        .unwrap_or_else(|| json!({}));
    let gw_flag = a.gateway_url.trim().to_string();
    let gw_cfg = setup::pget(&gw, "gateway_url").trim().to_string();
    if !gw_flag.is_empty() {
        gw["gateway_url"] = json!(gw_flag);
        ok(&format!("gateway_url: {gw_flag}(显式 -g)"));
        if !gw_cfg.is_empty() && gw_cfg != gw_flag {
            warn(&format!(
                "-g {gw_flag} 覆盖 cfg.gateway_url={gw_cfg}(按 -g 注销)"
            ));
        }
    } else if !gw_cfg.is_empty() {
        ok(&format!("gateway_url: {gw_cfg}(来自 cfg;可用 -g 覆盖)"));
    } else {
        warn(
            "cfg 缺 gateway_url 且未给 -g —— 无法确定注销目标, 跳过网关侧注销(不按生产默认兜底); 本机数据仍会清除",
        );
    }

    let core_dir = crate::core::sdkroot::resolve_or_repo_candidate().path;
    let gw_url = setup::pget(&gw, "gateway_url");
    let admin_key = setup::pget(&gw, "admin_key");
    let manager = setup::pget(&gw, "manager_address");
    let sid_dir = systems_dir.join(&sid);

    // 1. 逐绑定网关注销（公共链：api-key → domain → whitelist，全在 SDK 内）
    if sid_dir.is_dir() {
        let mut subs: Vec<std::path::PathBuf> = std::fs::read_dir(&sid_dir)
            .map(|rd| rd.flatten().map(|e| e.path()).collect())
            .unwrap_or_default();
        subs.sort();
        for sub in subs {
            let aj = sub.join(crate::core::contract::binding_file());
            if !readable_file(&aj) {
                continue;
            }
            let email = std::fs::read_to_string(&aj)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("email")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                })
                .unwrap_or_default();
            if email.is_empty() {
                continue;
            }
            if gw_url.is_empty() || admin_key.is_empty() {
                warn(&format!(
                    "no gateway credentials — skip gateway deregister for {email}"
                ));
                continue;
            }
            let mgr_binding = std::fs::read_to_string(&aj)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("manager_address")
                        .and_then(Value::as_str)
                        .map(|s| s.to_string())
                })
                .unwrap_or_default();
            let mgr_use = if mgr_binding.is_empty() {
                manager.clone()
            } else {
                mgr_binding
            };
            let client = json!({"gateway_url": gw_url, "admin_key": admin_key});
            match crate::core::sdkcall::call_positional(
                "aimail_base",
                "deregister_agent_email",
                &[json!({"__client__": client}), json!(sid), json!(email)],
                &json!({"manager_address": mgr_use}),
                &core_dir,
                std::time::Duration::from_secs(120),
                &[],
            ) {
                Ok(st) => ok(&format!(
                    "gateway deregister {email} (api-key={} domain={} whitelist={})",
                    st.get("api_key").map(|v| v.to_string()).unwrap_or_default(),
                    st.get("domain").map(|v| v.to_string()).unwrap_or_default(),
                    st.get("whitelist")
                        .map(|v| v.to_string())
                        .unwrap_or_default()
                )),
                Err(e) => warn(&format!(
                    "gateway deregister {email} failed: {}",
                    e.display_like_python()
                )),
            }
        }
    }

    // 1b. 网关侧兜底：本机绑定覆盖不到的地址与白名单行（SDK 单入口）
    if !gw_url.is_empty() && !admin_key.is_empty() {
        let local_emails: Vec<String> = if sid_dir.is_dir() {
            std::fs::read_dir(&sid_dir)
                .map(|rd| {
                    rd.flatten()
                        .map(|e| e.path())
                        .filter(|p| p.is_dir())
                        .filter_map(|d| {
                            let aj = d.join(crate::core::contract::binding_file());
                            if !readable_file(&aj) {
                                return None;
                            }
                            std::fs::read_to_string(&aj)
                                .ok()
                                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                                .and_then(|v| {
                                    v.get("email")
                                        .and_then(Value::as_str)
                                        .map(|s| s.to_string())
                                })
                        })
                        .collect()
                })
                .unwrap_or_default()
        } else {
            Vec::new()
        };
        let doms = match list_system_domains(&gw_url, &admin_key, &sid) {
            Some(d) => d,
            None => {
                warn("gateway reverse lookup failed(跳过网关侧兜底)");
                Vec::new()
            }
        };
        let mut addrs: Vec<String> = Vec::new();
        let mut domains: Vec<String> = Vec::new();
        for d in &doms {
            let v = d
                .get("domain")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            if v.is_empty() {
                continue;
            }
            if v.contains('@') {
                addrs.push(v);
            } else {
                domains.push(v);
            }
        }
        let mut deregistered: Vec<String> = Vec::new();
        let client = json!({"gateway_url": gw_url, "admin_key": admin_key});
        for ad in &addrs {
            if local_emails.contains(ad) || (!manager.is_empty() && *ad == manager) {
                continue;
            }
            match crate::core::sdkcall::call_positional(
                "aimail_base",
                "deregister_agent_email",
                &[json!({"__client__": client}), json!(sid), json!(ad)],
                &json!({"manager_address": manager}),
                &core_dir,
                std::time::Duration::from_secs(120),
                &[],
            ) {
                Ok(_) => {
                    deregistered.push(ad.clone());
                    ok(&format!("gateway deregister (no local binding) {ad}"));
                }
                Err(e) => warn(&format!(
                    "gateway deregister (no local binding) {ad} failed: {}",
                    e.display_like_python()
                )),
            }
        }
        match crate::core::sdkcall::call_positional(
            "aimail_base",
            "cleanup_system_whitelists",
            &[
                json!({"__client__": client}),
                json!(sid),
                json!(addrs),
                json!(domains),
                json!(deregistered),
            ],
            &json!({}),
            &core_dir,
            std::time::Duration::from_secs(120),
            &[],
        ) {
            Ok(sw) => {
                if let Some(per_key) = sw.get("per_key").and_then(Value::as_object) {
                    for (k, n) in per_key {
                        if n.as_i64().unwrap_or(0) != 0 {
                            ok(&format!("gateway whitelist sweep({k}): removed {n} row(s)"));
                        }
                    }
                }
                if let Some(errs) = sw.get("errors").and_then(Value::as_array) {
                    for e in errs {
                        warn(&format!(
                            "whitelist sweep: {}",
                            e.as_str().unwrap_or(&e.to_string())
                        ));
                    }
                }
                if let Some(r) = sw.get("removed") {
                    if r.as_i64().unwrap_or(0) != 0 {
                        ok(&format!(
                            "gateway whitelist sweep: {r} row(s) removed (system-owned scope)"
                        ));
                    }
                }
            }
            Err(e) => warn(&format!(
                "whitelist sweep failed: {}",
                e.display_like_python()
            )),
        }
    }

    // 2. 平台侧清理（uninstall_steps 表驱动）
    let mut ctx = serde_json::Map::new();
    ctx.insert(
        "home".into(),
        json!(system_home.to_string_lossy().to_string()),
    );
    ctx.insert("sid".into(), json!(sid));
    ctx.insert("cfg".into(), gw.clone());
    if let Err(e) = steps::run_uninstall_steps(&platform, &mut ctx, &core_dir) {
        warn(&e);
    }

    // 3. 本机数据
    if sid_dir.is_dir() {
        if let Ok(rd) = std::fs::read_dir(&sid_dir) {
            for e in rd.flatten() {
                let aj = e.path().join(crate::core::contract::binding_file());
                let email = std::fs::read_to_string(&aj)
                    .ok()
                    .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                    .and_then(|v| {
                        v.get("email")
                            .and_then(Value::as_str)
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_default();
                if email.is_empty() {
                    continue;
                }
                let mdir = e.path().join("mail");
                if mdir.is_dir() {
                    let _ = std::fs::remove_dir_all(&mdir);
                    ok(&format!("removed mail data {}", mdir.to_string_lossy()));
                }
            }
        }
        let _ = std::fs::remove_dir_all(&sid_dir);
        ok(&format!(
            "removed system data {}",
            sid_dir.to_string_lossy()
        ));
    }
    let raw = sid_dir.join(setup::SYSTEM_RAW_KEY_FILE);
    if raw.is_file() {
        let _ = std::fs::remove_file(&raw);
        ok(&format!("removed raw key {}", raw.to_string_lossy()));
    }

    println!("  uninstall done for {sid}");
    0
}
