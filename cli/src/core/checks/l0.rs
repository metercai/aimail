//! L0 配置层探针 —— `cli/check_status.py:983-1185` 的复刻。
//!
//! 三块：
//! 1. [`l0_configs`] 系统级环境文件完备性 + `system_home` + 指针归属（`:989-1023`）；
//! 2. [`agentmail_json`] 逐 agent 绑定文件九字段完备性 + 内部一致性（`:1055-1090`）；
//! 3. [`bridge_completeness`] 桥 TOML 结构 + pull 条目 + routes 覆盖（`:1093-1184`）。
//!
//! 读**原始 JSON**（不是 [`crate::core::config::GatewayConfig`]）：Python 侧这几处按
//! dict 语义判读（`if sid and gw:` 判的是"dict 非空"），用原始 Value 才对得上。

use crate::core::check::Check;
use crate::core::contract;
use crate::core::probe;
use crate::core::{config, home, platforms};
use serde_json::Value;
use std::path::{Path, PathBuf};

/// 探针需要的两侧根目录（显式传入 ⇒ 测试可指向夹具，不依赖进程环境）。
#[derive(Debug, Clone)]
pub struct Ctx {
    pub aimail_home: PathBuf,
    pub user_home: PathBuf,
}

impl Ctx {
    /// 取进程环境里的真实根（命令面用）。
    pub fn from_env() -> Self {
        Self {
            aimail_home: home::aimail_home(),
            user_home: home::user_home(),
        }
    }
}

/// 绑定文件必备九字段（`check_status.py:983-986` 的逐字清单）。
pub const BINDING_REQUIRED: &[&str] = &[
    "email",
    "gateway_url",
    "domain",
    "system_id",
    "system_name",
    "manager_address",
    "api_key",
    "webhook_url",
    "webhook_secret",
];

/// `_read_gw_cfg`：读系统级环境文件，缺失/不可读/非 JSON ⇒ None。
pub fn read_gw_cfg(ctx: &Ctx, sid: &str) -> Option<Value> {
    let path = config::gateway_config_path_in(&ctx.aimail_home, sid);
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn field_str<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or("")
}

fn is_falsy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => true,
        Some(Value::String(s)) => s.is_empty(),
        Some(Value::Bool(b)) => !b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        _ => false,
    }
}

/// 平台根目录（注册表 `home_dir`，缺省 `.{name}`）—— 与 `_pointer_platforms_for_sid` 同算法。
fn platform_root(user_home: &Path, name: &str) -> PathBuf {
    match platforms::home_dir(name) {
        Some(dir) => user_home.join(dir),
        None => user_home.join(format!(".{name}")),
    }
}

