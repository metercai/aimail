//! `aimail address` —— 地址查看/维护。S3 只移植**查看面**（`list` / `show`）。
//!
//! 维护面（`-d/--default`、`set-name`、`set-manager`、隐藏的 `--inbound-live|down`）
//! 保留命令面但一律走 [`crate::cmd::stub::not_yet_ported`]（rc 1）—— **绝不**静默
//! 落成 `list`：Python 侧 `cli/aimail:2045-2065` 有亲笔记录的两起静默降级事故
//! （2026-09-25 未登记 flag 落 list；2026-09-29 `-e` 单独出现时列表掩盖"目标不存在"），
//! op 顺序照抄即为此。
//!
//! 现状码对照：`cli/aimail:1355-1381`（平台枚举）· `:1384-1477`（`_list_agents`）·
//! `:1480-1514`（指针扫描）· `:1974-2002`（表头/表体）· `:2030-2109`（op 链与查看面）。

use crate::cmd::report;
use crate::core::{config, contract, gateway::GatewayClient, home, platforms};
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Args {
    pub system_id: String,
    pub default: Option<String>,
    pub agent: Option<String>,
    pub email: Option<String>,
    pub name: Option<String>,
    pub manager: Option<String>,
    pub inbound_live: bool,
    pub inbound_down: bool,
}

pub(crate) struct Row {
    pub(crate) agent: String,
    pub(crate) email: String,
    pub(crate) manager: String,
    pub(crate) webhook: String,
    pub(crate) registered: bool,
    pub(crate) platform: String,
}

/// 地址 → 目录键反查归属系统（`cli/aimail:2005-2027`）。
fn sid_for_address(aimail_home: &Path, email: &str) -> String {
    let want = email.trim().to_lowercase();
    if want.is_empty() {
        return String::new();
    }
    let Ok(systems) = std::fs::read_dir(config::systems_root_in(aimail_home)) else {
        return String::new();
    };
    let mut dirs: Vec<PathBuf> = systems
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    for dir in dirs {
        for (_, agent_email, _) in pointers_in(&dir) {
            if agent_email.trim().to_lowercase() == want {
                return dir
                    .file_name()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
            }
        }
    }
    String::new()
}

/// 系统目录下每个 agent 目录里的绑定文件 → `(agent_name, email, sid)`。
fn pointers_in(dir: &Path) -> Vec<(String, String, String)> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut subs: Vec<PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    let mut out = Vec::new();
    for sub in subs {
        let path = sub.join(contract::binding_file());
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
        let sid = value
            .get("system_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !email.is_empty() {
            out.push((String::new(), email, sid));
        }
    }
    out
}

/// 期望地址的 local part 归一（`cli/aimail:1463` 的同字符集：atext-**no-dot**；
/// 清空则落 `agent`）。注意与目录名归一（`[^\w.\-]`）**不是**同一套字符集。
fn local_part_sanitize(name: &str) -> String {
    const EXTRA: &str = "!#$%&'*+-/=?^_`{|}~";
    let s: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || EXTRA.contains(c) {
                c
            } else {
                '_'
            }
        })
        .collect();
    if s.is_empty() {
        "agent".to_string()
    } else {
        s
    }
}

