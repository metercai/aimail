//! `aimail reset` —— `cli/aimail:1241-1348` 的 Rust 复刻。
//!
//! 语义：**只走 admin-key 路径**，重置本机当前实际配置（不重激活、不碰云端 key）。
//! 顺序：home(可反查) → 平台判定 → sid(可指针反查) → 读 admin-key(只读) → 读 prev 取默认
//! → **进程内跑激活 worker**（复用 `core::setup`，与 `--system-only` 同一条）→ 注册链幂等重跑
//! （尽力语义：失败只告警不中断）→ 路由对账（**桥未移植 ⇒ 明确告警**）→ 建议行。

use serde_json::{json, Value};

use crate::core::{config, platforms, register, setup, style};

pub struct Args {
    pub home: String,
    pub system_id: String,
    pub platform: String,
    pub gateway_url: String,
    pub system_name: String,
    pub manager: String,
    pub all_agents: bool,
}

fn ok(msg: &str) {
    println!("  {}{}{} {}", style::GREEN, style::CHECK, style::NC, msg);
}

fn fail(msg: &str) -> i32 {
    println!("  {}{}{} {}", style::RED, style::CROSS, style::NC, msg);
    1
}

fn warn(msg: &str) {
    println!("  {}{}{} {}", style::YELLOW, style::CROSS, style::NC, msg);
}

pub fn run(a: &Args) -> i32 {
    let mut sid = a.system_id.clone();
    // home：未给则按 --system-id 反查本地配置（空判按真值，别用 Path 的 truthiness）
    let system_home = if a.home.is_empty() {
        if sid.is_empty() {
            return fail("reset 需要 --home(系统 home/平台根),或带 --system-id 以便反查");
        }
        let sh = crate::core::home::system_home_from_sid(&sid);
        if sh.is_empty() {
            return fail(&format!(
                "--system-id {sid} 无本地配置可反查 --home(系统不在这台机器?)"
            ));
        }
        let p = std::path::PathBuf::from(&sh);
        println!("  --home 由 --system-id 反查: {}", p.to_string_lossy());
        p
    } else {
        platforms::normalize_platform_home(&crate::core::home::expand_user(&a.home))
    };
    if !system_home.exists() {
        return fail(&format!(
            "home 目录不存在: {}",
            system_home.to_string_lossy()
        ));
    }
    // --platform 显式覆盖（注册表校验）优先，否则按 home 特征判定
    let platform = match platforms::platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => platforms::resolve_platform(&system_home),
        Err(msg) => return fail(&msg),
    };
    if platform.is_empty() {
        return fail("无法确定平台");
    }
    if sid.is_empty() {
        sid = platforms::pointer_sid_for(&crate::core::home::user_home(), &platform);
    }
    if sid.is_empty() {
        return fail("无法确定 system_id(用 --system-id 指定)");
    }

    // admin-key：只读（系统层原始 key 文件优先，其次已有配置）
    let raw_key_path = config::systems_root_in(&crate::core::home::aimail_home())
        .join(&sid)
        .join(setup::SYSTEM_RAW_KEY_FILE);
    let mut admin_key = std::fs::read_to_string(&raw_key_path)
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if admin_key.is_empty() {
        if let Some(c) = config::load_gateway_config(&sid) {
            admin_key = c.admin_key;
        }
    }
    if admin_key.is_empty() {
        return fail(&format!(
            "无 admin-key({} 或配置)——reset 只走 admin-key 路径",
            raw_key_path.to_string_lossy()
        ));
    }

    // 默认参数：CLI 显式覆盖 > prev（system_name 只认显式 + prev，不取 .env：身份字段）
    let prev = config::load_gateway_config(&sid);
    let pget = |k: &str| -> String {
        prev.as_ref()
            .and_then(|c| match k {
                "gateway_url" => Some(c.gateway_url.clone()),
                "system_name" => Some(c.system_name.clone()),
                "manager_address" => Some(c.manager_address.clone()),
                "system_home" => Some(c.system_home.clone()),
                _ => None,
            })
            .unwrap_or_default()
    };
    let gw_url = if !a.gateway_url.is_empty() {
        a.gateway_url.clone()
    } else {
        let e = config::env_val("AIMAIL_URL", "");
        if !e.is_empty() {
            e
        } else {
            pget("gateway_url")
        }
    };
    let sys_name = if !a.system_name.is_empty() {
        a.system_name.clone()
    } else {
        pget("system_name")
    };
    let manager = if !a.manager.is_empty() {
        a.manager.clone()
    } else {
        let e = config::env_val("AIMAIL_MANAGER_ADDRESS", "");
        if !e.is_empty() {
            e
        } else {
            pget("manager_address")
        }
    };

    println!("  reset platform={platform} system_id={sid}");
    // 与 Python 同：把 INTEGRATE_* 契约灌进进程环境后跑 worker（worker 按该契约取值）
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
    std::env::set_var(
        "INTEGRATE_SYSTEM_HOME",
        if system_home.to_string_lossy().is_empty() {
            pget("system_home")
        } else {
            system_home.to_string_lossy().to_string()
        },
    );
    std::env::set_var("INTEGRATE_MANAGER_ADDRESS", &manager);
    if !manager.is_empty() {
        std::env::set_var("AIMAIL_MANAGER_ADDRESS", &manager);
    }
    std::env::set_var("INTEGRATE_ADMIN_KEY", &admin_key);

    let wa = setup::SetupArgs {
        gateway_url: gw_url.clone(),
        system_id: sid.clone(),
        admin_key: admin_key.clone(),
        product_code: String::new(),
        system_name: sys_name.clone(),
        domain: config::env_val("AIMAIL_DOMAIN", ""),
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
    let r = setup::setup(&wa);
    if !r.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
        return fail("setup_system(reset) 失败");
    }
    ok("config parameters reset (admin-key path; least-privilege key re-derived)");

    // 注册链幂等重跑（注册表驱动；尽力语义：失败只告警，不阻断 reset）
    let core_dir = crate::core::sdkroot::resolve_or_repo_candidate().path;
    let cfg: Value = config::load_gateway_config(&sid)
        .map(|c| Value::Object(c.to_json()))
        .unwrap_or_else(|| json!({}));
    let home_str = system_home.to_string_lossy().to_string();
    if a.all_agents {
        if let Err(e) = register::register_all(&platform, &cfg, &manager, &home_str, &core_dir) {
            warn(&format!(
                "registration chain re-run: registration failed (see above); 异常: {e}"
            ));
        }
    } else {
        let agent = register::default_agent_name(&platform);
        if let Err(e) = register::register_agent(&platform, &agent, &cfg, &manager, &home_str, &core_dir, 
        ) {
            warn(&format!(
                "registration chain re-run: registration failed (see above); 异常: {e}"
            ));
        } else {
            ok("registration chain re-run (idempotent)");
        }
    }
    // 路由对账：`_ensure_inbound_routes` 等价物（best-effort，永不改 rc）
    crate::core::bridge_wire::ensure_inbound_routes(&sid);
    println!("  建议: aimail check --system-id {sid}");
    0
}
