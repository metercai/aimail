//! `aimail bridge` —— `cli/aimail:2682-2808` 的 Rust 复刻（**status / 按系统重刷路由**）。
//!
//! `--restart` / `--upgrade` 依赖"部署面"（`deploy_bridge`：二进制就位 + 起进程 + zip 升级），
//! 属 P2 切片3 ⇒ 此处**响亮未移植**（rc=1），绝不静默当成功。
//!
//! 口径（照抄 Python）：
//! · 无 `-s`/`-H` ⇒ 状态查看（进程 / 配置 / 路由表 / 日志新鲜度）；
//! · 有 `-s`/`-H` ⇒ 重刷该系统的转发路由：**目标优先取绑定 webhook_url**（唯一信任源），
//!   回退历史路由表、再回退平台默认；`host` 传**完整 URL**（含 `://`）或裸 `host:port` 两字段，
//!   由 `target_to_route_fields` 决定（传错会生成不可投递目标 —— 2026-09-21 实测）；
//! · 桥刷新**直接打固定 admin 地址** `127.0.0.1:38081`（与 `sync_route` 的"声明式"取址不同，照抄）。

use serde_json::json;

use crate::cmd::report::{fail, ok, warn};
use crate::core::bridge_wire::{
    bridge_cfg_file, bridge_log_file, bridge_pids, read_routes, routes_file, system_agents,
    target_to_route_fields, BRIDGE_ADDR,
};

pub struct Args {
    pub system_id: String,
    pub home: String,
    pub restart: bool,
    pub upgrade: bool,
    pub platform: String,
}

pub fn run(a: &Args) -> i32 {
    let pids = bridge_pids();
    let running = !pids.is_empty();

    if a.upgrade {
        return crate::cmd::stub::not_yet_ported(
            "bridge --upgrade（部署面：zip 校验 → 契约停机 → 原子替换 → 重启；P2 切片3）",
        );
    }
    if a.restart {
        return crate::cmd::stub::not_yet_ported(
            "bridge --restart（部署面：起进程/写 pid；P2 切片3）",
        );
    }

    if a.system_id.is_empty() && a.home.is_empty() {
        return status(&pids);
    }
    refresh(a, running)
}

/// 状态查看（无 `-s`/`-H`）。
fn status(pids: &[u32]) -> i32 {
    println!("  bridge 状态:");
    if !pids.is_empty() {
        let list: Vec<String> = pids.iter().map(|p| p.to_string()).collect();
        ok(&format!("进程: {} 个 ({})", pids.len(), list.join(", ")));
    } else {
        warn("进程: 未运行");
    }

    let cfg = bridge_cfg_file();
    if cfg.is_file() {
        match std::fs::read_to_string(&cfg)
            .ok()
            .and_then(|t| t.parse::<toml::Value>().ok())
        {
            Some(td) => {
                let mode = td.get("mode").and_then(|v| v.as_str()).unwrap_or("?");
                // 配置里的键是 `bind`（历史兼容 `addr`）—— 旧代码读 addr 恒为 `?`
                let bind = td
                    .get("bind")
                    .or_else(|| td.get("addr"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("?");
                let sys_ids: Vec<String> = td
                    .get("pull")
                    .and_then(|p| p.get("systems"))
                    .and_then(|s| s.as_array())
                    .map(|arr| {
                        arr.iter()
                            .map(|s| {
                                let sid =
                                    s.get("system_id").and_then(|v| v.as_str()).unwrap_or("?");
                                let n = sid.chars().count();
                                sid.chars().skip(n.saturating_sub(12)).collect::<String>()
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                ok(&format!(
                    "配置: mode={} addr={} systems={:?}",
                    mode, bind, sys_ids
                ));
            }
            None => warn("配置读取失败: invalid TOML"),
        }
    } else {
        warn("配置: aimail_bridge.toml 不存在");
    }

    let routes = read_routes(&routes_file());
    if !routes.is_empty() {
        ok(&format!("路由: {} 条", routes.len()));
        for (email, target) in &routes {
            println!("       {} → {}", email, target);
        }
    } else {
        warn("路由表为空(aimail_routes.toml)");
    }

    let log = bridge_log_file();
    if let Ok(md) = std::fs::metadata(&log) {
        if let Ok(secs) = md
            .modified()
            .map(|t| t.elapsed().map(|d| d.as_secs()).unwrap_or(0))
        {
            ok(&format!("日志: {}s 前更新 ({})", secs, log.display()));
        }
    }
    0
}

/// 重刷转发路由（有 `-s`/`-H`）。
fn refresh(a: &Args, running: bool) -> i32 {
    use crate::core::platforms::{normalize_platform_home, platform_override, resolve_platform};

    let system_home = if a.home.is_empty() {
        std::path::PathBuf::new()
    } else {
        normalize_platform_home(&crate::core::home::expand_user(&a.home))
    };
    let (sid, platform0) = crate::cmd::uninstall::resolve_system_id(&system_home, &a.system_id);
    if sid.is_empty() {
        return fail("无法确定 system_id(用 --system-id 或 --home 指定)");
    }
    let platform = match platform_override(&a.platform) {
        Ok(Some(p)) => p,
        Ok(None) => {
            if !platform0.is_empty() {
                platform0
            } else {
                resolve_platform(&system_home)
            }
        }
        Err(msg) => return fail(&msg),
    };
    if platform.is_empty() {
        return fail(&format!("无法确定平台(system {})", sid));
    }

    let cfg_path = crate::core::config::gateway_config_path(&sid);
    if !cfg_path.is_file() {
        return fail(&format!("系统配置不存在: {}", cfg_path.display()));
    }
    if !running {
        warn("bridge 未运行——仅更新本地路由表,不会热加载(先 --restart)");
    }

    let routes = read_routes(&routes_file());
    let mut refreshed = 0usize;
    let mut missing = 0usize;
    for (email, target) in system_agents(&sid) {
        let old = routes.get(&email).cloned();
        let (host, port) = target_to_route_fields(&target);
        // host 传完整 URL 时桥走 from_url（URL 保真）；port 仅为过必填校验
        let body = json!({"email": email, "host": host, "port": port});
        let (okcall, detail) = crate::core::http::raw_req_method(
            "POST",
            &format!("http://{}/api/v1/routes", BRIDGE_ADDR),
            Some(body.to_string().as_bytes()),
            Some("application/json"),
            5,
        )
        .map(|(code, _)| ((200..300).contains(&code), format!("HTTP {}", code)))
        .unwrap_or_else(|e| (false, e));
        if okcall {
            refreshed += 1;
            if old.as_deref() != Some(target.as_str()) {
                println!(
                    "  ↻ {}: {} → {}",
                    email,
                    old.as_deref().unwrap_or("(无)"),
                    target
                );
            } else {
                println!("  ✓ {}: {}(unchanged)", email, target);
            }
        } else {
            missing += 1;
            warn(&format!("  ✗ {} 路由注册失败: {}", email, detail));
        }
    }

    if refreshed > 0 {
        ok(&format!("重刷完成: {} 条路由已注册/确认", refreshed));
    }
    if missing > 0 {
        warn(&format!("{} 条失败(检查 bridge 是否运行)", missing));
        return 1;
    }
    0
}