pub(crate) fn list_agents(aimail_home: &Path, sid: &str, cfg: &config::GatewayConfig) -> Vec<Row> {
    let mut rows: Vec<Row> = Vec::new();
    let base = config::system_dir_in(aimail_home, sid);
    if base.is_dir() {
        let mut entries: Vec<PathBuf> = std::fs::read_dir(&base)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect();
        entries.sort();
        for dir in entries {
            let jf = dir.join(contract::binding_file());
            if !jf.is_file() {
                continue;
            }
            let Ok(text) = std::fs::read_to_string(&jf) else {
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
            // agent 名 = 平台指针所在目录名（指针扫 cfg.system_home / 绑定里的 home）
            let mut agent_name = String::new();
            for key in [
                cfg.system_home.as_str(),
                value
                    .get("system_home")
                    .and_then(Value::as_str)
                    .unwrap_or(""),
                value.get("home").and_then(Value::as_str).unwrap_or(""),
            ] {
                if key.is_empty() || !Path::new(key).is_dir() {
                    continue;
                }
                for (nm, ptr_email, ptr_sid) in platforms::scan_pointers(Path::new(key)) {
                    if ptr_email == email && ptr_sid == sid {
                        agent_name = nm;
                        break;
                    }
                }
                if !agent_name.is_empty() {
                    break;
                }
            }
            if agent_name.is_empty() {
                let local = email.split('@').next().unwrap_or("").to_string();
                agent_name = local.split('.').next().unwrap_or("").to_string();
            }
            rows.push(Row {
                agent: agent_name,
                email,
                manager: value
                    .get("manager_address")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                webhook: value
                    .get("webhook_url")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                registered: true,
                platform: String::new(),
            });
        }
    }

    // 平台枚举：未注册的 agent（新增场景检测）
    let platform = if cfg.system_home.is_empty() {
        String::new()
    } else {
        platforms::detect_platform_from_home(Path::new(&cfg.system_home)).to_string()
    };
    if !cfg.system_home.is_empty() {
        let home_path = Path::new(&cfg.system_home);
        let mut known: Vec<String> = rows.iter().map(|r| r.agent.clone()).collect();
        // pointer-first：已带本系统指针的 profile/agent 目录按其指针邮箱登记（改名场景）
        for (nm, pe, psid) in platforms::scan_pointers(home_path) {
            if psid != sid || nm.is_empty() {
                continue;
            }
            if !known.contains(&nm) && !rows.iter().any(|r| r.agent == nm) {
                rows.push(Row {
                    agent: nm.clone(),
                    email: pe,
                    manager: String::new(),
                    webhook: String::new(),
                    registered: true,
                    platform: platform.clone(),
                });
                known.push(nm);
            }
        }
        let aliases = platforms::aliases(&platform);
        for name in platforms::agent_names(home_path, &platform) {
            if known.contains(&name) {
                continue;
            }
            let base_name = if aliases.contains(&name) {
                "agent".to_string()
            } else {
                name.clone()
            };
            let cleaned = local_part_sanitize(&base_name);
            let want = if cfg.system_name.is_empty() {
                format!("{cleaned}@{}", cfg.domain)
            } else {
                format!("{cleaned}.{}@{}", cfg.system_name, cfg.domain)
            };
            if !rows.iter().any(|r| r.email == want) {
                rows.push(Row {
                    agent: name,
                    email: want,
                    manager: String::new(),
                    webhook: String::new(),
                    registered: false,
                    platform: platform.clone(),
                });
            }
        }
    }

    // 已注册在前，其次按 agent 名（Python 同键：`(not registered, agent)`；稳定排序）
    rows.sort_by_key(|r| (!r.registered, r.agent.clone()));
    rows
}

fn header_lines(sid: &str, cfg: &config::GatewayConfig) -> Vec<String> {
    let current = if cfg.default_agent_name.is_empty() {
        "agent"
    } else {
        cfg.default_agent_name.as_str()
    };
    let platform = if cfg.system_home.is_empty() {
        String::new()
    } else {
        platforms::detect_platform_from_home(Path::new(&cfg.system_home)).to_string()
    };
    let mut lines = vec![
        format!(
            "  system: {sid} ({})",
            if platform.is_empty() { "?" } else { &platform }
        ),
        format!("  默认主 agent 名: {current}"),
    ];
    let pairs: Vec<String> = platforms::all_aliases()
        .iter()
        .flat_map(|(k, al)| al.iter().map(move |a| format!("{k} {a}")))
        .collect();
    if !pairs.is_empty() {
        lines.push(format!("  (默认映射: {} → {current})", pairs.join(", ")));
    }
    lines.push(String::new());
    lines
}

fn table_lines(rows: &[Row]) -> Vec<String> {
    let mut out = vec![format!(
        "  {:<18} {:<46} {:<30} webhook   状态",
        "agent", "email", "manager"
    )];
    for a in rows {
        let webhook: String = a.webhook.chars().take(28).collect();
        if a.registered {
            out.push(format!(
                "  {:<18} {:<46} {:<30} {webhook}  已注册",
                a.agent, a.email, a.manager
            ));
        } else {
            let plat = if a.platform.is_empty() {
                "?"
            } else {
                a.platform.as_str()
            };
            out.push(format!(
                "  {:<18} {:<46} {:<30} {:<28}  未注册(平台 {plat} 有该 agent,先 install/bind 注册)",
                a.agent, a.email, "", ""
            ));
        }
    }
    out
}

pub fn run(args: Args) -> i32 {
    let aimail_home = home::aimail_home();
    let mut sid = args.system_id.clone();
    if sid.is_empty() && (args.inbound_live || args.inbound_down) {
        let target = args
            .email
            .clone()
            .or_else(|| args.agent.clone())
            .unwrap_or_default();
        sid = sid_for_address(&aimail_home, &target);
    }
    if sid.is_empty() {
        return report::fail("address 需要 --system-id");
    }
    let cfg_path = config::gateway_config_path_in(&aimail_home, &sid);
    if !cfg_path.is_file() {
        return report::fail(&format!("无系统配置: {}", cfg_path.display()));
    }
    let cfg = match config::load_gateway_config_in(&aimail_home, &sid) {
        Some(c) => c,
        None => return report::fail(&format!("配置读取失败: {}", cfg_path.display())),
    };

    // ── 入站 live/down：必须先于 op 链（否则 flag 会静默落成 list）──────────
    if args.inbound_live || args.inbound_down {
        // ①② 互斥 + 定位（照抄 `cli/aimail:_cmd_inbound_route` 的三条文案）
        if args.inbound_live && args.inbound_down {
            return crate::cmd::report::fail("inbound-live 与 inbound-down 互斥,只能选一个");
        }
        let loc = if args.email.is_some() {
            args.email.clone().unwrap_or_default()
        } else if let Some(a) = &args.agent {
            a.clone()
        } else {
            return crate::cmd::report::fail(
                "inbound live/down needs -a <agent> (or -e <email>) to locate the binding",
            );
        };
        let agents = list_agents(&aimail_home, &sid, &cfg);
        let target = agents.iter().find(|r| {
            if loc.contains('@') {
                r.email == loc
            } else {
                r.agent == loc || r.email.split('@').next().unwrap_or("") == loc
            }
        });
        let Some(t) = target else {
            let msg = if loc.contains('@') {
                format!("no local address for {loc} (run install/bind for that agent first)")
            } else {
                let mut names: Vec<String> = agents.iter().map(|r| r.agent.clone()).collect();
                names.sort();
                format!(
                    "no local address for -a {loc} (local: {})",
                    names.join(", ")
                )
            };
            return crate::cmd::report::fail(&msg);
        };
        let anchor = t.email.clone();
        if !binding_registered(&aimail_home, &sid, &anchor) {
            return crate::cmd::report::fail(&format!(
                "no local binding for {anchor} (run install/bind for that agent first)"
            ));
        }
        let cfgj = Value::Object(cfg.to_json());
        let bcfg = crate::core::bridge_wire::bridge_cfg_file();

        if args.inbound_down {
            let (withdrawn, failed, no_bridge, anchor_out) =
                crate::core::inbound_route::withdraw_inbound_routes(&sid, &cfgj, &bcfg, &anchor);
            if no_bridge {
                return crate::core::inbound_route::finish_anchor(anchor_out.as_ref());
            }
            let note = match crate::core::inbound_route::gateway_backlog_count(
                &cfgj,
                std::slice::from_ref(&anchor),
            ) {
                Some(n) => format!("; backlog {n} pending"),
                None => "; backlog unknown".to_string(),
            };
            crate::cmd::report::ok(&format!(
                "inbound down: {withdrawn} route(s) withdrawn for system {sid}{note}"
            ));
            if failed > 0 {
                crate::cmd::report::warn(&format!("{failed} FAILED (nothing changed for those)"));
            }
            let synth = if anchor_out.is_none() {
                Some(crate::core::inbound_route::already_absent_outcome(&anchor))
            } else {
                anchor_out.clone()
            };
            return crate::core::inbound_route::finish_anchor(synth.as_ref());
        }

        // live：走既有全量对账；锚点行不在表里（pull 绑定）⇒ 合成 skipped（幂等）
        let (_reported, anchor_out) =
            crate::core::bridge_wire::reconcile_inbound_routes(&sid, &cfgj, &anchor, &bcfg);
        let synth = if anchor_out.is_none() {
            Some(crate::core::inbound_route::skipped_pull_outcome(&anchor))
        } else {
            anchor_out.clone()
        };
        return crate::core::inbound_route::finish_anchor(synth.as_ref());
    }

    // ── op 推导（顺序照抄 Python；只有"无任何定位参数"才是 list）────────────
    let op = if args.default.is_some() {
        "default"
    } else if args.manager.is_some() {
        "set-manager"
    } else if args.name.is_some() {
        "set-name"
    } else if args.email.is_some() {
        "show"
    } else {
        "list"
    };

    match op {
        "list" => {
            let agents = list_agents(&aimail_home, &sid, &cfg);
            for line in header_lines(&sid, &cfg) {
                println!("{line}");
            }
            if agents.is_empty() {
                println!("  无本地注册 agent(地址键目录为空)。");
                return 0;
            }
            for line in table_lines(&agents) {
                println!("{line}");
            }
            0
        }
        "add" | "rm" => {
            // 契约 v1.0 §4.1(2)：为 SDK 反调预留（agent 新增/删除属 address 子命令）。
            // 未实现必须**响亮失败**（非 0），不得静默当成功。
            crate::cmd::stub::not_yet_ported("address add|rm")
        }
        "show" => {
            let agents = list_agents(&aimail_home, &sid, &cfg);
            let want = args.email.clone().unwrap_or_default().to_lowercase();
            match agents.iter().find(|r| r.email == want) {
                None => report::fail(&format!(
                    "本机未找到地址 {want} 的 {}(先在该 agent 上跑 install 注册)",
                    contract::binding_file()
                )),
                Some(target) => {
                    for line in header_lines(&sid, &cfg) {
                        println!("{line}");
                    }
                    for line in table_lines(std::slice::from_ref(target)) {
                        println!("{line}");
                    }
                    0
                }
            }
        }
        "default" => {
            let new_name = args
                .default
                .clone()
                .or_else(|| args.name.clone())
                .unwrap_or_default();
            if new_name.is_empty() {
                return report::fail("address default 需要 -n <名字>");
            }
            let atext_ok = !new_name.is_empty()
                && new_name
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "!#$%&'*+-/=?^_`{|}~".contains(c));
            if !atext_ok {
                return report::fail(&format!(
                    "非法名字 '{new_name}':须为 atext-no-dot 字符(不能含点/空格/@)"
                ));
            }
            let cfgj = Value::Object(cfg.to_json());
            let current = cfgj
                .get("default_agent_name")
                .and_then(|v| v.as_str())
                .unwrap_or("agent")
                .to_string();
            if new_name == current {
                println!("  已是当前默认名: {current}");
                return 0;
            }
            let gw = cfg.gateway_url.trim_end_matches('/').to_string();
            if !gw.is_empty() && !cfg.admin_key.is_empty() {
                let client = GatewayClient::with_defaults(&gw, &cfg.admin_key);
                let r = client.get(&format!("/api/v1/admin/systems/{sid}/domains"));
                let payload = match r.get("data") {
                    Some(d) => d.clone(),
                    None => r,
                };
                let mut occupied: Vec<String> = Vec::new();
                for d in payload.as_array().cloned().unwrap_or_default() {
                    let addr = d.get("domain").and_then(|v| v.as_str()).unwrap_or("");
                    let base = match addr.split_once('@') {
                        Some((b, _)) => b,
                        None => "",
                    };
                    if base == new_name {
                        occupied.push(addr.to_string());
                    }
                }
                if !occupied.is_empty() {
                    return report::fail(&format!(
                        "名字 '{new_name}' 已占用: {}",
                        crate::core::pyjson::repr_python(&Value::Array(
                            occupied.iter().map(|s| Value::String(s.clone())).collect()
                        ))
                    ));
                }
            }
            let mut newcfg = cfg.clone();
            newcfg.default_agent_name = new_name.clone();
            let w = config::save_gateway_config_in(&aimail_home, &sid, &newcfg);
            if let Err(e) = w {
                return report::fail(&format!("写入配置失败: {e}"));
            }
            report::ok(&format!("默认主 agent 名: {current} → {new_name}"));
            let dom = cfgj.get("domain").and_then(|v| v.as_str()).unwrap_or("");
            let sysname = cfgj
                .get("system_name")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let suffix = if sysname.is_empty() {
                String::new()
            } else {
                format!(".{sysname}")
            };
            println!(
                "  生效方式:agent 地址将变为 {new_name}@{dom}{suffix}(共享域加 .{sysname} 后缀)"
            );
            println!("  需重注册:aimail reset --system-id {sid} 或 aimail install");
            0
        }
        "set-manager" => {
            // `-m` 校验（Python：空或缺 `@` ⇒ 同一句文案）
            let mgr = args.manager.clone().unwrap_or_default();
            if mgr.is_empty() || !mgr.contains('@') {
                return report::fail("address set-manager 需要 -m <manager 邮箱>");
            }
            let agents = list_agents(&aimail_home, &sid, &cfg);
            let target = locate(&agents, &args);
            let Some(t) = target else {
                return report::fail("该操作需要 -a <agent> 或 -e <email> 定位目标地址");
            };
            // agent 域资源 CRUD 在 SDK、CLI 只触发（owner 裁决）⇒ 按名调用，**不复刻算法**。
            let agent_cfg_path = aimail_home
                .join("systems")
                .join(&sid)
                .join(crate::core::bridge_wire::addr_clean(&t.email))
                .join(contract::binding_file());
            let agent_cfg: Value = std::fs::read_to_string(&agent_cfg_path)
                .ok()
                .and_then(|x| serde_json::from_str(&x).ok())
                .unwrap_or_else(|| serde_json::json!({}));
            let cfg_json = Value::Object(cfg.to_json());
            let sdk_root = crate::core::sdkroot::resolve_or_repo_candidate();
            match crate::core::sdkcall::call_positional(
                "aimail_base",
                "set_agent_manager",
                &[
                    Value::String(sid.clone()),
                    Value::String(t.email.clone()),
                    Value::String(mgr.clone()),
                    cfg_json,
                    agent_cfg,
                ],
                &serde_json::json!({}),
                &sdk_root.path,
                std::time::Duration::from_secs(30),
                &[],
            ) {
                Ok(_) => {
                    report::ok(&format!("manager: {} → {}(云端+本地已同步)", t.email, mgr));
                    0
                }
                Err(e) => {
                    // Python：ValueError ⇒ `更新 manager 失败: {e}`
                    let msg = match &e {
                        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
                        other => format!("{:?}", other),
                    };
                    report::fail(&format!("更新 manager 失败: {}", msg))
                }
            }
        }
        "set-name" => {
            let new_name = args.name.clone().unwrap_or_default();
            if new_name.is_empty() {
                return report::fail("address set-name 需要 -n <新地址名>");
            }
            let agents = list_agents(&aimail_home, &sid, &cfg);
            let Some(t) = locate(&agents, &args) else {
                return report::fail("该操作需要 -a <agent> 或 -e <email> 定位目标地址");
            };
            // 改名 = SDK 的 CRUD（owner 裁决）：校验/派生/冲突预检/云端 rename/白名单清理/
            // 本地迁移/指针全在 SDK；CLI 只触发、不自拼名字、不自打桥。**不复刻算法**。
            // 契约 v1.0 §4.1：经 SDK 门 `update(action=rename)`（判定/派生/落盘全在 SDK；
            // CLI 只触发、不自拼名字、不取中间值）。
            let res = match crate::core::sdk::sdk_ops_call(
                "update",
                &serde_json::json!({
                    "system_id": sid.clone(),
                    "email": t.email.clone(),
                    "old_email": t.email.clone(),
                    "new_name": new_name.clone(),
                    "action": "rename",
                }),
                &crate::core::home::program_root(),
                std::time::Duration::from_secs(60),
                &[],
            ) {
                Ok(v) => v.get("result").cloned().unwrap_or(v),
                Err(e) => {
                    let msg = match &e {
                        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
                        other => format!("{:?}", other),
                    };
                    return report::fail(&format!("地址改名失败: {msg}"));
                }
            };
            if res
                .get("unchanged")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                println!("  已是该地址: {}", t.email);
                return 0;
            }
            if res.get("merged").and_then(|v| v.as_bool()).unwrap_or(false) {
                report::warn(&format!(
                    "本地目录 {} 已存在,合并内容字段(保留两个目录)",
                    res.get("dir").and_then(|v| v.as_str()).unwrap_or("")
                ));
            }
            if !res
                .get("migrated")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                report::warn("本地迁移失败(云端已改名,重跑 install 可重建本地)");
            }
            report::ok(&format!(
                "地址改名: {} → {}",
                t.email,
                res.get("new_email").and_then(|v| v.as_str()).unwrap_or("")
            ));
            println!(
                "  服务端资源(白名单/联系人/看板/密钥)与本地 {} 全部继承",
                contract::binding_file()
            );
            let sig = res
                .get("signal")
                .cloned()
                .unwrap_or_else(|| serde_json::json!({}));
            let state = sig.get("state").and_then(|v| v.as_str()).unwrap_or("");
            if state == "notified" {
                println!("  生效:同 key 继续使用;上线信号已触发路由对账,新地址即日起收发");
            } else {
                report::warn(&format!(
                    "上线信号未送达({}): {} — 路由未刷新,重启 agent 或 inbound-live 后自愈",
                    if state.is_empty() { "skipped" } else { state },
                    sig.get("detail").and_then(|v| v.as_str()).unwrap_or("")
                ));
            }
            0
        }
        other => crate::cmd::stub::not_yet_ported(&format!("address {other}")),
    }
}

