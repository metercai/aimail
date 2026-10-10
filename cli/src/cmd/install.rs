//! `aimail install` —— 集成入口。本切片（S6 切片1）只落**机器面分派 + `--payload` 通道**：
//!
//! · `--system-only` / `--payload` 的**互斥与拒参**（`cli/aimail:784-827` 逐字复刻：文案、流向、
//!   rc=2；其中 payload 通道的"未知动作/缺 bundle"文案走 **stdout**，拒参走 stderr —— 与 Python 一致）；
//! · `--payload dir|source|resource` 三个**只读**动作（路径解析，可跨语言逐字比对）；
//! · `--payload install`（写载荷，含 stamp/清理）· `--system-only`（L1 激活/复用，单行 JSON ABI）
//! · **人路径**（`cli/aimail:836-1072`）：激活/复用 worker（`core::setup`）→ domain 预置/创建 →
//!   平台接线（`core::steps` 表驱动）→ 容器 runtime 记录。
//!   桥相关面：路由对账（`core::bridge_wire`）与**远端网关的 bridge 部署**（`core::bridge_deploy`）
//!   均已接线；仅 `bridge --upgrade` 的 zip 通道见 `bridge` 命令面。
//!   —— 不是"尝试失败"，而是"尚未实现"，必须能分辨。
//!
//! 未移植清单在 `cli/tests/cli_surface.rs::unported_faces_are_honest` 里钉住。

use crate::core::payload;
use crate::core::{config, home, platforms, setup, steps};
use std::path::PathBuf;

/// `install` 的全部参数（人面 + 隐藏机器面；未定义的值一律空串/`false`）。
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
    pub system_only: bool,
    pub payload: String,
    pub payload_name: String,
    pub dest: String,
    pub source_root: String,
    pub force: bool,
    pub container: String,
    pub container_home: String,
    pub platform: String,
}

pub fn run(a: Args) -> i32 {
    // ── 机器面分派（互斥；并拒绝另一侧的参数 —— 绝不静默吞掉）─────────────────
    if a.system_only && !a.payload.is_empty() {
        eprintln!("ERROR: --system-only 与 --payload 互斥");
        return 2;
    }
    if !a.payload.is_empty() {
        // payload 通道只认自身参数；激活/接线类参数一律拒（exit 2）
        let rejects: [(&str, bool); 9] = [
            ("-H", !a.home.is_empty()),
            ("-s", !a.system_id.is_empty()),
            ("-c", !a.product_code.is_empty()),
            ("-k", !a.admin_key.is_empty()),
            ("-g", !a.gateway_url.is_empty()),
            ("-n", !a.system_name.is_empty()),
            ("-m", !a.manager.is_empty()),
            ("-d", !a.domain.is_empty()),
            ("--all-agents", a.all_agents),
        ];
        for (flag, present) in rejects {
            if present {
                eprintln!("ERROR: --payload 通道不接受 {flag}");
                return 2;
            }
        }
        return run_payload(
            &a.payload,
            &a.payload_name,
            &a.dest,
            &a.source_root,
            a.force,
        );
    }
    if a.system_only {
        // system-only 是 L1-only 铁律入口：接线/捆绑两侧的参数都拒
        let rejects: [(&str, bool); 5] = [
            ("--all-agents", a.all_agents),
            ("--dest", !a.dest.is_empty()),
            ("--source-root", !a.source_root.is_empty()),
            ("--force", a.force),
            ("payload operand", !a.payload_name.is_empty()),
        ];
        for (flag, present) in rejects {
            if present {
                eprintln!("ERROR: --system-only 通道不接受 {flag}");
                return 2;
            }
        }
        return ensure_system(&a);
    }
    // 无机器面标志却带了机器面参数/操作数 → 显式拒绝（不静默吞拼写错误）
    let stray: [(&str, bool); 4] = [
        ("payload operand", !a.payload_name.is_empty()),
        ("--dest", !a.dest.is_empty()),
        ("--source-root", !a.source_root.is_empty()),
        ("--force", a.force),
    ];
    for (flag, present) in stray {
        if present {
            eprintln!("ERROR: {flag} 只在 --payload 通道有效");
            return 2;
        }
    }
    install_human(&a)
}

