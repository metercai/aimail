//! `aimail install` —— 集成入口。本切片（S6 切片1）只落**机器面分派 + `--payload` 通道**：
//!
//! · `--system-only` / `--payload` 的**互斥与拒参**（`cli/aimail:784-827` 逐字复刻：文案、流向、
//!   rc=2；其中 payload 通道的"未知动作/缺 bundle"文案走 **stdout**，拒参走 stderr —— 与 Python 一致）；
//! · `--payload dir|source|resource` 三个**只读**动作（路径解析，可跨语言逐字比对）；
//! · `--payload install`（写载荷，含 stamp/清理）与 `--system-only`（L1 激活/复用，单行 JSON ABI）
//!   以及人路径**一律未移植**：明确的非零退出 + 说明，绝不静默当成功、绝不降级成人路径。
//!
//! 未移植清单在 `cli/tests/cli_surface.rs::unported_faces_are_honest` 里钉住。

use crate::cmd::stub::not_yet_ported;
use crate::core::payload;

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
    not_yet_ported("install (human path: activation / platform wiring / runtime resources)")
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
    let v = crate::core::config::env_val("AIMAIL_URL", "");
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

/// `sid_from_system_home(home)`：平台指针 `{home}/.agentmail` **优先**（权威），
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
    env.push(("INTEGRATE_AIMAIL_DOMAIN".into(), new_system_domain));
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
            "export AIMAIL_URL + AIMAIL_PRODUCT_CODE then retry (or run bootstrap), or pass -c <code> / -k <admin-key>",
        );
    }

    let _ = env;
    not_yet_ported("install --system-only activation worker (setup_system: platform wiring-free L1 activation/reuse)")
}