/// 目标定位（照抄 `cli/aimail:2082-2098`）：`-e` 精确 email；`-a` 按 agent 名或
/// email 本地段前缀匹配。命中不到 ⇒ 返回 None（调用方给统一失败文案）。
fn locate<'a>(agents: &'a [Row], args: &Args) -> Option<&'a Row> {
    if let Some(em) = &args.email {
        return agents.iter().find(|r| r.email == em.to_lowercase());
    }
    if let Some(ag) = &args.agent {
        return agents.iter().find(|r| {
            r.agent == *ag
                || r.email
                    .split('@')
                    .next()
                    .map(|l| l.starts_with(ag.as_str()))
                    .unwrap_or(false)
        });
    }
    None
}

/// 绑定是否"已注册"（绑定文件存在且可解析为对象）—— hidden 面锚点守卫用。
fn binding_registered(home: &std::path::Path, sid: &str, email: &str) -> bool {
    let leaf = home
        .join("systems")
        .join(sid)
        .join(crate::core::bridge_wire::addr_clean(email));
    let f = leaf.join(crate::core::contract::binding_file());
    std::fs::read_to_string(&f)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .map(|v| v.is_object())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_part_sanitize_keeps_atext_and_drops_dot() {
        assert_eq!(local_part_sanitize("agent"), "agent");
        assert_eq!(local_part_sanitize("a.b"), "a_b"); // 点不在集合内 → 归一
        assert_eq!(local_part_sanitize("a+b_c"), "a+b_c");
        assert_eq!(local_part_sanitize("中文"), "__");
        assert_eq!(local_part_sanitize(""), "agent");
    }

    #[test]
    fn table_header_matches_python_layout() {
        let lines = table_lines(&[]);
        assert_eq!(
            lines[0],
            format!(
                "  {:<18} {:<46} {:<30} webhook   状态",
                "agent", "email", "manager"
            )
        );
    }
}