/// 哪些平台的指针文件引用了该 sid（`check_status.py:1026-1052`；**不去重**，与 Python 一致）。
pub fn pointer_platforms_for_sid(ctx: &Ctx, sid: &str) -> Vec<String> {
    let mut candidates: Vec<(String, PathBuf)> = Vec::new();
    for name in platforms::order() {
        let root = platform_root(&ctx.user_home, name);
        let ptr_name = platforms::pointer_file_for(name);
        let root_ptr = root.join(ptr_name);
        if root_ptr.is_file() {
            candidates.push((name.to_string(), root_ptr));
        }
        if platforms::pointer_kind(name) == "root_or_profiles" {
            let profiles = root.join("profiles");
            if profiles.is_dir() {
                let mut hits: Vec<PathBuf> = std::fs::read_dir(&profiles)
                    .into_iter()
                    .flatten()
                    .flatten()
                    .map(|e| e.path().join(ptr_name))
                    .filter(|p| p.is_file())
                    .collect();
                hits.sort();
                for p in hits {
                    candidates.push((name.to_string(), p));
                }
            }
        }
    }
    candidates
        .into_iter()
        .filter(|(_, ptr)| ptr.is_file())
        .filter(|(_, ptr)| {
            std::fs::read_to_string(ptr)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .and_then(|v| {
                    v.get("system_id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
                .as_deref()
                == Some(sid)
        })
        .map(|(name, _)| name)
        .collect()
}

/// L0：系统级环境文件完备性 + `system_home` + 指针归属。
pub fn l0_configs(c: &mut Check, sid: &str, ctx: &Ctx) {
    let Some(gw) = read_gw_cfg(ctx, sid) else {
        c.add(
            "config",
            "gateway_json",
            false,
            &format!("{} missing/unreadable", config::GATEWAY_CONFIG_NAME),
            &format!(
                "Run: aimail install --home <platform-root> --system-id {}",
                if sid.is_empty() { "<sid>" } else { sid }
            ),
        );
        return;
    };

    // 1. 连接字段完备
    let missing: Vec<&str> = ["gateway_url", "admin_key"]
        .iter()
        .filter(|k| is_falsy(gw.get(**k)))
        .copied()
        .collect();
    c.add(
        "config",
        "complete",
        missing.is_empty(),
        &if missing.is_empty() {
            "gateway_url + admin_key present".to_string()
        } else {
            format!("missing: {}", missing.join(", "))
        },
        &format!("Run: aimail install --system-id {sid}"),
    );

    // 2. system_home 存在且目录有效
    let sh = field_str(&gw, "system_home");
    if sh.is_empty() {
        c.add(
            "config",
            "system_home",
            false,
            "system_home MISSING (stats platform label → [?])",
            &format!("Run: aimail install --home <platform-root> --system-id {sid}"),
        );
    } else if !Path::new(sh).is_dir() {
        c.add(
            "config",
            "system_home",
            false,
            &format!("system_home dir does not exist: {sh}"),
            &format!("Fix path or re-run: aimail install --home <platform-root> --system-id {sid}"),
        );
    } else {
        c.add("config", "system_home", true, sh, "");
    }

    // 3. pointer 归属（任一平台指针引用该 sid 即可）
    let hits = pointer_platforms_for_sid(ctx, sid);
    c.add(
        "config",
        "pointer",
        !hits.is_empty(),
        &if hits.is_empty() {
            format!(
                "no platform {} pointer references this system",
                contract::pointer_file()
            )
        } else {
            format!("pointer: {}", hits.join(","))
        },
        &format!("Re-run platform install or aimail repair --system-id {sid}"),
    );
}

/// L0 扩展：逐 agent 绑定文件完备性 + 内部一致性。
pub fn agentmail_json(c: &mut Check, sid: &str, ctx: &Ctx) {
    let sysdir = ctx.aimail_home.join("systems").join(sid);
    if !sysdir.is_dir() {
        return;
    }
    let gw = read_gw_cfg(ctx, sid);
    for sub in sorted_subdirs(&sysdir) {
        let sub_name = file_name_of(&sub);
        let aj = sub.join(contract::binding_file());
        if !aj.is_file() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&aj) else {
            continue;
        };
        let d: Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(e) => {
                c.add(
                    "agent",
                    "config-json",
                    false,
                    &format!("{sub_name}: parse error: {e}"),
                    "Re-run `openclaw aimail register`",
                );
                continue;
            }
        };
        let name = {
            let a = field_str(&d, "agent_id");
            if a.is_empty() {
                sub_name.clone()
            } else {
                a.to_string()
            }
        };

        let missing: Vec<&str> = BINDING_REQUIRED
            .iter()
            .filter(|k| is_falsy(d.get(**k)))
            .copied()
            .collect();
        c.add(
            "agent",
            "config-complete",
            missing.is_empty(),
            &if missing.is_empty() {
                format!("{name}: all {} fields present", BINDING_REQUIRED.len())
            } else {
                format!("{name}: missing fields: {}", missing.join(", "))
            },
            "Re-run `openclaw aimail register`(api_key/webhook_secret cannot be rebuilt locally)",
        );

        // 内部一致性：system_id / gateway_url / domain vs email
        let mut issues: Vec<String> = Vec::new();
        let d_sid = field_str(&d, "system_id");
        if !d_sid.is_empty() && d_sid != sid {
            issues.push(format!(
                "system_id {}… != {}…",
                take_chars(d_sid, 16),
                take_chars(sid, 16)
            ));
        }
        let d_gw = field_str(&d, "gateway_url");
        if !d_gw.is_empty() {
            if let Some(g) = gw.as_ref() {
                let g_gw = field_str(g, "gateway_url");
                if !g_gw.is_empty() && d_gw.trim_end_matches('/') != g_gw.trim_end_matches('/') {
                    issues.push(format!(
                        "gateway_url differs from {}",
                        config::GATEWAY_CONFIG_NAME
                    ));
                }
            }
        }
        let email = field_str(&d, "email");
        let domain = field_str(&d, "domain");
        if !email.is_empty() && !domain.is_empty() && !email.ends_with(&format!("@{domain}")) {
            issues.push(format!("email domain != domain field ({domain})"));
        }
        c.add(
            "agent",
            "config-consistency",
            issues.is_empty(),
            &if issues.is_empty() {
                format!("{name}: consistent")
            } else {
                format!("{name}: {}", issues.join("; "))
            },
            "Re-run `openclaw aimail register`",
        );
    }
}

/// L0 扩展：桥配置完备性 + pull 条目 + routes 覆盖。
pub fn bridge_completeness(c: &mut Check, sid: &str, ctx: &Ctx) {
    let cfg = ctx.aimail_home.join("bridge").join("aimail_bridge.toml");
    if !cfg.exists() {
        return; // direct-connect 合法，check_bridge（L2）会报
    }
    let td: toml::Value = match std::fs::read_to_string(&cfg)
        .map_err(|e| e.to_string())
        .and_then(|text| toml::from_str::<toml::Value>(&text).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            c.add(
                "bridge",
                "config-complete",
                false,
                &format!("toml parse error: {e}"),
                "Check aimail_bridge.toml syntax",
            );
            return;
        }
    };

    let mode = td.get("mode").and_then(toml::Value::as_str).unwrap_or("");
    let mode_ok = mode == "push" || mode == "pull";
    c.add(
        "bridge",
        "config-mode",
        mode_ok,
        &if mode_ok {
            format!("mode={mode}")
        } else {
            format!("invalid mode: '{mode}'")
        },
        "mode must be push or pull",
    );
    if mode != "pull" {
        return;
    }

    let systems: Vec<Value> = td
        .get("pull")
        .and_then(|p| p.get("systems"))
        .and_then(toml::Value::as_array)
        .map(|arr| arr.iter().map(toml_to_json).collect())
        .unwrap_or_default();
    let gw = read_gw_cfg(ctx, sid);
    let gw_truthy = gw.as_ref().map(json_object_nonempty).unwrap_or(false);
    if !sid.is_empty() && gw_truthy {
        let entry = systems
            .iter()
            .find(|x| field_str(x, "system_id") == sid)
            .cloned();
        match entry {
            None => c.add(
                "bridge",
                "pull-entry",
                false,
                &format!("no pull.systems entry for {sid}"),
                &format!("Run: aimail bridge --system-id {sid}"),
            ),
            Some(e) => {
                let g = gw.as_ref().expect("checked non-empty");
                let key_match = field_str(&e, "admin_key") == field_str(g, "admin_key");
                c.add(
                    "bridge",
                    "pull-entry",
                    key_match,
                    &format!(
                        "pull entry present, admin_key {}",
                        if key_match { "match" } else { "MISMATCH" }
                    ),
                    &format!("Re-run: aimail bridge --system-id {sid}"),
                );
            }
        }
    }

    // routes 覆盖：每个本系统 agent 的 email 在投递路径表里有条目
    let routes_file = ctx.aimail_home.join("bridge").join("aimail_routes.toml");
    if !routes_file.exists() {
        return;
    }
    let Ok(routes_text) = std::fs::read_to_string(&routes_file) else {
        return;
    };
    let mut routes: Vec<(String, String)> = Vec::new();
    for line in routes_text.lines() {
        let line = line.trim();
        if line.contains('=') && !line.starts_with('#') {
            if let Some((k, v)) = line.split_once('=') {
                routes.push((
                    k.trim().trim_matches('"').to_string(),
                    v.trim()
                        .trim_matches('"')
                        .trim_matches('\'')
                        .trim_matches(',')
                        .to_string(),
                ));
            }
        }
    }
    let sysdir = ctx.aimail_home.join("systems").join(sid);
    if !sysdir.is_dir() {
        return;
    }
    for sub in sorted_subdirs(&sysdir) {
        let sub_name = file_name_of(&sub);
        let aj = sub.join(contract::binding_file());
        let Ok(text) = std::fs::read_to_string(&aj) else {
            continue;
        };
        let Ok(d) = serde_json::from_str::<Value>(&text) else {
            continue;
        };
        let email = field_str(&d, "email").to_string();
        if email.is_empty() {
            continue;
        }
        let name = {
            let a = field_str(&d, "agent_id");
            if a.is_empty() {
                sub_name
            } else {
                a.to_string()
            }
        };
        let declared = field_str(&d, "webhook_url").to_string();
        let Some((_, target)) = routes.iter().find(|(k, _)| *k == email) else {
            c.add(
                "bridge",
                "routes-entry",
                false,
                &format!("{name}: no route for {email} (pull delivery dead)"),
                &format!("Run: aimail bridge --system-id {sid}"),
            );
            continue;
        };
        let target = target.clone();
        let host = url_host(&target);
        let local = host == "127.0.0.1" || host == "localhost" || host == "::1";
        let (target_ok, _) = probe::probe_endpoint(&target, probe::DEFAULT_TIMEOUT_SECS);
        if !target_ok && !local {
            c.add(
                "bridge",
                "routes-target",
                true,
                &format!("{name}: route target remote (not probeable locally): {target}"),
                "",
            );
            continue;
        }
        let declared_ok = if declared.is_empty() {
            false
        } else {
            probe::probe_endpoint(&declared, probe::DEFAULT_TIMEOUT_SECS).0
        };
        if !target_ok {
            c.add(
                "bridge",
                "routes-target",
                false,
                &format!("{name}: route target dead: {target}"),
                "Check the agent platform is running; do NOT auto-edit routes",
            );
        } else if !declared.is_empty() && !declared_ok {
            c.add(
                "bridge",
                "routes-target",
                false,
                &format!(
                    "{name}: declared webhook_url dead: {declared} (route target alive: {target})"
                ),
                &format!("Run: aimail repair --system-id {sid}"),
            );
        } else if !declared.is_empty()
            && target.trim_end_matches('/') != declared.trim_end_matches('/')
        {
            c.add(
                "bridge",
                "routes-target",
                true,
                &format!(
                    "{name}: paths differ but both alive (route={target} vs declared={declared}) \
                     — repair will align"
                ),
                &format!("Run: aimail repair --system-id {sid}"),
            );
        } else {
            c.add(
                "bridge",
                "routes-target",
                true,
                &format!("{name}: route target alive & consistent"),
                "",
            );
        }
    }
}

