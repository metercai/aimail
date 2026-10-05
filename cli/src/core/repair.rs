//! repair 引擎 —— `cli/repair.py` 的复刻（plan v3 S5，分切片落地）。
//!
//! 本切片（S5a）落**框架与判读**：
//! - `REPAIRABILITY` 表（检查维度 → 可修性分类：auto/hint + 阶梯步号 + needs）
//!   —— 与 `cli/repair.py:851-897` 逐条对齐，并有一条**表一致性守门**测试直接向
//!   Python 要真值比对（表漂移即红）；
//! - `classify_residual`：复检残余 → （本地可修的缺陷, 需要管理员/宿主的提示）；
//! - 阶梯（`ladder`）的描述与顺序（含 `--deep` 追加的 drain 步）；
//! - `run`：repair 的编排骨架（打印头 → 跑 check（进程内复用引擎，零逻辑重复）
//!   → 列失败项 → 建阶梯 → dry-run 只列计划 → 否则逐步执行 → 复检 → 残余分类 → rc）。
//!
//! **阶梯的十个写面动作在 S5b 落地**：`Step::run` 目前一律返回
//! `Step::NotPorted(...)`，由编排计入 `ladder_bad` 并打印未移植说明 —— 不是静默通过。
//! 也因此 `repair` 命令面**仍未接线**（写面残缺的 repair 会"报修好但没修"）。

use crate::core::check::{Check, Record};
use crate::core::style::{NC, RED, YELLOW};
use crate::core::{config, contract, platforms, sdk};
use serde_json::Value;
use std::path::Path;

/// 可修性分类（`repair.py:903-905` 的返回结构）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Repairability {
    /// 本机可自动修（`kind=auto`），`step` = 阶梯步号（仅用于文档/排序参考）。
    Auto { step: u8 },
    /// 只能给提示：需要宿主/服务端/管理员动作。
    Hint { why: Option<&'static str> },
}

impl Repairability {
    pub fn is_auto(&self) -> bool {
        matches!(self, Repairability::Auto { .. })
    }
}

use Repairability::{Auto, Hint};

/// 表一致性守门：本表与 `cli/repair.py:851-897` 必须逐条一致
/// （`cli/tests/repair_parity.rs` 直接向 Python 要真值比对）。
pub const REPAIRABILITY: &[((&str, &str), Repairability)] = &[
    (("config", "complete"), Auto { step: 4 }),
    (("config", "gateway_json"), Auto { step: 4 }),
    (("config", "system_home"), Auto { step: 4 }),
    (("config", "pointer"), Auto { step: 5 }),
    (("gateway", "config"), Auto { step: 4 }),
    (("gateway", "smtp_port"), Auto { step: 4 }),
    (
        ("gateway", "health"),
        Hint {
            why: Some("the target gateway is down/unreachable -> start or fix it on the host side"),
        },
    ),
    (
        ("gateway", "api_key"),
        Hint {
            why: Some(
                "admin key missing or invalid -> re-install (aimail install) or have an administrator issue one",
            ),
        },
    ),
    (("bridge", "process"), Auto { step: 1 }),
    (("bridge", "config"), Auto { step: 1 }),
    (("bridge", "config-complete"), Auto { step: 1 }),
    (("bridge", "config_consistency"), Auto { step: 1 }),
    (("bridge", "config-mode"), Auto { step: 1 }),
    (("bridge", "activity"), Auto { step: 1 }),
    (("bridge", "self_health"), Auto { step: 1 }),
    (
        ("bridge", "pull_path"),
        Hint {
            why: Some(
                "bridge cannot poll the gateway pending API -> the gateway must be reachable (host side); a missing config entry is fixed by pull-entry",
            ),
        },
    ),
    (("bridge", "pull-entry"), Auto { step: 9 }),
    (("bridge", "routes-entry"), Auto { step: 8 }),
    (
        ("bridge", "routes-target"),
        Hint {
            why: Some(
                "the target agent endpoint is down / the address is unreachable -> start that agent on the host side",
            ),
        },
    ),
    (("agent", "config"), Auto { step: 7 }),
    (("agent", "config-json"), Auto { step: 7 }),
    (("agent", "config-consistency"), Auto { step: 7 }),
    (("agent", "name_apikey"), Auto { step: 7 }),
    (
        ("agent", "config-complete"),
        Hint {
            why: Some(
                "fields such as system_name are authoritative server-side (no local source) -> re-register with aimail install, or let an administrator handle it",
            ),
        },
    ),
    (("agent", "pointer"), Auto { step: 5 }),
    (
        ("agent", "hook"),
        Auto { step: 6 },
    ),
    (
        ("agent", "skill"),
        Auto { step: 6 },
    ),
    (
        ("agent", "toolset"),
        Auto { step: 6 },
    ),
    (("agent", "webhook"), Auto { step: 3 }),
    (
        ("agent", "discovery"),
        Hint {
            why: Some(
                "the platform has no record of this agent yet -> create/register it with the platform's own command",
            ),
        },
    ),
    (
        ("agent", "register"),
        Hint {
            why: Some(
                "the address is not registered in the cloud -> aimail install / the registration chain, or an administrator",
            ),
        },
    ),
    (
        ("agent", "session"),
        Hint {
            why: Some("the agent session/endpoint is down -> start that agent on the host side"),
        },
    ),
    (("runtime", "mcp-payload"), Auto { step: 6 }),
    (("runtime", "board-resources"), Auto { step: 6 }),
    (("runtime", "host-payload-refs"), Auto { step: 6 }),
    (("runtime", "platform-locatable"), Auto { step: 5 }),
    (("system", "id"), Auto { step: 4 }),
];

/// 未登记维度的兜底（`_UNREGISTERED`）：当 HINT 处理，并**显式**说明这不是"已覆盖"。
pub const UNREGISTERED: Repairability = Hint {
    why: Some(
        "unregistered check dimension -> treated as needing admin action (please report it to the maintainer)",
    ),
};

/// `repairability(level, name)`。
pub fn repairability(level: &str, name: &str) -> Repairability {
    REPAIRABILITY
        .iter()
        .find(|((l, n), _)| *l == level && *n == name)
        .map(|(_, v)| *v)
        .unwrap_or(UNREGISTERED)
}

/// `classify_residual`：复检残余 → （本地可修的缺陷, 需要管理员/宿主的提示）。
/// 顺序保真（Python 是单次遍历，两列表各自保持原顺序）。
pub fn classify_residual(residual: &[Record]) -> (Vec<Record>, Vec<Record>) {
    let mut defects = Vec::new();
    let mut hints = Vec::new();
    for c in residual {
        if repairability(&c.level, &c.check).is_auto() {
            defects.push(c.clone());
        } else {
            hints.push(c.clone());
        }
    }
    (defects, hints)
}

/// 阶梯一步（描述 + 动作）。描述文本是 dry-run 计划的一部分 ⇒ 逐字照抄。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    BridgeAlive,
    RefreshRoutes,
    WebhookPairing,
    GatewayConfig,
    Pointer,
    RuntimeResources,
    McpPayload,
    AgentmailJson,
    RoutesEntries,
    PullEntryKey,
    DrainStuck,
}

impl Step {
    /// 该步的 dry-run 描述（`repair.py:980-1006` 逐字；含契约值的用常量拼，不抄字面量）。
    pub fn desc(&self) -> String {
        match self {
            Step::BridgeAlive => {
                "bridge alive (remote gateway only; start it idempotently when dead)".to_string()
            }
            Step::RefreshRoutes => {
                "refresh bridge routes (file + admin API hot reload)".to_string()
            }
            Step::WebhookPairing => {
                "repair the gateway webhook pairing (evidence-driven; --deep rewrites directly)"
                    .to_string()
            }
            Step::GatewayConfig => {
                "backfill the gateway config (system_home/webhook_host: fill gaps only)".to_string()
            }
            Step::Pointer => {
                "recreate the platform pointer (only when the platform root is certain and the pointer is missing)"
                    .to_string()
            }
            Step::RuntimeResources => {
                "redeploy runtime resources (patch marker/resources missing -> idempotent reinstall)"
                    .to_string()
            }
            Step::McpPayload => {
                "refresh the runtime payload (mcp missing/stale -> idempotent local bundle install)"
                    .to_string()
            }
            // 描述里含绑定文件名 —— 用**契约常量**拼（不抄字面量）
            Step::AgentmailJson => format!(
                "fill gaps in {} + align webhook_url with the live route",
                crate::core::contract::binding_file()
            ),
            Step::RoutesEntries => {
                "complete the bridge routes entries (missing -> refresh)".to_string()
            }
            Step::PullEntryKey => {
                "align the bridge pull entry admin_key with gateway.json".to_string()
            }
            Step::DrainStuck => "drain stuck pending (--deep)".to_string(),
        }
    }