/// `cmd_payload`（`cli/aimail:304-338`）。
fn run_payload(action: &str, name: &str, dest: &str, source_root: &str, force: bool) -> i32 {
    let name = name.trim();
    match action {
        "install" => {
            if name.is_empty() {
                // Python 此处走 stdout（`print`），不是 stderr —— 照抄流向
                println!(
                    "ERROR: payload install 需要 <bundle>(可选: {})",
                    payload::bundle_names()
                );
                return 2;
            }
            payload::install_bundle(name, dest, source_root, force)
        }
        "dir" => {
            let bundle = if name.is_empty() { "mcp" } else { name };
            match payload::payload_dir_named(bundle) {
                Ok(p) => {
                    println!("{p}");
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "resource" => {
            if name.is_empty() {
                println!(
                    "ERROR: payload resource 需要 <name>\
                     (skills|board-role|board-role-zh|board-soul|board-soul-zh)"
                );
                return 2;
            }
            match payload::source_path(name, source_root) {
                Ok(p) => {
                    println!("{p}");
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "source" => match payload::resolve_source_root(source_root) {
            Ok((root, kind)) => {
                println!("{kind}\t{root}");
                0
            }
            Err(e) => {
                eprintln!("{e}");
                1
            }
        },
        _ => {
            println!("ERROR: 未知 payload 动作");
            2
        }
    }
}

// ════════════════════════════════════════════════════════════════════════════
// `--system-only`：L1 只激活/复用（SDK 反调 ABI，`cli/aimail:1076-1240`）
//   stdout **恰一行 JSON**（成功或 error+hint）；人话一律走 stderr；exit 0/1。
//   本函数**绝不**做平台接线、**绝不**部署桥（那属于人路径与 bootstrap machine-init）——
//   跳过它们才是打断 install↔plugin 调用环的关键。
// ════════════════════════════════════════════════════════════════════════════

/// 单行 JSON 错误信封（`_err`）：`{"success":false,"error":...}`（+ hint 同时进 stderr）。
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

/// `sid_from_system_home(home)`：平台指针文件（`{home}/<pointer_file>`）**优先**（权威），
/// 再退回扫描 `systems/*/` 的唯一匹配；零或多 ⇒ 空（**不猜**）。
fn sid_from_system_home(system_home: &std::path::Path) -> String {
    // 1) 指针优先
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
    // 2) 扫描唯一匹配
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
                    && crate::core::platforms::normalize_platform_home(std::path::Path::new(&sh))
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

fn ensure_system(a: &Args) -> i32 {
    use crate::core::config;
    let gw_url0 = resolve_gateway_url(&a.gateway_url, "").0;
    let _ = gw_url0;
    let mut sys_name = a.system_name.clone();
    let manager = if !a.manager.is_empty() {
        a.manager.clone()
    } else {
        config::env_val("AIMAIL_MANAGER_ADDRESS", "")
    };
    let mut prod_code = a.product_code.clone();
    let adm_key = a.admin_key.clone();
    let want_domain = if !a.domain.is_empty() {
        a.domain.clone()
    } else {
        config::env_val("AIMAIL_DOMAIN", "")
    };
    let mut sid = a.system_id.clone();

    // home ↔ sid 反查（SDK 反调通常只给 -H）
    let system_home: std::path::PathBuf = if a.home.is_empty() {
        if sid.is_empty() {
            return err_json(
                "--home is required (or --system-id backed by local config)",
                "",
            );
        }
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
    } else {
        crate::core::platforms::normalize_platform_home(std::path::Path::new(&a.home))
    };
    if !system_home.exists() {
        return err_json(
            &format!("home dir not found: {}", system_home.display()),
            "",
        );
    }

    // 复用优先：home 归属唯一系统 → 复用；.env 的码只在真新建时兜底
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

    // 凭据装配：复用分支回落本系统 cfg.gateway_url（#15）；stdout 是单行 JSON ⇒ 播报走 stderr
    let mut gw_url = a.gateway_url.clone();
    if !sid.is_empty() && prod_code.is_empty() {
        let (u, src) = resolve_gateway_url(&a.gateway_url, &sid);
        gw_url = u;
        if src == "prev" {
            eprintln!("gateway_url inherited from local config: {gw_url}");
        } else if src == "default" {
            eprintln!("gateway_url: no local value, falling back to default {gw_url}");
        }
    }
    let mut env: Vec<(String, String)> = Vec::new();
    env.push(("INTEGRATE_GATEWAY_URL".into(), gw_url.clone()));
    env.push(("INTEGRATE_SYSTEM_ID".into(), sid.clone()));
    env.push(("INTEGRATE_SYSTEM_NAME".into(), sys_name.clone()));
    env.push((
        "INTEGRATE_NAME_EXPLICIT".into(),
        if a.system_name.is_empty() {
            "false"
        } else {
            "true"
        }
        .into(),
    ));
    env.push(("INTEGRATE_MANAGER_ADDRESS".into(), manager.clone()));
    // F10：绑定写入点读 AIMAIL_MANAGER_ADDRESS（只导 INTEGRATE_* 会让绑定 manager 恒空）
    if !manager.is_empty() {
        env.push(("AIMAIL_MANAGER_ADDRESS".into(), manager.clone()));
    }
    // 新系统（产品码）路径：域属于产品 ⇒ 不替它转发机器级 .env 的域（否则共享域产品 409）
    let new_system_domain = if !prod_code.is_empty() {
        a.domain.clone()
    } else {
        want_domain.clone()
    };
    env.push(("INTEGRATE_AIMAIL_DOMAIN".into(), new_system_domain.clone()));
    let snaps = config::env_val("AIMAIL_SAVE_SNAPSHOTS", "yes").to_lowercase();
    env.push((
        "INTEGRATE_SAVE_SNAPSHOTS".into(),
        if matches!(snaps.as_str(), "yes" | "true" | "1") {
            "true"
        } else {
            "false"
        }
        .into(),
    ));
    env.push((
        "INTEGRATE_WEBHOOK_HOST".into(),
        config::env_val("AIMAIL_WEBHOOK_HOST", ""),
    ));
    // system_home 归属只在新建/显式凭据写；本地复用继承 prev
    if !prod_code.is_empty() {
        env.push(("INTEGRATE_USE_PRODUCT_CODE".into(), "true".into()));
        env.push(("INTEGRATE_PRODUCT_CODE".into(), prod_code.clone()));
        env.push((
            "INTEGRATE_SYSTEM_HOME".into(),
            system_home.to_string_lossy().to_string(),
        ));
    } else if !adm_key.is_empty() {
        env.push(("INTEGRATE_ADMIN_KEY".into(), adm_key.clone()));
        env.push((
            "INTEGRATE_SYSTEM_HOME".into(),
            system_home.to_string_lossy().to_string(),
        ));
    } else if !sid.is_empty() {
        let key = config::load_gateway_config(&sid)
            .map(|c| c.admin_key)
            .unwrap_or_default();
        if !key.is_empty() {
            env.push(("INTEGRATE_ADMIN_KEY".into(), key));
            eprintln!("reuse admin_key for {sid}");
        } else {
            return err_json(
                &format!("system {sid} has no local admin_key"),
                "pass -k <admin-key> or -c <product-code>",
            );
        }
    } else {
        return err_json(
            "no credential to activate with",
            "export AIMAIL_GW_URL + AIMAIL_PRODUCT_CODE then retry (or run bootstrap), or pass -c <code> / -k <admin-key>",
        );
    }

    // ── 激活/复用 worker（`setup_system.py` 的 Rust 复刻，**进程内**执行）──────────────
    // 为什么进程内而不是 spawn python：worker 是 CLI 自己的实现（激活唯一实现在 CLI），
    // rust 化后由本二进制承担；SDK 侧只保留 whoami/create_api_key/activate_system 这类
    // "签名 + HTTP 端点"调用（owner 2026-10-04 裁决：rust 原生实现，不新增 SDK 面）。
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
        webhook_host: config::env_val("AIMAIL_WEBHOOK_HOST", ""),
        system_home: system_home.to_string_lossy().to_string(),
    };
    let _ = &env; // env 仍按 Python 口径装配（日志/子进程契约），worker 用显式参数
    let data = crate::core::setup::setup(&wa);
    if !data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        // 照抄子进程契约的失败播报：worker 打印 `display`（缩进 JSON）+ `__ERROR__:<err>` 后 exit 1，
        // Python 侧 `check_output` 抛错 ⇒ `_err("system setup failed", stdout.strip()[-400:])`。
        // 进程内执行没有子进程，这里**重放**同一段字符串以保持 ABI 逐字一致。
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
        // Python 还会按 mtime 认领"刚写入的系统"（那是解析**子进程 stdout** 失败时的兜底）；
        // 进程内执行时 system_id 必然来自 worker 自身，故无此窗口逻辑可用 —— 直接报错。
        return err_json("setup finished without a system_id", "");
    }
    let cfg = config::load_gateway_config(&sid2);
    eprintln!("system configured: {sid2}");
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
// 人路径（`cli/aimail:836-1072`）：激活/复用 → domain 预置 → 桥（未移植，P2）→ 平台接线 → 路由（未移植）
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

/// domain 预置/创建（`cli/aimail:983-1004`）：只走显式/复用路径的域；产品码新建路径不在此。
fn ensure_domain(gw_url: &str, admin_key: &str, sid: &str, want_domain: &str) {
    let path = format!("/api/v1/admin/systems/{sid}/domains");
    let headers = crate::core::sig::signed_headers(admin_key, "GET", &path, None, "");
    let url = format!("{}{}", gw_url.trim_end_matches('/'), path);
    let (status, body) = crate::core::http::json_req(&url, &headers, None, Some("GET"), 15);
    if !(200..300).contains(&status) {
        warn(&format!("domain check failed: {body}"));
        return;
    }
    // Python 侧 `list_system_domains`：dict 里取 `data`，否则原值；非 list ⇒ []
    let list = match &body {
        serde_json::Value::Array(_) => Some(body.clone()),
        serde_json::Value::Object(o) => o.get("data").cloned().filter(|v| v.is_array()),
        _ => None,
    };
    let Some(list) = list else {
        warn(&format!("domain check failed: {body}"));
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
        ok(&format!("domain present: {want_domain}"));
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
        warn(format!("domain create failed: {err} {detail}").trim_end());
    } else {
        ok(&format!("domain created: {want_domain}"));
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
    let adm_key = a.admin_key.clone();
    let want_domain = if a.domain.is_empty() {
        config::env_val("AIMAIL_DOMAIN", "")
    } else {
        a.domain.clone()
    };
    let mut sid = a.system_id.clone();

    // 目标双向反查：--home 与 --system-id 任给其一
    let system_home = if a.home.is_empty() {
        if sid.is_empty() {
            return fail("install 需要 --home,或带 --system-id 以便从本地配置反查");
        }
        let sh = home::system_home_from_sid(&sid);
        if sh.is_empty() {
            return fail(&format!(
                "--system-id {sid} 无本地配置可反查 --home(系统不在这台机器?)"
            ));
        }
        let p = PathBuf::from(&sh);
        println!("  --home 由 --system-id 反查: {}", p.to_string_lossy());
        p
    } else {
        platforms::normalize_platform_home(&home::expand_user(&a.home))
    };
    if sid.is_empty() && prod_code.is_empty() && adm_key.is_empty() {
        // 重复安装：home 归属唯一系统 ⇒ 自动复用（.env 的码可能已被消耗）
        let sid2 = sid_from_system_home(system_home.as_path());
        if !sid2.is_empty() {
            sid = sid2;
            println!("  复用归属系统: {sid}(由 --home 反查)");
        }
    }
    if sid.is_empty() && prod_code.is_empty() && adm_key.is_empty() {
        prod_code = config::env_val("AIMAIL_PRODUCT_CODE", "");
        if sys_name.is_empty() {
            sys_name = config::env_val("AIMAIL_SYSTEM_NAME", "");
        }
    }
    if !system_home.exists() {
        return fail(&format!(
            "home 目录不存在: {}",
            system_home.to_string_lossy()
        ));
    }
    if !a.container_home.is_empty() && a.container.is_empty() {
        return fail("--container-home requires --container <name>");
    }
    let platform = match platforms::platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => {
            let p = platforms::resolve_platform(&system_home);
            if p.is_empty() {
                return fail(&format!(
                    "无法确定平台:{} 目录无特征且无 aimail 指针(可用 --platform 显式指定)",
                    system_home.to_string_lossy()
                ));
            }
            p
        }
        Err(msg) => return fail(&msg),
    };
    let mut line = format!(
        "  install platform={platform} system_home={} system_id={}",
        system_home.to_string_lossy(),
        if sid.is_empty() {
            "(新建)".to_string()
        } else {
            sid.clone()
        }
    );
    if !a.container.is_empty() {
        line.push_str(&format!(" container={}", a.container));
    }
    println!("{line}");

    // 激活/复用凭据装配（env 契约）
    let mut gw_url = a.gateway_url.clone();
    if !sid.is_empty() && prod_code.is_empty() {
        let (u, src) = resolve_gateway_url(&a.gateway_url, &sid);
        gw_url = u;
        if src == "prev" {
            ok(&format!(
                "gateway_url 继承本地配置: {gw_url}(复用 {sid};-g / AIMAIL_GW_URL 优先)"
            ));
        } else if src == "default" {
            warn(&format!(
                "gateway_url 无本地值, 落到默认 {gw_url}(未给 -g / AIMAIL_GW_URL)"
            ));
        }
    }
    let new_system_domain = if !prod_code.is_empty() {
        a.domain.clone()
    } else {
        want_domain.clone()
    };

    // 凭据三选一：产品码 / 显式 admin-key / 从既有配置复用
    let mut env_admin_key = String::new();
    if !prod_code.is_empty() {
        std::env::set_var("INTEGRATE_USE_PRODUCT_CODE", "true");
        std::env::set_var("INTEGRATE_PRODUCT_CODE", &prod_code);
        std::env::set_var(
            "INTEGRATE_SYSTEM_HOME",
            system_home.to_string_lossy().to_string(),
        );
    } else if !adm_key.is_empty() {
        env_admin_key = adm_key.clone();
        std::env::set_var("INTEGRATE_ADMIN_KEY", &adm_key);
        std::env::set_var(
            "INTEGRATE_SYSTEM_HOME",
            system_home.to_string_lossy().to_string(),
        );
    } else if !sid.is_empty() {
        if let Some(c) = config::load_gateway_config(&sid) {
            if !c.admin_key.is_empty() {
                env_admin_key = c.admin_key.clone();
                std::env::set_var("INTEGRATE_ADMIN_KEY", &c.admin_key);
                std::env::set_var("INTEGRATE_SYSTEM_ID", &sid);
            }
        }
    }
    std::env::set_var("INTEGRATE_GATEWAY_URL", &gw_url);
    std::env::set_var("INTEGRATE_SYSTEM_ID", &sid);
    std::env::set_var("INTEGRATE_SYSTEM_NAME", &sys_name);
    std::env::set_var(
        "INTEGRATE_NAME_EXPLICIT",
        if a.system_name.is_empty() {
            "false"
        } else {
            "true"
        },
    );
    std::env::set_var("INTEGRATE_MANAGER_ADDRESS", &manager);
    if !manager.is_empty() {
        std::env::set_var("AIMAIL_MANAGER_ADDRESS", &manager);
    }
    std::env::set_var("INTEGRATE_AIMAIL_DOMAIN", &new_system_domain);
    std::env::set_var(
        "INTEGRATE_SAVE_SNAPSHOTS",
        if matches!(
            config::env_val("AIMAIL_SAVE_SNAPSHOTS", "yes")
                .to_lowercase()
                .as_str(),
            "yes" | "true" | "1"
        ) {
            "true"
        } else {
            "false"
        },
    );
    std::env::set_var(
        "INTEGRATE_WEBHOOK_HOST",
        config::env_val("AIMAIL_WEBHOOK_HOST", ""),
    );

    // 激活/复用 worker（进程内；与 `--system-only` 同一条）
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
        webhook_host: config::env_val("AIMAIL_WEBHOOK_HOST", ""),
        system_home: system_home.to_string_lossy().to_string(),
    };
    let data = setup::setup(&wa);
    if !data
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        // 与子进程契约同构：worker 的 `__ERROR__:<text>` 就是失败原因本身
        let err = data
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("setup_system 失败");
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
        std::env::set_var("INTEGRATE_SYSTEM_ID", &sid2);
        ok(&format!("system_id: {sid2}"));
    }
    // 激活产物回填（新系统激活路径没有显式 -k）
    let mut cfg2 = config::load_gateway_config(&sid2);
    if let Some(c) = cfg2.take() {
        if !c.admin_key.is_empty() {
            env_admin_key = c.admin_key.clone();
            std::env::set_var("INTEGRATE_ADMIN_KEY", &c.admin_key);
        }
        if !c.domain.is_empty() {
            std::env::set_var("INTEGRATE_AIMAIL_DOMAIN", &c.domain);
        }
        cfg2 = Some(c);
    }

    // 1b) domain 预置/创建（只显式/复用路径）
    if !new_system_domain.is_empty() && !sid2.is_empty() {
        ensure_domain(&gw_url, &env_admin_key, &sid2, &want_domain);
    }

    // 2) 桥部署：本地网关直连不需要桥；远端网关 ⇒ 本机部署桥（`core::bridge_deploy::deploy`）
    if crate::core::gateway::is_local_gateway(&gw_url) {
        ok("bridge: local gateway (direct mode) -- no bridge needed");
    } else {
        // 远端网关：本机部署桥（二进制就位 → 公告判定 → 配置合并 → 起桥）
        let cfg_wh_host = config::load_gateway_config(&sid2)
            .map(|c| c.webhook_host.clone())
            .unwrap_or_default();
        let rc = crate::core::bridge_deploy::deploy(&crate::core::bridge_deploy::DeploySpec {
            gateway_url: gw_url.clone(),
            admin_key: env_admin_key.clone(),
            system_id: sid2.clone(),
            domain: std::env::var("INTEGRATE_AIMAIL_DOMAIN").unwrap_or_default(),
            webhook_mode_bridge: std::env::var("WEBHOOK_MODE").unwrap_or_default() != "push",
            announce_arg: String::new(), // Python 由 deploy_bridge 自己的 argv 读；CLI 侧无此参数
            cfg_webhook_host: cfg_wh_host,
        });
        if rc == 0 {
            ok("bridge deployed");
        } else {
            warn(&format!(
                "bridge deploy failed (exit {}) -- see the deploy_bridge output",
                rc
            ));
        }
    }

    // 3) 平台适配（注册表 install_steps 动作表驱动）
    let mut ctx: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
    let cfg_for_steps = cfg2
        .as_ref()
        .map(|c| serde_json::Value::Object(c.to_json()))
        .unwrap_or_else(|| {
            serde_json::json!({
                "system_id": std::env::var("INTEGRATE_SYSTEM_ID").unwrap_or_default(),
                "gateway_url": gw_url,
                "admin_key": env_admin_key,
                "domain": "",
                "system_name": sys_name,
                "manager_address": manager,
                "system_home": std::env::var("INTEGRATE_SYSTEM_HOME").unwrap_or_default(),
            })
        });
    ctx.insert(
        "home".into(),
        serde_json::json!(system_home.to_string_lossy().to_string()),
    );
    ctx.insert("sid".into(), serde_json::json!(sid2));
    let _py = crate::core::sdkroot::probe_in_domain(&a.container, &system_home.to_string_lossy());
    let _pkgmgr = crate::core::sdkroot::probe_pkgmgr(&a.container, &_py);
    ctx.insert("pkgmgr".into(), serde_json::json!(_pkgmgr));
    let core_dir = crate::core::sdkroot::resolve_with(&_py)
        .map(|r| r.path)
        .unwrap_or_default();
    ctx.insert("python".into(), serde_json::json!(_py));
    ctx.insert("manager".into(), serde_json::json!(manager));
    ctx.insert(
        "scripts".into(),
        serde_json::json!(crate::core::sdkroot::resolve()
            .map(|r| r.path)
            .unwrap_or_default()
            .to_string_lossy()
            .to_string()),
    );
    // `{sdk}` 必须走 SDK 根的**单真源**（repo→pip 两形态）。设备形态（安装物只有二进制）下
    // 仓库 pysdk/ 不存在 ⇒ 必须回退到已安装的 aimail 包目录，否则 install_steps 里的
    // `{sdk}/…/*.sh` 会展开成不存在的 repo 路径（实测 2026-10-05：deer-flow install-skill.sh exit 127）。
    let sdk_root = match crate::core::sdkroot::resolve() {
        Ok(r) => r.path.to_string_lossy().to_string(),
        Err(e) => {
            eprintln!("  ✗ {e}");
            core_dir.to_string_lossy().to_string()
        }
    };
    ctx.insert("sdk".into(), serde_json::json!(sdk_root));
    ctx.insert("cfg".into(), cfg_for_steps);
    ctx.insert("all_agents".into(), serde_json::json!(a.all_agents));
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

    // 系统级 cfg 记录：`platform`（transport 分派真源 —— 契约 §4.1(2) 的 node/python 选择；
    // 该文件 CLI 唯一写，见契约 §2）+ 显式 `--container` 时的 runtime 记录（幂等覆写同值）。
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

    // 路由侧：每地址的桥路由在平台步之后确保（桥未移植 ⇒ 明确告警）
    // 路由对账（`_ensure_inbound_routes`）：best-effort，永不改 rc
    crate::core::bridge_wire::ensure_inbound_routes(&sid2);

    let tail = if a.system_id.is_empty() {
        String::new()
    } else {
        format!("--system-id {}", a.system_id)
    };
    println!(
        "{}",
        format!("  install done. 建议: aimail check {tail}").trim_end()
    );
    0
}