// ── 小工具 ─────────────────────────────────────────────────────

fn sorted_subdirs(dir: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    out.sort();
    out
}

fn file_name_of(path: &Path) -> String {
    path.file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn json_object_nonempty(v: &Value) -> bool {
    v.as_object().map(|o| !o.is_empty()).unwrap_or(false)
}

/// TOML → JSON 视图（只为复用同一套取值代码，语义与 tomllib.load 的 dict 一致）。
pub fn toml_to_json(v: &toml::Value) -> Value {
    match v {
        toml::Value::String(s) => Value::String(s.clone()),
        toml::Value::Integer(i) => Value::Number((*i).into()),
        toml::Value::Float(f) => serde_json::Number::from_f64(*f)
            .map(Value::Number)
            .unwrap_or(Value::Null),
        toml::Value::Boolean(b) => Value::Bool(*b),
        toml::Value::Datetime(d) => Value::String(d.to_string()),
        toml::Value::Array(a) => Value::Array(a.iter().map(toml_to_json).collect()),
        toml::Value::Table(t) => Value::Object(
            t.iter()
                .map(|(k, val)| (k.clone(), toml_to_json(val)))
                .collect(),
        ),
    }
}

/// 主机名提取（`urllib.parse.urlparse(...).hostname` 的等价：小写、去 userinfo/端口）。
fn url_host(url: &str) -> String {
    let after = url.split("://").nth(1).unwrap_or("");
    let authority = after.split(['/', '?', '#']).next().unwrap_or("");
    let host_port = authority.rsplit('@').next().unwrap_or(authority);
    if let Some(rest) = host_port.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_lowercase();
    }
    host_port.split(':').next().unwrap_or("").to_lowercase()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    /// 造一个"系统配置 + 一个 agent"的最小夹具，返回 (tmp, ctx, sid)。
    fn fixture(tag: &str) -> (TempDir, Ctx, String) {
        let tmp = TempDir::new(tag);
        let aimail_home = tmp.path().join("aimail");
        let user_home = tmp.path().join("home");
        let sid = "s1".to_string();
        let sid_dir = aimail_home.join("systems").join(&sid);
        std::fs::create_dir_all(&sid_dir).unwrap();
        std::fs::create_dir_all(&user_home).unwrap();
        std::fs::write(
            sid_dir.join(config::GATEWAY_CONFIG_NAME),
            br#"{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1",
                 "system_name":"s1","domain":"example.test","save_raw_snapshots":true}"#,
        )
        .unwrap();
        let ctx = Ctx {
            aimail_home,
            user_home,
        };
        (tmp, ctx, sid)
    }

    fn write_agent(ctx: &Ctx, sid: &str, dir: &str, body: &str) {
        let d = ctx.aimail_home.join("systems").join(sid).join(dir);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join(contract::binding_file()), body).unwrap();
    }

    fn details(c: &Check, level: &str, name: &str) -> Vec<String> {
        c.checks
            .iter()
            .filter(|r| r.level == level && r.check == name)
            .map(|r| r.detail.clone())
            .collect()
    }

    #[test]
    fn missing_gateway_config_is_one_record_and_returns() {
        let (_t, ctx, sid) = fixture("l0-missing");
        std::fs::remove_file(
            ctx.aimail_home
                .join("systems")
                .join(&sid)
                .join(config::GATEWAY_CONFIG_NAME),
        )
        .unwrap();
        let mut c = Check::new();
        l0_configs(&mut c, &sid, &ctx);
        assert_eq!(c.checks.len(), 1, "{:?}", c.checks);
        assert_eq!(c.checks[0].check, "gateway_json");
        assert!(!c.checks[0].pass);
        assert!(c.checks[0].detail.ends_with("missing/unreadable"));
    }

    #[test]
    fn complete_system_home_and_pointer_records() {
        let (_t, ctx, sid) = fixture("l0-ok");
        // system_home 指向真实目录 ⇒ 通过；无指针 ⇒ pointer 失败
        let sh = ctx.user_home.join(".hermes");
        std::fs::create_dir_all(&sh).unwrap();
        let path = ctx
            .aimail_home
            .join("systems")
            .join(&sid)
            .join(config::GATEWAY_CONFIG_NAME);
        let mut v: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        v["system_home"] = Value::String(sh.to_string_lossy().into_owned());
        std::fs::write(&path, serde_json::to_string_pretty(&v).unwrap()).unwrap();

        let mut c = Check::new();
        l0_configs(&mut c, &sid, &ctx);
        assert_eq!(
            details(&c, "config", "complete"),
            vec!["gateway_url + admin_key present"]
        );
        assert!(c.checks.iter().any(|r| r.check == "system_home" && r.pass));
        assert!(c.checks.iter().any(|r| r.check == "pointer" && !r.pass));
    }

    #[test]
    fn pointer_hit_is_reported_by_platform_name() {
        let (_t, ctx, sid) = fixture("l0-ptr");
        let hermes = ctx.user_home.join(".hermes");
        std::fs::create_dir_all(&hermes).unwrap();
        std::fs::write(
            hermes.join(contract::pointer_file()),
            format!(r#"{{"system_id":"{sid}","email":"a@x.test"}}"#),
        )
        .unwrap();
        let hits = pointer_platforms_for_sid(&ctx, &sid);
        assert_eq!(hits, vec!["hermes".to_string()]);
        let mut c = Check::new();
        l0_configs(&mut c, &sid, &ctx);
        assert_eq!(details(&c, "config", "pointer"), vec!["pointer: hermes"]);
    }

    #[test]
    fn binding_file_missing_fields_and_inconsistency_are_reported() {
        let (_t, ctx, sid) = fixture("l0-bind");
        write_agent(
            &ctx,
            &sid,
            "a_example.test",
            r#"{"email":"a@example.test","gateway_url":"http://other.test","domain":"example.test","system_id":"s1","system_name":"s1","api_key":"k","agent_id":"a"}"#,
        );
        let mut c = Check::new();
        agentmail_json(&mut c, &sid, &ctx);
        let complete = details(&c, "agent", "config-complete");
        assert_eq!(complete.len(), 1);
        assert!(
            complete[0].contains("missing fields: manager_address, webhook_url, webhook_secret"),
            "{complete:?}"
        );
        let consistency = details(&c, "agent", "config-consistency");
        assert!(
            consistency[0].contains("gateway_url differs from"),
            "{consistency:?}"
        );
    }

    #[test]
    fn binding_file_parse_error_is_reported_and_scan_continues() {
        let (_t, ctx, sid) = fixture("l0-parse");
        write_agent(&ctx, &sid, "broken", "{not json");
        write_agent(&ctx, &sid, "ok_dir", r#"{"email":"b@example.test"}"#);
        let mut c = Check::new();
        agentmail_json(&mut c, &sid, &ctx);
        assert_eq!(details(&c, "agent", "config-json").len(), 1);
        assert_eq!(
            details(&c, "agent", "config-complete").len(),
            1,
            "坏文件不阻断后续"
        );
    }

    #[test]
    fn bridge_config_mode_is_checked_and_pull_entry_compared() {
        let (_t, ctx, sid) = fixture("l0-bridge");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(
            bridge.join("aimail_bridge.toml"),
            "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"OTHER\"\n",
        )
        .unwrap();
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        assert_eq!(details(&c, "bridge", "config-mode"), vec!["mode=pull"]);
        let entry = details(&c, "bridge", "pull-entry");
        assert_eq!(entry, vec!["pull entry present, admin_key MISMATCH"]);
    }

    #[test]
    fn bridge_invalid_mode_stops_before_pull_checks() {
        let (_t, ctx, sid) = fixture("l0-bridge-bad");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(bridge.join("aimail_bridge.toml"), "mode = \"weird\"\n").unwrap();
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        assert_eq!(
            details(&c, "bridge", "config-mode"),
            vec!["invalid mode: 'weird'"]
        );
        assert!(details(&c, "bridge", "pull-entry").is_empty());
    }

    #[test]
    fn missing_route_entry_marks_pull_delivery_dead() {
        let (_t, ctx, sid) = fixture("l0-routes");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(
            bridge.join("aimail_bridge.toml"),
            "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"k\"\n",
        )
        .unwrap();
        std::fs::write(
            bridge.join("aimail_routes.toml"),
            "other@example.test = \"http://127.0.0.1:1/x\"\n",
        )
        .unwrap();
        write_agent(
            &ctx,
            &sid,
            "a_example.test",
            r#"{"email":"a@example.test","webhook_url":"http://127.0.0.1:1/hook","agent_id":"a"}"#,
        );
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        let entry = details(&c, "bridge", "routes-entry");
        assert_eq!(
            entry,
            vec!["a: no route for a@example.test (pull delivery dead)"]
        );
    }

    #[test]
    fn dead_local_route_target_fails_without_auto_edit_hint() {
        let (_t, ctx, sid) = fixture("l0-dead-target");
        let bridge = ctx.aimail_home.join("bridge");
        std::fs::create_dir_all(&bridge).unwrap();
        std::fs::write(
            bridge.join("aimail_bridge.toml"),
            "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"k\"\n",
        )
        .unwrap();
        // 127.0.0.1:1 必然连不上，且是本地地址 ⇒ 走 FAIL 分支（不是 remote WARN）
        std::fs::write(
            bridge.join("aimail_routes.toml"),
            "a@example.test = \"http://127.0.0.1:1/x\"\n",
        )
        .unwrap();
        write_agent(
            &ctx,
            &sid,
            "a_example.test",
            r#"{"email":"a@example.test","webhook_url":"http://127.0.0.1:1/hook","agent_id":"a"}"#,
        );
        let mut c = Check::new();
        bridge_completeness(&mut c, &sid, &ctx);
        let target = details(&c, "bridge", "routes-target");
        assert_eq!(target, vec!["a: route target dead: http://127.0.0.1:1/x"]);
        let rec = c
            .checks
            .iter()
            .find(|r| r.check == "routes-target")
            .expect("record");
        assert_eq!(
            rec.fix,
            "Check the agent platform is running; do NOT auto-edit routes"
        );
    }

    #[test]
    fn url_host_matches_urlparse_hostname_semantics() {
        assert_eq!(url_host("http://127.0.0.1:8080/x"), "127.0.0.1");
        assert_eq!(url_host("http://LocalHost/X"), "localhost");
        assert_eq!(url_host("http://user:pw@host.test:9/x"), "host.test");
        assert_eq!(url_host("http://[::1]:8080/x"), "::1");
        assert_eq!(url_host("not-a-url"), "");
    }
}