    /// 执行该步。**仍未移植的步一律 `NotPorted`**（绝不静默当成功）；已落地的走真实现。
    pub fn run(&self, sid: &str, home: &str, _deep: bool) -> StepResult {
        match self {
            // 第 7 步：绑定文件补空 + webhook_url 对齐（写回经 SDK 门）
            Step::AgentmailJson => {
                let ah = crate::core::home::aimail_home();
                if agentmail_backfill(sid, &ah) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // 第 6 步：运行时资源缺失 → 经该平台**自足 SDK 安装入口**幂等重铺
            Step::RuntimeResources => {
                if runtime_resources(sid, home, &crate::core::home::program_root()) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // 第 11 步（--deep）：把卡住的 pending 全部 ack（兜底清理）
            Step::DrainStuck => {
                let client = gateway_client(sid);
                if drain_stuck(&client) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // 第 1 步：桥存活（本地网关=直连模式 ⇒ 无需桥；否则 pid 探测，死了幂等起）
            Step::BridgeAlive => {
                if ensure_bridge_running(sid) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            // 第 2 步：重刷本系统路由 = 复用本仓 `bridge --system-id`（同一实现，不复刻）
            Step::RefreshRoutes => {
                let rc = crate::cmd::bridge::run(&crate::cmd::bridge::Args {
                    system_id: sid.to_string(),
                    home: String::new(),
                    restart: false,
                    upgrade: false,
                    platform: String::new(),
                });
                if rc == 0 {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            Step::GatewayConfig => {
                // `cli/repair.py:473-517`：只补 system_home/webhook_host 的空缺，**绝不覆写已有值**。
                let gw_path = crate::core::config::gateway_config_path(sid);
                if !gw_path.is_file() {
                    fail(&format!(
                        "gateway config does not exist: {}",
                        gw_path.display()
                    ));
                    StepResult::NothingToDo
                } else {
                    let mut v: serde_json::Value = std::fs::read_to_string(&gw_path)
                        .ok()
                        .and_then(|t| serde_json::from_str(&t).ok())
                        .unwrap_or(serde_json::Value::Null);
                    if v.is_null() {
                        fail(&format!("gateway config unreadable: {}", gw_path.display()));
                        StepResult::NothingToDo
                    } else {
                        let mut changed = false;
                        let root = if !home.is_empty() {
                            home.to_string()
                        } else {
                            auto_platform_home(
                                &crate::core::home::user_home(),
                                v.get("system_home").and_then(|x| x.as_str()),
                            )
                        };
                        let need_home = v
                            .get("system_home")
                            .and_then(|x| x.as_str())
                            .unwrap_or("")
                            .is_empty();
                        if need_home {
                            if !root.is_empty()
                                && crate::core::platforms::detect_platform_from_home(
                                    std::path::Path::new(&root),
                                ) != "unknown"
                            {
                                v["system_home"] = serde_json::Value::String(root.clone());
                                ok(&format!("system_home backfilled: {root}"));
                                changed = true;
                            } else {
                                warn("system_home missing and the platform root cannot be determined (on a multi-platform machine pass --home)");
                            }
                        }
                        if v.get("webhook_host")
                            .and_then(|x| x.as_str())
                            .unwrap_or("")
                            .is_empty()
                        {
                            let wh = detect_webhook_host(
                                v.get("gateway_url").and_then(|x| x.as_str()).unwrap_or(""),
                            );
                            if !wh.is_empty() {
                                let (deliverable, why) = judge_deliverable(&wh);
                                if deliverable {
                                    v["webhook_host"] = serde_json::Value::String(wh.clone());
                                    ok(&format!("webhook_host backfilled: {wh}"));
                                    changed = true;
                                } else {
                                    warn(&format!("detected callback address '{wh}' is not a deliverable http(s) URL ({why}) — leaving webhook_host unset (no bridge entry; registration keeps the local endpoint)"));
                                }
                            }
                        }
                        if changed {
                            let txt = serde_json::to_string_pretty(&v).unwrap_or_default();
                            let _ = std::fs::write(&gw_path, txt + "\n");
                            let _ = std::fs::set_permissions(
                                &gw_path,
                                std::os::unix::fs::PermissionsExt::from_mode(0o600),
                            );
                            StepResult::Fixed
                        } else {
                            StepResult::NothingToDo
                        }
                    }
                }
            }
            Step::RoutesEntries => {
                // `cli/repair.py:720-757`：路由缺失项 ⇒ 交本仓 `bridge --system-id` 刷新（生成在桥侧）。
                let sysdir =
                    crate::core::config::system_dir_in(&crate::core::home::aimail_home(), sid);
                let rf = crate::core::bridge_wire::routes_file();
                if !sysdir.is_dir() || !rf.is_file() {
                    return StepResult::NothingToDo;
                }
                let routes = crate::core::bridge_wire::read_routes(&rf);
                let mut subs: Vec<std::path::PathBuf>;
                let mut missing: Vec<String> = Vec::new();
                let mut skipped: Vec<String> = Vec::new();
                subs = match std::fs::read_dir(&sysdir) {
                    Ok(rd) => rd.filter_map(|e| e.ok().map(|x| x.path())).collect(),
                    Err(e) => {
                        warn(&format!(
                            "system directory unreadable ({e}) -> skipping the routes-entry repair"
                        ));
                        return StepResult::NothingToDo;
                    }
                };
                subs.sort();
                for sub in subs {
                    let aj = sub.join(crate::core::contract::binding_file());
                    if !aj.is_file() {
                        continue;
                    }
                    // 逐文件容错：单个文件读不了/坏 ⇒ 只跳过它，绝不中止整步（2026-09-20 实测）
                    let raw = match std::fs::read_to_string(&aj) {
                        Ok(t) => t,
                        Err(_) => {
                            skipped.push(format!(
                                "{}(OSError)",
                                sub.file_name()
                                    .map(|x| x.to_string_lossy().to_string())
                                    .unwrap_or_default()
                            ));
                            continue;
                        }
                    };
                    let d: serde_json::Value = match serde_json::from_str(&raw) {
                        Ok(d) => d,
                        Err(_) => {
                            skipped.push(format!(
                                "{}(JSONDecodeError)",
                                sub.file_name()
                                    .map(|x| x.to_string_lossy().to_string())
                                    .unwrap_or_default()
                            ));
                            continue;
                        }
                    };
                    let email = d
                        .get("email")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string();
                    if !email.is_empty() && !routes.contains_key(&email) {
                        missing.push(email);
                    }
                }
                if !skipped.is_empty() {
                    warn(&format!(
                        "routes-entry repair: skipped {} unreadable file(s): {}",
                        skipped.len(),
                        skipped.join(", ")
                    ));
                }
                if missing.is_empty() {
                    ok("routes entries ok");
                    return StepResult::NothingToDo;
                }
                warn(&format!(
                    "routes missing {} entr(ies) -> refreshed via bridge",
                    crate::core::pyjson::repr_python(&serde_json::json!(missing))
                ));
                if refresh_routes(sid) {
                    StepResult::Fixed
                } else {
                    StepResult::NothingToDo
                }
            }
            Step::McpPayload => {
                // `cli/repair.py:915+`：结论取自 payload 状态，绝不看退出码（防误报）。
                let dest = crate::core::payload::bundle_default_dest("mcp").unwrap_or_default();
                let dp = std::path::PathBuf::from(&dest);
                let st =
                    crate::core::payload::payload_state(crate::core::payload::MCP_FILES, &dp, None);
                if !st.present {
                    warn("mcp payload not installed -> idempotent install");
                } else if st.missing.is_empty() && st.stale.is_empty() {
                    ok("mcp payload ok (complete and in step with the current version)");
                    return StepResult::NothingToDo;
                } else {
                    let mut bad: Vec<String> = st.missing.clone();
                    bad.extend(st.stale.clone());
                    warn(&format!(
                        "mcp payload missing/stale ({}) -> idempotent reinstall",
                        bad.join(", ")
                    ));
                }
                let rc = crate::core::payload::install_bundle("mcp", &dest, "", true);
                if rc == 0 {
                    ok("mcp payload installed");
                    StepResult::Fixed
                } else {
                    fail("mcp payload install failed");
                    StepResult::NothingToDo
                }
            }
            Step::Pointer => {
                // `cli/repair.py:520-555`：某平台根 + 本 sid 无指针 + 目标指针文件不存在 ⇒ 才写。
                let uh = crate::core::home::user_home();
                if sid_has_pointer(&uh, sid) || home.is_empty() {
                    return StepResult::NothingToDo;
                }
                let plat =
                    crate::core::platforms::detect_platform_from_home(std::path::Path::new(home));
                if plat == "unknown" {
                    return StepResult::NothingToDo;
                }
                let sysdir =
                    crate::core::config::system_dir_in(&crate::core::home::aimail_home(), sid);
                let mut email = String::new();
                if let Ok(rd) = std::fs::read_dir(&sysdir) {
                    let mut subs: Vec<std::path::PathBuf> =
                        rd.filter_map(|e| e.ok().map(|x| x.path())).collect();
                    subs.sort();
                    for sub in subs {
                        let aj = sub.join(crate::core::contract::binding_file());
                        let d = std::fs::read_to_string(&aj)
                            .ok()
                            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
                        if let Some(e) = d
                            .as_ref()
                            .and_then(|v| v.get("email"))
                            .and_then(|v| v.as_str())
                        {
                            if !e.is_empty() {
                                email = e.to_string();
                                break;
                            }
                        }
                    }
                }
                if email.is_empty() {
                    return StepResult::NothingToDo;
                }
                let ptr = crate::core::platforms::pointer_paths(&uh, plat)
                    .into_iter()
                    .next()
                    .unwrap_or_default();
                if ptr.as_os_str().is_empty() || ptr.exists() {
                    return StepResult::NothingToDo;
                }
                if let Some(dir) = ptr.parent() {
                    let _ = std::fs::create_dir_all(dir);
                }
                let body = serde_json::json!({"system_id": sid, "email": email});
                let text = serde_json::to_string_pretty(&body).unwrap_or_default();
                match std::fs::write(&ptr, text) {
                    Ok(_) => {
                        ok(&format!("pointer created: {} → {sid}", ptr.display()));
                        StepResult::Fixed
                    }
                    Err(e) => {
                        fail(&format!("pointer write failed: {} ({e})", ptr.display()));
                        StepResult::NothingToDo
                    }
                }
            }
            Step::PullEntryKey => {
                // `cli/repair.py:775-8xx`：把桥 `pull.systems[].admin_key` 对齐 gateway.json（唯一权威）。
                let gw_path = crate::core::config::gateway_config_path(sid);
                let cfg = crate::core::bridge_wire::bridge_cfg_file();
                if !gw_path.is_file() || !cfg.is_file() {
                    return StepResult::NothingToDo;
                }
                let gw: serde_json::Value = match std::fs::read_to_string(&gw_path)
                    .ok()
                    .and_then(|t| serde_json::from_str(&t).ok())
                {
                    Some(v) => v,
                    None => return StepResult::NothingToDo,
                };
                let gk = gw
                    .get("admin_key")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if gk.is_empty() {
                    return StepResult::NothingToDo;
                }
                let raw = std::fs::read_to_string(&cfg).unwrap_or_default();
                let td: toml::Value = match raw.parse() {
                    Ok(v) => v,
                    Err(_) => return StepResult::NothingToDo,
                };
                let systems = td
                    .get("pull")
                    .and_then(|p| p.get("systems"))
                    .and_then(|s| s.as_array())
                    .cloned()
                    .unwrap_or_default();
                let entry = systems
                    .iter()
                    .find(|x| x.get("system_id").and_then(|v| v.as_str()) == Some(sid));
                if entry.is_none() {
                    // 缺条目 => 本地创建（无需服务端），复用 install 的写者
                    let url = gw
                        .get("gateway_url")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .or_else(|| {
                            gw.get("aimail_url")
                                .and_then(|v| v.as_str())
                                .filter(|s| !s.is_empty())
                        })
                        .unwrap_or("")
                        .to_string();
                    if url.is_empty() {
                        warn("pull entry missing and the gateway config has no gateway_url -> cannot create it locally (fix the config first)");
                        return StepResult::NothingToDo;
                    }
                    let mode = td
                        .get("mode")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .unwrap_or("pull")
                        .to_string();
                    let addr = td
                        .get("bind")
                        .and_then(|v| v.as_str())
                        .filter(|s| !s.is_empty())
                        .unwrap_or("127.0.0.1:38081")
                        .to_string();
                    let spec = crate::core::bridge_deploy::BridgeConfigSpec {
                        path: cfg.clone(),
                        mode,
                        addr,
                        gateway_url: url,
                        admin_key: gk.clone(),
                        system_id: sid.to_string(),
                        api_key: gw
                            .get("api_key")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        webhook_secret: String::new(),
                        hostname: String::new(),
                    };
                    return match crate::core::bridge_deploy::write_bridge_config(&spec) {
                        Ok(_) => {
                            ok("bridge pull entry missing -> created locally (gateway_url/admin_key/system_id from the gateway config)");
                            StepResult::Fixed
                        }
                        Err(e) => {
                            fail(&format!("pull entry creation failed: {e}"));
                            StepResult::NothingToDo
                        }
                    };
                }
                if entry
                    .and_then(|e| e.get("admin_key"))
                    .and_then(|v| v.as_str())
                    == Some(gk.as_str())
                {
                    return StepResult::NothingToDo;
                }
                // 定向替换该条目的 admin_key（两种字段序，等价于 Python 的两条正则）
                match replace_entry_admin_key(&raw, sid, &gk) {
                    Some(new_raw) => {
                        if let Err(e) = std::fs::write(&cfg, &new_raw) {
                            fail(&format!("pull entry admin_key alignment failed: {e}"));
                            return StepResult::NothingToDo;
                        }
                        let _ = chmod600(&cfg);
                        ok("bridge pull entry admin_key aligned with the gateway config");
                        StepResult::Fixed
                    }
                    None => {
                        warn("pull entry admin_key alignment failed (no format match) -- check the bridge config by hand");
                        StepResult::NothingToDo
                    }
                }
            }
            _ => StepResult::NotPorted,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepResult {
    /// 有实际动作（或幂等确认）。
    Fixed,
    /// 明确未做（不计为失败，但必须打印）。
    NothingToDo,
    /// 本切片尚未移植（计为 ladder_bad，打印原因）。
    NotPorted,
}

/// 阶梯（`--deep` 追加 drain 步）。
pub fn ladder(deep: bool) -> Vec<Step> {
    let mut plan = vec![
        Step::BridgeAlive,
        Step::RefreshRoutes,
        Step::WebhookPairing,
        Step::GatewayConfig,
        Step::Pointer,
        Step::RuntimeResources,
        Step::McpPayload,
        Step::AgentmailJson,
        Step::RoutesEntries,
        Step::PullEntryKey,
    ];
    if deep {
        plan.push(Step::DrainStuck);
    }
    plan
}

fn warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}
fn fail(msg: &str) {
    println!("  {RED}✗{NC} {msg}");
}
fn ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}
use crate::core::style::GREEN;

/// 复检/首检的输出尾部（Python `_check_output_tail`：head 240 / tail 600）。
/// 进程内调用没有子进程 stdout；这里用记录摘要代替，保证"不可判读"时**有证据可看**。
fn checks_tail(records: &[Record]) -> String {
    if records.is_empty() {
        return "(no check output)".to_string();
    }
    let line = |c: &Record| format!("{}:{}", c.level, c.check);
    let head: Vec<String> = records.iter().take(6).map(line).collect();
    if records.len() <= 12 {
        head.join(", ")
    } else {
        let tail: Vec<String> = records
            .iter()
            .rev()
            .take(6)
            .map(line)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        format!("{}, … , {}", head.join(", "), tail.join(", "))
    }
}

/// repair 的表头行（dry-run 计划与测试都按它比对）。
pub fn header_line(sid: &str, deep: bool, dry_run: bool) -> String {
    let mut header = format!("  repair system={sid}");
    if dry_run {
        header.push_str(" [dry-run]");
    }
    if deep {
        header.push_str(" [deep]");
    }
    header
}

/// dry-run 计划行（`    - {desc}`）—— 单点保证"打印的"与"比对的"是同一份数据。
pub fn dry_run_plan_lines(deep: bool) -> Vec<String> {
    ladder(deep)
        .iter()
        .map(|s| format!("    - {}", s.desc()))
        .collect()
}

/// `repair(sid, deep, dry_run, home)` 的编排（check 走**进程内**引擎，零逻辑重复）。
///
/// `check_engine` 由命令层传入（cmd::check 的编排函数），这样 repair 与 check 用的是
/// 同一份引擎实现 —— Python 侧用"子进程 + 解析 JSON"达到同样目的。
/// 重刷本系统路由 = 复用本仓 `bridge --system-id`（同一实现，零复刻；Python `repair.py:_refresh_routes`）。
fn refresh_routes(sid: &str) -> bool {
    crate::cmd::bridge::run(&crate::cmd::bridge::Args {
        system_id: sid.to_string(),
        home: String::new(),
        restart: false,
        upgrade: false,
        platform: String::new(),
    }) == 0
}

/// 把桥配置文本里 `system_id = "<sid>"` 所属 `{…}` 块内的 `admin_key = "…"` 值替换为 gk。
/// 等价于 Python `repair.py:815-824` 的两条定向正则（字段序两种都试）。
fn replace_entry_admin_key(raw: &str, sid: &str, gk: &str) -> Option<String> {
    let sid_tok = format!("system_id = \"{sid}\"");
    let splice = |open: usize, close: usize, block_new: String| -> String {
        let mut out = String::with_capacity(raw.len() + gk.len());
        out.push_str(&raw[..open]);
        out.push_str(&block_new);
        out.push_str(&raw[close + 1..]);
        out
    };
    if let Some(pos) = raw.find(&sid_tok) {
        if let (Some(open), Some(close)) =
            (raw[..pos].rfind('{'), raw[pos..].find('}').map(|c| pos + c))
        {
            if let Some(nb) = replace_first_admin_key(&raw[open..=close], gk) {
                return Some(splice(open, close, nb));
            }
        }
    }
    let mut idx = 0usize;
    while let Some(o) = raw[idx..].find('{') {
        let open = idx + o;
        let close = match raw[open..].find('}') {
            Some(c) => open + c,
            None => break,
        };
        let block = &raw[open..=close];
        if block.contains(&sid_tok) {
            if let Some(nb) = replace_first_admin_key(block, gk) {
                return Some(splice(open, close, nb));
            }
        }
        idx = close + 1;
    }
    None
}

fn replace_first_admin_key(block: &str, gk: &str) -> Option<String> {
    let key = "admin_key = \"";
    let i = block.find(key)? + key.len();
    let j = block[i..].find('"')? + i;
    let mut out = String::with_capacity(block.len() + gk.len());
    out.push_str(&block[..i]);
    out.push_str(gk);
    out.push_str(&block[j..]);
    Some(out)
}

fn chmod600(p: &std::path::Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o600));
}

pub fn run(
    sid: &str,
    deep: bool,
    dry_run: bool,
    home: &str,
    check_engine: impl Fn(&str, Option<&Path>) -> Check,
) -> i32 {
    println!("{}", header_line(sid, deep, dry_run));

    let agent_home = if home.is_empty() {
        None
    } else {
        Some(Path::new(home))
    };
    let first = check_engine(sid, agent_home);
    let records: Vec<Record> = first.checks.clone();
    // 空 check = GAP（不可判读），不是"通过"（repair 方案A 2026-09-29）
    let passed = !records.is_empty() && records.iter().all(|r| r.pass);
    if records.is_empty() {
        warn("check produced no output -- the check step is unjudgeable in this form (NOT a system_id verdict); repair continues with its unconditional ladder");
        warn(&format!(
            "    check subprocess output: {}",
            checks_tail(&records)
        ));
    }
    if passed {
        ok("check is all green, nothing to repair");
        return 0;
    }
    for c in records.iter().filter(|r| !r.pass) {
        let detail: String = c.detail.chars().take(90).collect();
        warn(&format!("check ✗ {}/{}: {}", c.level, c.check, detail));
    }

    let plan = ladder(deep);
    if dry_run {
        println!("  [dry-run] plan:");
        for line in dry_run_plan_lines(deep) {
            println!("{line}");
        }
        return 0;
    }

    let mut ladder_bad = 0;
    for step in &plan {
        println!("\n  ── {} ──", step.desc());
        match step.run(sid, home, deep) {
            StepResult::Fixed | StepResult::NothingToDo => {}
            StepResult::NotPorted => {
                fail(&format!(
                    "repair action not ported yet: {} (plan v3 S5b)",
                    step.desc()
                ));
                ladder_bad += 1;
            }
        }
    }

    println!("\n  -- re-check --");
    let second = check_engine(sid, agent_home);
    let still: Vec<Record> = second.checks.iter().filter(|r| !r.pass).cloned().collect();
    let passed2 = !second.checks.is_empty() && second.checks.iter().all(|r| r.pass);
    if second.checks.is_empty() {
        warn("check produced no output on re-check -- re-check is UNJUDGEABLE (empty output is not all green)");
        warn(&format!(
            "    check subprocess output: {}",
            checks_tail(&second.checks)
        ));
        if ladder_bad > 0 {
            fail(&format!(
                "-> re-check unjudgeable and the ladder reported {ladder_bad} failed step(s): rc=1"
            ));
            return 1;
        }
        warn(
            "-> re-check unjudgeable: rc is the ladder's own result only, NOT an all-green verdict",
        );
        return 0;
    }
    if passed2 {
        ok("re-check is all green");
        return 0;
    }
    let (defects, hints) = classify_residual(&still);
    for c in &defects {
        let detail: String = c.detail.chars().take(90).collect();
        warn(&format!(
            "[D locally fixable, still failing] {}/{}: {}",
            c.level, c.check, detail
        ));
    }
    for c in &hints {
        let detail: String = c.detail.chars().take(80).collect();
        warn(&format!(
            "[H needs admin/host action] {}/{}: {}",
            c.level, c.check, detail
        ));
        if let Repairability::Hint { why: Some(why) } = repairability(&c.level, &c.check) {
            warn(&format!("    → {why}"));
        }
    }
    warn(&format!(
        "re-check not all green: {} locally-fixable defect(s) / {} needing admin action",
        defects.len(),
        hints.len()
    ));
    if !defects.is_empty() {
        warn("-> locally-fixable items are still failing (defects): report them to the maintainer with the log above");
    } else {
        warn("-> the remaining items are not reliably self-fixable (repair only hints; a system administrator acts)");
    }
    1
}

// ════════════════════════════════════════════════════════════════════════════
// S5b-i：CLI 自有权写入步（系统级文件 / 平台指针）—— 归属铁律：CLI 不写绑定文件
// 现状码：`cli/repair.py:415-553`（_auto_platform_home / _sid_has_pointer /
// _repair_gateway_config / _repair_pointer）
// ════════════════════════════════════════════════════════════════════════════

/// `_auto_platform_home`：单平台机器自动定平台根；多平台回退 cfg 的 system_home；否则空
/// （**不猜**）。
pub fn auto_platform_home(user_home: &Path, stored_system_home: Option<&str>) -> String {
    let mut hits: Vec<String> = Vec::new();
    for name in platforms::order() {
        let root = platforms::platform_root(user_home, name);
        if root.exists() && platforms::detect_platform_from_home(&root) == name {
            hits.push(root.to_string_lossy().into_owned());
        }
    }
    if hits.len() == 1 {
        return hits.remove(0);
    }
    if hits.len() > 1 {
        let sh = stored_system_home.unwrap_or("");
        if !sh.is_empty() && Path::new(sh).is_dir() {
            return sh.to_string();
        }
    }
    String::new()
}

/// `_sid_has_pointer(sid)`：任一平台的任一指针（root / root_or_profiles）指向该 sid。
pub fn sid_has_pointer(user_home: &Path, sid: &str) -> bool {
    for name in platforms::order() {
        for ptr in platforms::pointer_paths(user_home, name) {
            if ptr.is_file() {
                if let Ok(text) = std::fs::read_to_string(&ptr) {
                    if let Ok(v) = serde_json::from_str::<Value>(&text) {
                        if v.get("system_id").and_then(Value::as_str) == Some(sid) {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

/// `judge_deliverable(url)` —— 云端能不能 POST 到这个 URL（**话说明白**的判据）。
///
/// 关键：`_detect_webhook_host` 返回的是**裸 host**（如 `127.0.0.1`）⇒ 这里必然判
/// 不可达 ⇒ `repair` 实际**从不写** `webhook_host`（只打告警）。文案逐字照抄
/// `cli/deploy_bridge.py:328-350`。
pub fn judge_deliverable(url: &str) -> (bool, String) {
    let raw = url.trim();
    if raw.is_empty() {
        return (
            false,
            "the URL is empty (pull mode: nothing is registered)".to_string(),
        );
    }
    let bare = || {
        format!(
            "'{raw}' is not an absolute http(s) URL — a bare host or host:port cannot be POSTed to (reqwest: builder error)"
        )
    };
    let Some((scheme, rest)) = raw.split_once("://") else {
        return (false, bare());
    };
    let scheme = scheme.to_lowercase();
    if scheme != "http" && scheme != "https" {
        return (false, bare());
    }
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.contains('@') {
        return (false, format!("'{raw}' carries userinfo"));
    }
    let host = host_of(authority);
    if host.is_empty() {
        return (false, format!("'{raw}' carries no host"));
    }
    if host == "0.0.0.0" || host == "::" {
        return (
            false,
            format!(
                "'{raw}' points at {host} — a bind/unspecified address, not a destination (U2: nothing can deliver there)"
            ),
        );
    }
    if let Some(p) = port_of(authority) {
        if !(1..=65535).contains(&p) {
            return (false, format!("'{raw}' carries an invalid port"));
        }
    }
    (true, String::new())
}

fn host_of(authority: &str) -> String {
    let a = authority.rsplit('@').next().unwrap_or(authority);
    if let Some(rest) = a.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_lowercase();
    }
    a.split(':').next().unwrap_or("").to_lowercase()
}

fn port_of(authority: &str) -> Option<i64> {
    let a = authority.rsplit('@').next().unwrap_or(authority);
    if a.starts_with('[') {
        return a
            .split(']')
            .nth(1)
            .and_then(|r| r.strip_prefix(':'))
            .and_then(|p| p.parse::<i64>().ok());
    }
    a.split(':').nth(1).and_then(|p| p.parse::<i64>().ok())
}

/// 公开版：install 的"是否本地网关"判定与 repair 共用**同一份** host 解析（单真源）。
pub fn url_host_pub(url: &str) -> String {
    url_host(url)
}

fn url_host(url: &str) -> String {
    let after = url.split("://").nth(1).unwrap_or("");
    let authority = after.split(['/', '?', '#']).next().unwrap_or("");
    host_of(authority)
}

fn is_loopback_host(host: &str) -> bool {
    matches!(host, "127.0.0.1" | "localhost" | "::1" | "ip6-localhost")
}

/// 私网判定（`ipaddress.ip_address(h).is_private` 的常用等价）。
fn is_private_host(host: &str) -> bool {
    if is_loopback_host(host) {
        return true;
    }
    match host.parse::<std::net::IpAddr>() {
        Ok(std::net::IpAddr::V4(v4)) => v4.is_private() || v4.is_link_local(),
        // fc00::/7（唯一本地）与 fe80::/10（链路本地）—— 不用 is_unique_local(1.84 才稳)
        Ok(std::net::IpAddr::V6(v6)) => {
            let head = v6.segments()[0];
            (head & 0xfe00) == 0xfc00 || (head & 0xffc0) == 0xfe80
        }
        Err(_) => false,
    }
}

/// 本机主 LAN IP（UDP connect 取本地端；失败再退 `ip -4 -brief addr`）。
pub fn detect_lan_ip() -> String {
    if let Ok(s) = std::net::UdpSocket::bind("0.0.0.0:0") {
        if s.connect("8.8.8.8:80").is_ok() {
            if let Ok(addr) = s.local_addr() {
                let ip = addr.ip().to_string();
                if !ip.starts_with("0.0.0.0") {
                    return ip;
                }
            }
        }
    }
    if let Ok(out) = std::process::Command::new("ip")
        .args(["-4", "-brief", "addr", "show", "scope", "global"])
        .output()
    {
        if out.status.success() {
            for line in String::from_utf8_lossy(&out.stdout).lines() {
                for p in line.split_whitespace() {
                    if p.contains('/') && p.chars().next().is_some_and(|c| c.is_ascii_digit()) {
                        let ip = p.split('/').next().unwrap_or("");
                        if !ip.starts_with("127.") {
                            return ip.to_string();
                        }
                    }
                }
            }
        }
    }
    String::new()
}

/// `_detect_webhook_host(gateway_url)`：网关回调本机时该用哪个 host
/// （`cli/setup_system.py:209-320`）。返回**裸 host**（外网兜底探测同 Python）。
pub fn detect_webhook_host(gateway_url: &str) -> String {
    let gw_host = url_host(gateway_url);
    if gw_host.is_empty() {
        return "127.0.0.1".to_string();
    }
    let lan_ip = detect_lan_ip();
    if is_loopback_host(&gw_host) {
        return "127.0.0.1".to_string();
    }
    if !lan_ip.is_empty() && gw_host == lan_ip {
        return lan_ip;
    }
    if is_private_host(&gw_host) {
        return if lan_ip.is_empty() {
            "127.0.0.1".to_string()
        } else {
            lan_ip
        };
    }
    use std::net::ToSocketAddrs;
    if let Ok(mut it) = (gw_host.as_str(), 0u16).to_socket_addrs() {
        if let Some(addr) = it.next() {
            let resolved = addr.ip().to_string();
            if is_loopback_host(&resolved) {
                return "127.0.0.1".to_string();
            }
            if !lan_ip.is_empty() && resolved == lan_ip {
                return lan_ip;
            }
            if is_private_host(&resolved) {
                return if lan_ip.is_empty() {
                    "127.0.0.1".to_string()
                } else {
                    lan_ip
                };
            }
        }
    }
    // 公网：外网探测（与 Python 同：https://ifconfig.me, UA curl/7.0）
    if let Ok((code, body)) = crate::core::http::raw_req("https://ifconfig.me", None, None, 5) {
        if code == 200 {
            let ext = body.trim();
            if !ext.is_empty() && !is_private_host(ext) {
                return ext.to_string();
            }
        }
    }
    "127.0.0.1".to_string()
}

fn step_ok(msg: &str) {
    println!("  {GREEN}✓{NC} {msg}");
}
fn step_warn(msg: &str) {
    println!("  {YELLOW}⚠{NC} {msg}");
}
fn step_fail(msg: &str) {
    println!("  {RED}✗{NC} {msg}");
}

/// `_repair_gateway_config`：只**补空**（system_home / webhook_host），绝不覆盖既有值。
///
/// 在**原始 JSON 映射**上改（不经过 typed 结构）：Python 是 dict 变更加 append，
/// 新键落在**末尾**、既有键序不变 ⇒ 这样落盘才能与 Python **字节等价**。
/// 写入面 = CLI 自有权（系统级文件），pretty(2 空格) 无尾换行 + 0600 原子写。
pub fn repair_gateway_config(
    aimail_home: &Path,
    user_home: &Path,
    sid: &str,
    args_home: &str,
) -> bool {
    let gw_path = crate::core::config::gateway_config_path_in(aimail_home, sid);
    if !gw_path.is_file() {
        step_fail(&format!(
            "gateway config does not exist: {}",
            gw_path.display()
        ));
        return false;
    }
    let Ok(text) = std::fs::read_to_string(&gw_path) else {
        return false;
    };
    let Ok(Value::Object(mut cfg)) = serde_json::from_str::<Value>(&text) else {
        return false;
    };
    let get_str = |m: &serde_json::Map<String, Value>, k: &str| -> String {
        m.get(k).and_then(Value::as_str).unwrap_or("").to_string()
    };
    let mut changed = false;
    let stored = {
        let v = get_str(&cfg, "system_home");
        if v.is_empty() {
            None
        } else {
            Some(v)
        }
    };
    let root = if args_home.is_empty() {
        auto_platform_home(user_home, stored.as_deref())
    } else {
        args_home.to_string()
    };
    if get_str(&cfg, "system_home").is_empty() {
        if !root.is_empty() && platforms::detect_platform_from_home(Path::new(&root)) != "unknown" {
            cfg.insert("system_home".to_string(), Value::String(root.clone()));
            step_ok(&format!("system_home backfilled: {root}"));
            changed = true;
        } else {
            step_warn("system_home missing and the platform root cannot be determined (on a multi-platform machine pass --home)");
        }
    }
    if get_str(&cfg, "webhook_host").is_empty() {
        let wh = detect_webhook_host(&get_str(&cfg, "gateway_url"));
        let (deliverable, why) = judge_deliverable(&wh);
        if !wh.is_empty() && deliverable {
            cfg.insert("webhook_host".to_string(), Value::String(wh.clone()));
            step_ok(&format!("webhook_host backfilled: {wh}"));
            changed = true;
        } else if !wh.is_empty() {
            step_warn(&format!(
                "detected callback address '{wh}' is not a deliverable http(s) URL ({why}) — leaving webhook_host unset (no bridge entry; registration keeps the local endpoint)"
            ));
        }
    }
    if changed && crate::core::config::write_private_json(&gw_path, &Value::Object(cfg)).is_err() {
        step_fail(&format!(
            "gateway config write failed: {}",
            gw_path.display()
        ));
        return false;
    }
    changed
}

/// `_repair_pointer`：平台根确定 + 本 sid 无指针 + 目标指针文件不存在 ⇒ 建指针。
pub fn repair_pointer(
    aimail_home: &Path,
    user_home: &Path,
    sid: &str,
    platform_home: &str,
) -> bool {
    if sid_has_pointer(user_home, sid) {
        return false;
    }
    if platform_home.is_empty() {
        return false;
    }
    let plat = platforms::detect_platform_from_home(Path::new(platform_home));
    if plat == "unknown" {
        return false;
    }
    // 绑定枚举（**只读**）：取第一个有 email 的
    let sysdir = crate::core::config::system_dir_in(aimail_home, sid);
    let mut email = String::new();
    let mut subs: Vec<std::path::PathBuf> = std::fs::read_dir(&sysdir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    subs.sort();
    for sub in subs {
        let aj = sub.join(crate::core::contract::binding_file());
        if !aj.is_file() {
            continue;
        }
        if let Ok(text) = std::fs::read_to_string(&aj) {
            if let Ok(v) = serde_json::from_str::<Value>(&text) {
                if let Some(e) = v.get("email").and_then(Value::as_str) {
                    if !e.is_empty() {
                        email = e.to_string();
                        break;
                    }
                }
            }
        }
    }
    if email.is_empty() {
        return false;
    }
    let ptr = platforms::platform_root(user_home, plat).join(crate::core::contract::pointer_file());
    if ptr.exists() {
        return false; // 已有指针（可能是别的系统）—— 不覆盖
    }
    if let Some(parent) = ptr.parent() {
        if std::fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let body = serde_json::json!({"system_id": sid, "email": email});
    let Ok(text) = serde_json::to_string_pretty(&body) else {
        return false;
    };
    if std::fs::write(&ptr, text).is_err() {
        return false;
    }
    let _ = crate::core::perms::set_user_only(&ptr);
    step_ok(&format!("pointer created: {} → {sid}", ptr.display()));
    true
}

/// 与 `aimail_gateway.json` 同义的网关客户端（`repair.py:72-80` 的 `_gateway_client`）。
/// 缺 `gateway_url`/`admin_key` ⇒ None（调用方据此打印"客户端不可用"的提示，而不是抛错）。
pub fn gateway_client(sid: &str) -> Option<crate::core::gateway::GatewayClient> {
    let gw = config::load_gateway_config_in(&crate::core::home::aimail_home(), sid)?;
    let obj = gw.to_json();
    let url = obj.get("gateway_url").and_then(Value::as_str).unwrap_or("");
    let key = obj.get("admin_key").and_then(Value::as_str).unwrap_or("");
    if url.is_empty() || key.is_empty() {
        return None;
    }
    Some(crate::core::gateway::GatewayClient::with_defaults(url, key))
}

/// pending 查询返回体 → `batches` 列表（`repair.py:281`：`pend["batches"]`，退回顶层数组/`data`）。
pub fn pending_batches(pend: &Value) -> Vec<Value> {
    if let Some(Value::Array(a)) = pend.get("batches") {
        return a.clone();
    }
    if let Value::Array(a) = pend {
        return a.clone();
    }
    if let Some(Value::Array(a)) = pend.get("data") {
        return a.clone();
    }
    Vec::new()
}

/// 空签名证据：headers 里既无 `X-Webhook-Signature` 也无 `-V2` 的投递 ⇒ `(id, email)`
/// （`repair.py:282-290`；`id` 可能是 None = --deep 的人造目标）。
pub fn pending_empties(pend: &Value) -> Vec<(Option<String>, String)> {
    let mut out = Vec::new();
    for b in pending_batches(pend) {
        let Some(deliveries) = b.get("deliveries").and_then(Value::as_array) else {
            continue;
        };
        for d in deliveries {
            let hs = d
                .get("headers")
                .and_then(Value::as_str)
                .and_then(|t| serde_json::from_str::<Value>(t).ok())
                .unwrap_or_else(|| serde_json::json!({}));
            let has_sig = hs
                .get("X-Webhook-Signature")
                .map(|v| !v.is_null())
                .unwrap_or(false)
                || hs
                    .get("X-Webhook-Signature-V2")
                    .map(|v| !v.is_null())
                    .unwrap_or(false);
            if !has_sig {
                out.push((
                    d.get("id").map(|v| {
                        v.as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| v.to_string())
                    }),
                    d.get("email")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                ));
            }
        }
    }
    out
}

/// 卡住判定（`repair.py:355-366`）：`created_at` 龄期 > 600s ⇒ 收集 id；时间不可解析 ⇒ 跳过该条。
pub fn stuck_pending_ids(pend: &Value, now: i64) -> Vec<String> {
    let mut stuck = Vec::new();
    for b in pending_batches(pend) {
        let Some(deliveries) = b.get("deliveries").and_then(Value::as_array) else {
            continue;
        };
        for d in deliveries {
            let created = d.get("created_at").and_then(Value::as_str).unwrap_or("");
            let Some(ts) = crate::core::time::parse_rfc3339_secs(created) else {
                continue;
            };
            if now - ts > 600 {
                if let Some(id) = d.get("id") {
                    stuck.push(
                        id.as_str()
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| id.to_string()),
                    );
                }
            }
        }
    }
    stuck
}

/// `_drain_stuck`（`repair.py:344-373`，`--deep` 的第 11 步）：把卡住的 pending 全部 ack 兜底。
pub fn drain_stuck(client: &Option<crate::core::gateway::GatewayClient>) -> bool {
    let Some(c) = client else {
        return false;
    };
    let pend = c.post(
        "/api/v1/admin/pending",
        &serde_json::json!({"filter": [], "emails": []}),
    );
    if let Some(err) = pend.get("error") {
        warn(&format!("pending query failed: {err}"));
        return false;
    }
    let stuck = stuck_pending_ids(&pend, crate::core::time::now_secs());
    for id in &stuck {
        let res = c.post(
            "/api/v1/admin/pending/ack",
            &serde_json::json!({"ids": [id]}),
        );
        match res.get("error") {
            Some(e) => warn(&format!("ack {id} failed: {e}")),
            None => ok(&format!("ack stuck pending id={id}")),
        }
    }
    !stuck.is_empty()
}

/// `_detect_platform_from_home`（`repair.py:394-407`）：**repaired 侧的手写判定变体**。
///
/// 与另两处平台判定**不同**（各自照抄，不合并）：hermes 用 `markers 任一` 的 OR；openclaw 看
/// `openclaw.json` 文件；deerflow 看 `backend/app/gateway` 目录。名字是平台语义（注册表只描述
/// 特征，表达不了这里的 OR/文件/目录三种形状）。
fn detect_platform_for_repair(dir: &Path) -> &'static str {
    let name = dir
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    if name == ".pi" && dir.join("agent").is_dir() {
        return "pi";
    }
    if name == ".dsh" && dir.join("profiles").is_dir() && dir.join("storages").is_dir() {
        return "dsh";
    }
    if dir.join("hermes-agent").exists() || dir.join("profiles").is_dir() {
        return "hermes";
    }
    if dir.join("openclaw.json").is_file() {
        return "openclaw";
    }
    if dir.join("backend").join("app").join("gateway").is_dir() {
        return "deerflow";
    }
    "unknown"
}

/// `_failing_file_checks`（`repair.py:570-596`）：注册表里**文件类** health_checks 中"没有命中"的项。
///
/// 三个定位键都必须认（`path` / `alt` / `glob` —— openclaw 的插件目录检查用 glob，漏认它会把
/// "资源在位"误读成"缺失"）。`glob_dir_any` = 目录类，命中即算在位（不做内容匹配）。
fn failing_file_checks(plat: &str, sh: &str, user_home: &Path) -> Vec<Value> {
    const FILE_KINDS: &[&str] = &[
        "file_contains",
        "file_contains_alt",
        "file_exists",
        "file_exists_any",
        "glob_dir_any",
    ];
    let mut fails = Vec::new();
    for ch in platforms::health_checks(plat) {
        let kind = ch.get("kind").and_then(Value::as_str).unwrap_or("");
        if !FILE_KINDS.contains(&kind) {
            continue;
        }
        let pat = ["path", "alt", "glob"]
            .iter()
            .find_map(|k| ch.get(*k).and_then(Value::as_str))
            .unwrap_or("")
            .replace("{home}", sh)
            .replace("{user_home}", &user_home.to_string_lossy());
        let mut ok = false;
        if let Ok(paths) = glob::glob(&pat) {
            for cand in paths.flatten() {
                if kind == "glob_dir_any" {
                    ok = true;
                    break;
                }
                if kind == "file_exists" || kind == "file_exists_any" {
                    if cand.is_file() {
                        ok = true;
                        break;
                    }
                    continue;
                }
                let marker = ch.get("marker").and_then(Value::as_str).unwrap_or("");
                if let Ok(text) = std::fs::read_to_string(&cand) {
                    if text.contains(marker) {
                        ok = true;
                        break;
                    }
                }
            }
        }
        if !ok {
            fails.push(ch);
        }
    }
    fails
}

/// `_repair_runtime_resources`（`repair.py:612-651`）：L2 运行时资源缺失 → 经该平台的**自足 SDK 安装入口**
/// 幂等重铺（`install.py install --type <tgt> --home <sh> --system-id <sid>`）。
///
/// 关键照抄点：① 平台根解析不出 ⇒ 跳过并说明（远端平台如 deerflow 要在宿主上跑）；
/// ② 该平台**没有** sdk_install 入口 ⇒ 只打印注册表自带的 fix 提示，**不**spawn 一个注定失败的安装；
/// ③ 安装输出取尾部 600 字符原样转发；④ 一律**幂等**（资源齐全时返回 false 不动作）。
pub fn runtime_resources(sid: &str, platform_home: &str, prog_root: &Path) -> bool {
    let ah = crate::core::home::aimail_home();
    let gw = config::load_gateway_config_in(&ah, sid)
        .map(|c| c.to_json())
        .unwrap_or_default();
    let sh = if platform_home.is_empty() {
        gw.get("system_home")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        platform_home.to_string()
    };
    if sh.is_empty() || !Path::new(&sh).is_dir() {
        warn("platform root not resolvable, skipping the runtime-resource redeploy (for remote platforms such as deerflow run it on the host)");
        return false;
    }
    let plat = detect_platform_for_repair(Path::new(&sh));
    if plat == "unknown" {
        warn("platform type not recognised (the root is neither a known platform nor a self-built one) -> skipping the runtime-resource redeploy");
        return false;
    }
    let fails = failing_file_checks(plat, &sh, &crate::core::home::user_home());
    if fails.is_empty() {
        ok(&format!("{plat} runtime resources ok"));
        return false;
    }
    let tgt = platforms::sdk_install_target(plat);
    if tgt.is_empty() {
        warn(&format!(
            "{plat} runtime resources missing; this platform has no SDK install entry (its own installer manages them) -> follow the hint:"
        ));
        for ch in &fails {
            let id = ch.get("id").and_then(Value::as_str).unwrap_or("?");
            let txt = ch
                .get("fix")
                .and_then(Value::as_str)
                .or_else(|| ch.get("fail_text").and_then(Value::as_str))
                .unwrap_or("");
            warn(&format!("  [{id}] {txt}"));
        }
        return false;
    }
    warn(&format!(
        "{plat} runtime resources missing -> idempotent reinstall (install.py install --type {tgt} --home {sh})"
    ));
    // 按**文件路径**调用（与每一步一致），不依赖解释器里 `import aimail`；install.py 自己会做 sys.path 自举，
    // 因而仓库(pysdk/) 与 pip(site-packages/aimail/) 两种布局都成立。
    // 按**文件路径**调用（与每一步一致），不依赖解释器里 `import aimail`；install.py 自己会做
    // sys.path 自举，因而仓库(pysdk/) 与 pip(site-packages/aimail/) 两种布局都成立。
    let py = std::env::var("AIMAIL_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let install_py = prog_root
        .join("aimail-src")
        .join("pysdk")
        .join("install.py");
    let mut args: Vec<String> = Vec::new();
    if install_py.is_file() {
        args.push(install_py.to_string_lossy().to_string());
    } else {
        args.push("-m".to_string());
        args.push("aimail.install".to_string());
    }
    args.extend([
        "install".to_string(),
        "--type".to_string(),
        tgt.clone(),
        "--home".to_string(),
        sh.clone(),
        "--system-id".to_string(),
        sid.to_string(),
    ]);
    match sdk::run_program(
        Path::new(&py),
        &args,
        std::time::Duration::from_secs(300),
        &[],
    ) {
        Ok((out, _err, 0)) => {
            let text = String::from_utf8_lossy(&out);
            let tail: String = text
                .chars()
                .rev()
                .take(600)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            print!("{tail}");
            ok("runtime resources reinstalled");
            true
        }
        Ok((_, err, code)) => {
            let text = String::from_utf8_lossy(&err);
            let tail: String = text
                .chars()
                .rev()
                .take(200)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            fail(&format!("reinstall failed (exit {code}): {tail}"));
            false
        }
        Err(e) => {
            fail(&format!("reinstall failed: {}", e.display_like_python()));
            false
        }
    }
}

/// `_repair_agentmail_json`（`repair.py:655-717`）：补绑定文件的可重建字段 + 与**活**路由对齐
/// `webhook_url`；写回**经 SDK 门** `backfill_binding`（per-agent 绑定只由 SDK 写 —— 归属铁律）。
///
/// 照抄点：① 字段补齐以网关配置为准，只补**空**的；② 路由表逐行解析（`k = v`，去引号/逗号，后写覆盖先写）；
/// ③ 对齐条件 = 路由目标存活 + 目标是本机地址 + 声明值与目标不同（存活的声明值不动）；
/// ④ 比对原字典决定是否写（值相等即不写，幂等）；⑤ 单个文件失败只跳过该文件。
pub fn agentmail_backfill(sid: &str, aimail_home: &Path) -> bool {
    agentmail_backfill_with(sid, aimail_home, &crate::core::home::program_root(), &[])
}

/// 同上，但门的**程序根**与**子进程环境**可注入（测试用夹具根 + 夹具 AIMAIL_HOME，
/// 既走"同源"分支又不改本进程环境 ⇒ 并行安全）。
pub fn agentmail_backfill_with(
    sid: &str,
    aimail_home: &Path,
    prog_root: &Path,
    door_env: &[(String, String)],
) -> bool {
    let gw = config::load_gateway_config_in(aimail_home, sid)
        .map(|c| c.to_json())
        .unwrap_or_default();
    let sysdir = config::system_dir_in(aimail_home, sid);
    if !sysdir.is_dir() {
        return false;
    }
    // 路由表（target url）—— k = v 逐行；去引号、去尾逗号；后写覆盖先写
    let mut routes: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    if let Ok(text) = std::fs::read_to_string(aimail_home.join("bridge").join("aimail_routes.toml"))
    {
        for raw in text.lines() {
            let line = raw.trim();
            if line.contains('=') && !line.starts_with('#') {
                let (k, v) = line.split_once('=').unwrap();
                let key = unquote(k.trim());
                let val = unquote(v.trim().trim_end_matches(','));
                routes.insert(key, val);
            }
        }
    }

    let mut dirs: Vec<std::path::PathBuf> = std::fs::read_dir(&sysdir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    let binding_name = contract::binding_file();
    let mut changed = false;
    for dir in dirs {
        let binding = dir.join(binding_name);
        let Ok(text) = std::fs::read_to_string(&binding) else {
            continue;
        };
        let Ok(Value::Object(mut d)) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let orig = d.clone();
        let dir_name = dir
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        // ① 补空字段（网关配置为准）
        for k in [
            "gateway_url",
            "domain",
            "system_id",
            "system_name",
            "manager_address",
        ] {
            let cur_empty = d
                .get(k)
                .and_then(Value::as_str)
                .map(|v| v.is_empty())
                .unwrap_or(true);
            if !cur_empty {
                continue;
            }
            if let Some(v) = gw.get(k) {
                let v_empty = v.as_str().map(|x| x.is_empty()).unwrap_or(v.is_null());
                if !v_empty {
                    d.insert(k.to_string(), v.clone());
                }
            }
        }
        // ② 与活路由对齐 webhook_url
        let email = d
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let target = routes.get(&email).cloned().unwrap_or_default();
        let declared = d
            .get("webhook_url")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let host = url_host(&target);
        let local = matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1");
        if !target.is_empty() && route_alive(&target) && local {
            if !declared.is_empty() && !route_alive(&declared) {
                d.insert("webhook_url".to_string(), Value::String(target.clone()));
                ok(&format!(
                    "{dir_name}: webhook_url {declared} -> {target} (declared dead, route alive)"
                ));
            } else if !declared.is_empty() && trim_slash(&declared) != trim_slash(&target) {
                d.insert("webhook_url".to_string(), Value::String(target.clone()));
                ok(&format!(
                    "{dir_name}: webhook_url aligned to route target {target}"
                ));
            }
        }
        if d != orig {
            // 写回经 SDK 门（原子 tmp+rename+0600 语义在 SDK 内）；单文件失败只跳过该文件
            let args = serde_json::json!({"binding": Value::Object(d), "system_id": sid});
            match sdk::sdk_ops_call(
                "backfill_binding",
                &args,
                prog_root,
                std::time::Duration::from_secs(120),
                door_env,
            ) {
                Ok(_) => changed = true,
                Err(e) => warn(&format!(
                    "{dir_name}: {binding_name} write failed ({}) -> skipping that file",
                    e.display_like_python()
                )),
            }
        }
    }
    if !changed {
        ok(&format!(
            "{binding_name} ok (fields complete and webhook_url aligned)"
        ));
    }
    changed
}

/// 探测端点是否存活（`repair.py:671-680`）：POST 空体、3s 超时；HTTP 404 = 死，其余状态码 = 活。
fn route_alive(url: &str) -> bool {
    if url.is_empty() || !url.starts_with("http") {
        return false;
    }
    match crate::core::http::raw_req(url, Some(b""), None, 3) {
        Ok((code, _)) => code != 404,
        Err(_) => false,
    }
}

/// 去一层引号（`k = "v",` 这类 toml 简式解析用）。
fn unquote(s: &str) -> String {
    let t = s.trim();
    let t = t.strip_prefix('"').unwrap_or(t);
    let t = t.strip_suffix('"').unwrap_or(t);
    t.trim().to_string()
}

fn trim_slash(url: &str) -> &str {
    url.trim_end_matches('/')
}

/// `repair.py:_ensure_bridge_running`：**先判模式**（本地网关=直连，无需桥）再探进程，
/// 死了才幂等起（模式判定与 install 共用 `gateway::is_local_gateway` —— 2026-09-27 owner 裁决）。
fn ensure_bridge_running(sid: &str) -> bool {
    let sid = sid.trim();
    let gw_url = if sid.is_empty() {
        String::new()
    } else {
        crate::core::config::load_gateway_config_in(&crate::core::home::aimail_home(), sid)
            .map(|c| c.gateway_url)
            .unwrap_or_default()
    };
    if !gw_url.is_empty() && crate::core::gateway::is_local_gateway(&gw_url) {
        ok("bridge: local gateway (direct mode) -- no bridge needed");
        return true;
    }
    let pids = crate::core::bridge_deploy::bridge_pids_for_probe();
    if !pids.is_empty() {
        ok(&format!("bridge ok (already running pid={})", pids[0]));
        return true;
    }
    warn("bridge not running -> starting it (deploy_bridge.start_bridge)");
    let cfg = crate::core::bridge_wire::bridge_cfg_file();
    // Python: `BRIDGE_BIN = AIMAIL_HOME/bridge/bin/aimail-bridge`（**带 bin/ 一级**；
    // 直接按 cfg.parent() 拼会误判"未部署" ⇒ BridgeAlive 假失败，L2 实测抓到）。
    let bin = cfg
        .parent()
        .map(|d| d.join("bin").join("aimail-bridge"))
        .unwrap_or_else(|| std::path::PathBuf::from("aimail-bridge"));
    if !cfg.exists() || !bin.exists() {
        fail("bridge not deployed (config/binary missing) -- run install first");
        return false;
    }
    if crate::core::bridge_deploy::start_bridge(
        &bin.to_string_lossy(),
        &cfg.to_string_lossy(),
        &crate::core::bridge_wire::bridge_pid_file().to_string_lossy(),
    ) {
        let pid = std::fs::read_to_string(crate::core::bridge_wire::bridge_pid_file())
            .unwrap_or_default()
            .trim()
            .to_string();
        ok(&format!(
            "bridge started (pid={})",
            if pid.is_empty() { "?".into() } else { pid }
        ));
        true
    } else {
        fail("bridge failed to start -- check ~/.aimail/bridge/aimail-bridge.log");
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(level: &str, check: &str, pass: bool) -> Record {
        Record {
            level: level.to_string(),
            check: check.to_string(),
            pass,
            detail: String::new(),
            fix: String::new(),
        }
    }

    #[test]
    fn entry_admin_key_replacement_handles_both_field_orders() {
        // 字段序 ①：system_id 在前
        let a = r#"[[pull.systems]]
{ system_id = "s1", admin_key = "OLD", aimail_url = "http://x" }"#;
        let ra = replace_entry_admin_key(a, "s1", "NEW").expect("序①应命中");
        assert!(ra.contains("admin_key = \"NEW\""), "{ra}");
        assert!(ra.contains("system_id = \"s1\""), "不改动 system_id");
        // 字段序 ②：admin_key 在前
        let b = r#"[[pull.systems]]
{ admin_key = "OLD", system_id = "s1" }"#;
        let rb = replace_entry_admin_key(b, "s1", "NEW").expect("序②应命中");
        assert!(rb.contains("admin_key = \"NEW\""), "{rb}");
        // 不同 sid 的条目不动
        let c = r#"{ system_id = "other", admin_key = "KEEP" }"#;
        assert!(replace_entry_admin_key(c, "s1", "NEW").is_none());
    }

    #[test]
    fn pointer_never_overwrites_an_existing_pointer_for_this_sid() {
        // cli/repair.py:522：本 sid 已有指针 => 直接返回，绝不改写。
        let td = std::env::temp_dir().join(format!("ptr-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        std::fs::create_dir_all(td.join(".pi")).unwrap();
        let ptr = td.join(".pi").join(crate::core::contract::pointer_file());
        let orig = br#"{"system_id": "s1", "email": "keep@x"}"#.to_vec();
        std::fs::write(&ptr, &orig).unwrap();
        std::env::set_var("HOME", td.to_string_lossy().to_string());
        std::env::set_var("AIMAIL_HOME", td.to_string_lossy().to_string());
        let _ = run(
            "s1",
            false,
            true,
            "/nonexistent-platform-home",
            |sid: &str, ah: Option<&std::path::Path>| {
                crate::cmd::check::engine(sid, ah, false).check
            },
        );
        assert_eq!(std::fs::read(&ptr).unwrap(), orig, "已有指针绝不被覆写");
        std::fs::remove_dir_all(&td).ok();
    }

    #[test]
    fn routes_entries_skips_bad_binding_files_without_aborting() {
        // Python 2026-09-20 实测：单个坏绑定文件只跳过它，绝不中止整步（repair.py:740）。
        let td = std::env::temp_dir().join(format!("rt-bad-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        std::fs::create_dir_all(td.join("systems/s1/leaf")).unwrap();
        std::fs::write(
            td.join("systems/s1/leaf")
                .join(crate::core::contract::binding_file()),
            b"{not json",
        )
        .unwrap();
        std::env::set_var("AIMAIL_HOME", td.to_string_lossy().to_string());
        let before = std::fs::read(
            td.join("systems/s1/leaf")
                .join(crate::core::contract::binding_file()),
        )
        .unwrap();
        let _ = run(
            "s1",
            false,
            true,
            "",
            |sid: &str, ah: Option<&std::path::Path>| {
                crate::cmd::check::engine(sid, ah, false).check
            },
        );
        let after = std::fs::read(
            td.join("systems/s1/leaf")
                .join(crate::core::contract::binding_file()),
        )
        .unwrap();
        assert_eq!(before, after, "坏文件不得被改写/删除");
        std::fs::remove_dir_all(&td).ok();
    }

    #[test]
    fn unregistered_dimension_is_hint_with_explicit_note() {
        let r = repairability("nope", "nope");
        assert_eq!(r, UNREGISTERED);
        assert!(!r.is_auto());
    }

    #[test]
    fn residual_splits_auto_from_hint() {
        let residual = vec![
            rec("gateway", "health", false), // hint
            rec("config", "pointer", false), // auto
            rec("agent", "hook", false),     // auto
            rec("agent", "register", false), // hint
        ];
        let (defects, hints) = classify_residual(&residual);
        let names = |v: &Vec<Record>| v.iter().map(|c| c.check.clone()).collect::<Vec<_>>();
        assert_eq!(names(&defects), vec!["pointer", "hook"]);
        assert_eq!(names(&hints), vec!["health", "register"]);
    }

    #[test]
    fn ladder_order_matches_python_and_deep_appends_drain() {
        let base = ladder(false);
        assert_eq!(base.len(), 10);
        assert_eq!(base[0], Step::BridgeAlive);
        assert_eq!(base[9], Step::PullEntryKey);
        let deep = ladder(true);
        assert_eq!(deep.len(), 11);
        assert_eq!(deep[10], Step::DrainStuck);
    }

    #[test]
    fn sdk_install_target_matches_registry() {
        // 注册表口径：dsh/hermes/deerflow 有自足安装入口；pi/openclaw 资源自己管（无入口）
        assert_eq!(platforms::sdk_install_target("hermes"), "hermes");
        assert_eq!(platforms::sdk_install_target("dsh"), "dsh");
        assert_eq!(platforms::sdk_install_target("deerflow"), "deerflow");
        assert_eq!(platforms::sdk_install_target("pi"), "");
        assert_eq!(platforms::sdk_install_target("openclaw"), "");
        assert_eq!(platforms::sdk_install_target("nope"), "");
    }

    #[test]
    fn detect_platform_for_repair_mirrors_python_variant() {
        let d = tempfile::tempdir().unwrap();
        // hermes 的 OR 语义：只要 profiles/ 在（无 hermes-agent）也算 hermes —— 与注册表驱动那两处不同
        let h = d.path().join(".hermes");
        std::fs::create_dir_all(h.join("profiles")).unwrap();
        assert_eq!(detect_platform_for_repair(&h), "hermes");
        // pi 需 名字 .pi + agent/；dsh 需 名字 .dsh + profiles/ + storages/
        let pi = d.path().join(".pi");
        std::fs::create_dir_all(pi.join("agent")).unwrap();
        assert_eq!(detect_platform_for_repair(&pi), "pi");
        let dsh = d.path().join(".dsh");
        std::fs::create_dir_all(dsh.join("profiles")).unwrap();
        std::fs::create_dir_all(dsh.join("storages")).unwrap();
        assert_eq!(detect_platform_for_repair(&dsh), "dsh");
        // openclaw 看文件；deerflow 看目录
        let oc = d.path().join(".openclaw");
        std::fs::create_dir_all(&oc).unwrap();
        std::fs::write(oc.join("openclaw.json"), "{}").unwrap();
        assert_eq!(detect_platform_for_repair(&oc), "openclaw");
        let df = d.path().join("deer-flow");
        std::fs::create_dir_all(df.join("backend/app/gateway")).unwrap();
        assert_eq!(detect_platform_for_repair(&df), "deerflow");
        assert_eq!(
            detect_platform_for_repair(&d.path().join("nope")),
            "unknown"
        );
    }

    #[test]
    fn failing_file_checks_honours_glob_and_marker() {
        let d = tempfile::tempdir().unwrap();
        let home = d.path().join(".hermes");
        std::fs::create_dir_all(&home).unwrap();
        // 资源全缺 ⇒ hermes 的文件类检查应有命中失败项
        let missing = failing_file_checks("hermes", &home.to_string_lossy(), d.path());
        assert!(!missing.is_empty(), "缺资源时应报出失败项");
        // 注册表自带的 fix 提示必须原样可读（供 repair 打印）
        assert!(missing
            .iter()
            .all(|ch| ch.get("id").is_some() || ch.get("path").is_some()));
    }

    #[test]
    fn runtime_resources_skips_when_root_unresolvable() {
        // 平台根解析不出 ⇒ 明确跳过（不 spawn 安装、不报成功）
        let d = tempfile::tempdir().unwrap();
        let ah = d.path().join("aimail");
        std::fs::create_dir_all(ah.join("systems/s1")).unwrap();
        std::env::set_var("AIMAIL_HOME", &ah);
        let r = runtime_resources("s1", "/nonexistent-platform-root", &ah);
        std::env::remove_var("AIMAIL_HOME");
        assert!(!r);
    }

    #[test]
    fn pending_batches_and_empties_match_python_reads() {
        let pend = serde_json::json!({"batches": [{"deliveries": [
            {"id": "d1", "email": "a@example.test", "headers": "{\"X-Webhook-Signature\": \"sig\"}"},
            {"id": "d2", "email": "b@example.test", "headers": "{}"},
            {"id": "d3", "email": "c@example.test", "headers": "not-json"},
            {"id": "d4", "email": "d@example.test", "headers": "{\"X-Webhook-Signature-V2\": \"v2\"}"}
        ]}]});
        assert_eq!(pending_batches(&pend).len(), 1);
        // 空签名证据 = d2（无签名头）+ d3（headers 非 JSON ⇒ 视作 {} ⇒ 也无签名），d1/d4 有签名
        let empties = pending_empties(&pend);
        let ids: Vec<String> = empties.iter().filter_map(|(i, _)| i.clone()).collect();
        assert_eq!(ids, vec!["d2".to_string(), "d3".to_string()]);
        assert_eq!(empties[0].1, "b@example.test");
        // 顶层数组 / data 数组两种返回形状都要认
        assert_eq!(
            pending_batches(&serde_json::json!([{"deliveries": []}])).len(),
            1
        );
        assert_eq!(
            pending_batches(&serde_json::json!({"data": [{"deliveries": []}]})).len(),
            1
        );
        assert!(pending_batches(&serde_json::json!({"ok": true})).is_empty());
    }

    #[test]
    fn stuck_pending_ids_uses_600s_age_and_skips_unparseable() {
        let now = crate::core::time::parse_rfc3339_secs("2026-10-04T12:00:00Z").unwrap();
        let pend = serde_json::json!({"batches": [{"deliveries": [
            {"id": "old", "created_at": "2026-10-04T11:00:00Z"},   // 3600s 前 ⇒ 卡住
            {"id": "fresh", "created_at": "2026-10-04T11:59:30Z"},  // 30s 前 ⇒ 不卡
            {"id": "bad", "created_at": "not-a-time"}               // 不可解析 ⇒ 跳过（Python 同判）
        ]}]});
        assert_eq!(stuck_pending_ids(&pend, now), vec!["old".to_string()]);
    }

    #[test]
    fn gateway_config_fills_gaps_only_and_never_overwrites() {
        let td = std::env::temp_dir().join(format!("aimail-gwcfg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&td);
        let sdir = td.join("systems").join("s1");
        std::fs::create_dir_all(&sdir).unwrap();
        let p = sdir.join("aimail_gateway.json");
        // ① 已有值 + 只缺 webhook_host ⇒ 既有值不动
        std::fs::write(
            &p,
            r#"{"system_home":"/root/.hermes","system_name":"keepme","gateway_url":""}"#,
        )
        .unwrap();
        std::env::set_var("AIMAIL_HOME", &td);
        let _ = crate::core::repair::run("s1", false, false, "", |sid, home| {
            crate::cmd::check::engine(sid, home, false).check
        });
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(
            v["system_home"], "/root/.hermes",
            "已存在的 system_home 不得被覆写"
        );
        assert_eq!(v["system_name"], "keepme", "无关字段必须保留");
        // ② 文件不存在 ⇒ 响亮失败且不动盘
        let _ = std::fs::remove_file(&p);
        let rc = crate::core::repair::run("s1", false, false, "", |sid, home| {
            crate::cmd::check::engine(sid, home, false).check
        });
        assert!(rc >= 0, "缺 cfg 时应响亮失败而非 panic");
        assert!(!p.exists(), "缺 cfg 时不得新建文件");
        let _ = std::fs::remove_dir_all(&td);
        std::env::remove_var("AIMAIL_HOME");
    }

    #[test]
    fn drain_stuck_posts_pending_then_acks_only_stuck() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        listener.set_nonblocking(true).unwrap();
        let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let seen2 = seen.clone();
        let h = std::thread::spawn(move || {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
            let mut served = 0;
            while std::time::Instant::now() < deadline && served < 4 {
                match listener.accept() {
                    Ok((mut sock, _)) => {
                        let mut buf = [0u8; 4096];
                        let n = sock.read(&mut buf).unwrap_or(0);
                        let req = String::from_utf8_lossy(&buf[..n]).to_string();
                        let first = req.lines().next().unwrap_or("").to_string();
                        let body = req.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
                        seen2.lock().unwrap().push(format!("{first} | {body}"));
                        let payload = if first.contains("/pending/ack") {
                            "{\"ok\":true}"
                        } else {
                            "{\"batches\":[{\"deliveries\":[
                                {\"id\":\"old\",\"email\":\"a@example.test\",\"created_at\":\"2000-01-01T00:00:00Z\"},
                                {\"id\":\"fresh\",\"email\":\"b@example.test\",\"created_at\":\"2999-01-01T00:00:00Z\"}
                            ]}]}"
                        };
                        let resp = format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                            payload.len(),
                            payload
                        );
                        let _ = sock.write_all(resp.as_bytes());
                        let _ = sock.flush();
                        served += 1;
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(20));
                    }
                    Err(_) => break,
                }
            }
        });
        let client = crate::core::gateway::GatewayClient::with_defaults(
            &format!("http://127.0.0.1:{port}"),
            "k",
        );
        let changed = drain_stuck(&Some(client));
        h.join().ok();
        assert!(changed, "有卡住的 pending ⇒ 应报告已处理");
        let log = seen.lock().unwrap().clone();
        assert!(
            log[0].starts_with("POST /api/v1/admin/pending ") && log[0].contains("\"filter\":[]"),
            "首个请求应是 pending 查询: {log:?}"
        );
        assert!(
            log.iter()
                .any(|l| l.contains("/pending/ack") && l.contains("\"old\"")),
            "应只 ack 卡住的那条: {log:?}"
        );
        assert!(
            !log.iter().any(|l| l.contains("\"fresh\"")),
            "新鲜的投递不该被 ack: {log:?}"
        );
    }
}
