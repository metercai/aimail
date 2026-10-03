//! `aimail stats` —— 本机 aimail 对接状态（只读）。
//!
//! 逐字复刻 Python 侧 `cli/aimail:3084-3237`：默认视图（系统行 + agent 行）与
//! `-a/--all` 全面视图（健康标注 / 断链系统段 / 本机平台段 / 维护链路提示）。
//!
//! 云状态分档（与 Python 完全同判据，注意 `_request` 失败返回 `status:0` 而**不抛**）：
//! `200 + expires_at` ⇒ ok 并给到期行；`403/404` ⇒ unlinked；无 url/key ⇒ broken-config。
//! 只在"到期值不可解析"这类异常上与 Python 一致地落到 `unreachable` 分支。

use crate::core::style::{GREEN, NC, RED, YELLOW};
use crate::core::{config, contract, gateway::GatewayClient, home, mail, platforms, time};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// 一个 agent 行（`stats` 两段视图共用）。
struct AgentRow {
    email: String,
    manager: String,
    received: u64,
    size: u64,
}

/// `sorted(systems/*)` 里**带系统级环境文件**的目录（`(sid, dir)`）。
fn system_dirs(systems_root: &Path) -> Vec<(String, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(systems_root) else {
        return Vec::new();
    };
    let mut out: Vec<(String, PathBuf)> = entries
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter(|e| e.path().join(config::GATEWAY_CONFIG_NAME).is_file())
        .map(|e| (e.file_name().to_string_lossy().into_owned(), e.path()))
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0)); // 与 Python `sorted(...)` 同序
    out
}

fn binding_rows(aimail_home: &Path, dir: &Path, gw: &config::GatewayConfig) -> Vec<AgentRow> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut subs: Vec<PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    let mut rows = Vec::new();
    for sub in subs {
        let path = sub.join(contract::binding_file());
        if !path.is_file() {
            continue; // 权限/IO 错误也算"没有"（Python `_is_readable_file` 同）
        }
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let email = value
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if email.is_empty() {
            continue;
        }
        let manager = value
            .get("manager_address")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .unwrap_or(&gw.manager_address)
            .to_string();
        let (received, size) = mail::mail_stats(aimail_home, &email);
        rows.push(AgentRow {
            email,
            manager,
            received,
            size,
        });
    }
    rows
}

/// 平台标签：`""` 或 `unknown` ⇒ 视图相关的兜底字面量（系统段 `unknown`、agent 段 `?`）。
fn platform_label(home: &str, fallback: &str) -> String {
    if home.is_empty() {
        return fallback.to_string();
    }
    let p = platforms::detect_platform_from_home(Path::new(home));
    if p.is_empty() || p == "unknown" {
        fallback.to_string()
    } else {
        p.to_string()
    }
}

/// 返回 `(expiry_line, cloud_status)`；`Err(())` = 落到 Python 的 `except` 分支。
fn quota_probe(gw: &config::GatewayConfig, sid: &str) -> Result<(String, String), ()> {
    let mut cloud_status = "ok".to_string();
    if !gw.gateway_url.is_empty() && !gw.admin_key.is_empty() {
        let client = GatewayClient::with_defaults(&gw.gateway_url, &gw.admin_key);
        let qr = client.get("/api/v1/quotas");
        let status = qr.get("status").and_then(Value::as_u64).unwrap_or(0);
        if status == 200 {
            if qr.get("expires_at").is_some() {
                let raw = qr.get("expires_at").and_then(Value::as_str).ok_or(())?;
                let expires = time::parse_rfc3339_secs(raw).ok_or(())?;
                let days = time::days_until(expires, time::now_secs());
                let mark = if days <= 3 {
                    format!("  {YELLOW}⚠ EXPIRES in {days}d{NC}")
                } else {
                    format!("  (expires in {days}d)")
                };
                let date: String = raw.chars().take(10).collect();
                return Ok((format!("   expires: {date}{mark}"), cloud_status));
            }
        } else if status == 403 || status == 404 {
            cloud_status = "unlinked".to_string();
            return Ok((
                format!(
                    "   {RED}EXPIRED/SUSPENDED{NC} — renew: aimail renew --system-id {sid} \
                     --code <activation-code>"
                ),
                cloud_status,
            ));
        }
    }
    if gw.gateway_url.is_empty() || gw.admin_key.is_empty() {
        cloud_status = "broken-config".to_string();
    }
    Ok((String::new(), cloud_status))
}

pub fn run(all_view: bool) -> i32 {
    let aimail_home = home::aimail_home();
    let user_home = home::user_home();
    let dirs = system_dirs(&config::systems_root_in(&aimail_home));
    if dirs.is_empty() {
        println!("  no aimail systems configured on this machine");
        return 0;
    }

    println!("  Systems installed:");
    let mut total_agents = 0usize;
    let mut broken: Vec<(String, String)> = Vec::new();
    let ptr_map = if all_view {
        platforms::all_pointer_sids(&user_home)
    } else {
        Vec::new()
    };

    for (sid, dir) in &dirs {
        let gw = config::load_gateway_config_in(&aimail_home, sid).unwrap_or_default();
        let platform = platform_label(&gw.system_home, "unknown");
        let agents = binding_rows(&aimail_home, dir, &gw);
        total_agents += agents.len();

        let (expiry_line, cloud_status) = match quota_probe(&gw, sid) {
            Ok(v) => v,
            Err(()) => (
                "   expires: (query failed)".to_string(),
                "unreachable".to_string(),
            ),
        };
        println!(
            "    {sid}   [{platform}]   agents: {}{expiry_line}",
            agents.len()
        );

        if all_view {
            let home_tag = if gw.system_home.is_empty() {
                format!("{RED}home-missing{NC}")
            } else if !Path::new(&gw.system_home).is_dir() {
                format!("{RED}home-dir-missing({}){NC}", gw.system_home)
            } else if platform == "unknown" {
                format!("{YELLOW}home-no-platform-signature{NC}")
            } else {
                format!("{GREEN}home-ok{NC}")
            };
            let ptrs: &[&str] = ptr_map
                .iter()
                .find(|(s, _)| s == sid)
                .map(|(_, v)| v.as_slice())
                .unwrap_or(&[]);
            let ptr_tag = if ptrs.is_empty() {
                format!("{YELLOW}pointer-none{NC}")
            } else {
                format!("{GREEN}pointer:{}{NC}", ptrs.join(","))
            };
            println!("        health: {home_tag} · {ptr_tag} · cloud:{cloud_status}");
            if cloud_status == "unlinked" || cloud_status == "broken-config" {
                broken.push((sid.clone(), cloud_status.clone()));
            }
        }
    }

    println!("  Agents ({total_agents}):");
    for (_, dir) in &dirs {
        let sid = dir
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let gw = config::load_gateway_config_in(&aimail_home, &sid).unwrap_or_default();
        let platform = platform_label(&gw.system_home, "?");
        for row in binding_rows(&aimail_home, dir, &gw) {
            println!("    {}   [{platform}]", row.email);
            let mgr = if row.manager.is_empty() {
                "-"
            } else {
                row.manager.as_str()
            };
            println!(
                "        received: {} emails · storage: {} · manager: {mgr}",
                row.received,
                mail::fmt_size(row.size)
            );
        }
    }

    if all_view {
        println!();
        if broken.is_empty() {
            println!("  Broken / unlinked systems: none");
        } else {
            println!("  Broken / unlinked systems:");
            for (bsid, why) in &broken {
                println!("    {bsid}   ({why})");
            }
            println!(
                "    → check: aimail check --system-id <sid> · repair: aimail repair --system-id <sid>"
            );
        }

        println!();
        println!("  Platforms on this machine:");
        let mut linked_any = false;
        for plat in platforms::order() {
            let exists = platforms::platform_root_exists(&user_home, plat);
            let ptr_sid = platforms::pointer_sid_for(&user_home, plat);
            let root = platforms::platform_root(&user_home, plat);
            let shown = root.display();
            if exists && !ptr_sid.is_empty() {
                println!("    {plat:<9} {shown}   linked → {ptr_sid}");
                linked_any = true;
            } else if exists {
                println!(
                    "    {plat:<9} {shown}   {YELLOW}installed, not linked{NC} — aimail install \
                     --home {shown}"
                );
            } else {
                println!("    {plat:<9} — not installed");
            }
        }
        if !linked_any {
            println!("    {YELLOW}no platform is linked to any aimail system{NC}");
        }

        println!();
        println!("  Maintenance: stats → check → repair");
        println!(
            "    aimail check --system-id <sid>        # full health exam (config/runtime/links)"
        );
        println!(
            "    aimail repair --system-id <sid> [--home <root>]   # fix per check findings \
             (hint-only items are reported, not forced)"
        );
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    fn write_system(home: &Path, sid: &str, system_home: &str) {
        let mut cfg = config::GatewayConfig {
            gateway_url: "http://127.0.0.1:1".into(), // 必然连不上 ⇒ status 0
            admin_key: "k".into(),
            system_id: sid.into(),
            system_home: system_home.into(),
            ..Default::default()
        };
        cfg.system_name = sid.into();
        config::save_gateway_config_in(home, sid, &cfg).unwrap();
    }

    #[test]
    fn empty_machine_prints_the_single_line() {
        let tmp = TempDir::new("stats-empty");
        let dirs = system_dirs(&config::systems_root_in(tmp.path()));
        assert!(dirs.is_empty());
    }

    #[test]
    fn system_dirs_require_the_gateway_file() {
        let tmp = TempDir::new("stats-dirs");
        let home = tmp.path();
        std::fs::create_dir_all(config::systems_root_in(home).join("orphan")).unwrap();
        assert!(system_dirs(&config::systems_root_in(home)).is_empty());
        write_system(home, "sid1", "");
        let dirs = system_dirs(&config::systems_root_in(home));
        assert_eq!(dirs.len(), 1);
        assert_eq!(dirs[0].0, "sid1");
    }

    #[test]
    fn probe_without_credentials_is_broken_config_and_no_network() {
        let gw = config::GatewayConfig {
            system_id: "s".into(),
            ..Default::default()
        };
        let (line, status) = quota_probe(&gw, "s").expect("not the exception branch");
        assert!(line.is_empty());
        assert_eq!(status, "broken-config");
    }

    #[test]
    fn probe_on_dead_gateway_keeps_ok_like_python_status_zero() {
        // 有 url+key 但没人监听 ⇒ _request 返回 status 0 ⇒ Python 侧 cloud 仍为 ok、
        // 且不打印到期行（这是复刻的关键语义，别"顺手"改成 unreachable）
        let gw = config::GatewayConfig {
            gateway_url: "http://127.0.0.1:1".into(),
            admin_key: "k".into(),
            ..Default::default()
        };
        let (line, status) = quota_probe(&gw, "s").expect("not the exception branch");
        assert!(line.is_empty(), "{line}");
        assert_eq!(status, "ok");
    }

    #[test]
    fn platform_label_falls_back_without_guessing() {
        assert_eq!(platform_label("", "unknown"), "unknown");
        assert_eq!(platform_label("/tmp/definitely-not-a-platform", "?"), "?");
    }
}
