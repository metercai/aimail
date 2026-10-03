//! L2 探针：aimail-bridge（本地 NAT 穿透桥，**可选组件**）——
//! `cli/check_status.py:1205-1427` 的复刻。
//!
//! 与 Python 同序同判读：
//! 2.1 配置（无配置文件 = 未部署，合法）→ 2.2 进程 → 2.3 活跃度 → 2.4 pull 通路（P0，
//! 远端也能测）→ 2.5 桥自健康（HTTP 到 addr，P1）→ 2.6 与网关的跨配置一致性。
//!
//! 注意 2.1 用**自定义极简 TOML 解析**（`_parse_toml`，不支持数组），2.6/2.4 用
//! **真 TOML**（Python 侧用 tomllib 重读）—— 两者差异是原实现的既有行为，照抄。

use crate::core::check::Check;
use crate::core::checks::l0::{field_str, read_gw_cfg, Ctx};
use crate::core::{bridge as bridgectl, http, sig};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

fn bridge_dir(ctx: &Ctx) -> PathBuf {
    ctx.aimail_home.join("bridge")
}
fn bridge_cfg(ctx: &Ctx) -> PathBuf {
    bridge_dir(ctx).join("aimail_bridge.toml")
}
fn bridge_log(ctx: &Ctx) -> PathBuf {
    bridge_dir(ctx).join("aimail-bridge.log")
}
fn bridge_bin(ctx: &Ctx) -> PathBuf {
    bridge_dir(ctx).join("bin").join("aimail-bridge")
}
fn bridge_pid(ctx: &Ctx) -> PathBuf {
    bridge_dir(ctx).join("bridge.pid")
}

/// 极简 TOML（`_parse_toml`：裸键 + `[section]`，不支持数组）。
fn parse_toml_min(text: &str) -> BTreeMap<String, BTreeMap<String, String>> {
    let mut data: BTreeMap<String, BTreeMap<String, String>> = BTreeMap::new();
    data.insert("__top__".to_string(), BTreeMap::new());
    let mut cur = "__top__".to_string();
    for line in text.lines() {
        let s = line.trim();
        if s.is_empty() || s.starts_with('#') {
            continue;
        }
        if let Some(name) = section_name(s) {
            cur = name.clone();
            data.entry(name).or_default();
            continue;
        }
        if let Some((k, v)) = s.split_once('=') {
            let value = v.trim().trim_matches('"').trim_matches('\'').to_string();
            data.entry(cur.clone())
                .or_default()
                .insert(k.trim().to_string(), value);
        }
    }
    data
}

/// `^\[(\w+)\]$` 的等价（只有单层方括号才算 section）。
fn section_name(s: &str) -> Option<String> {
    let inner = s.strip_prefix('[')?.strip_suffix(']')?;
    if inner.is_empty() || !inner.chars().all(|c| c.is_alphanumeric() || c == '_') {
        return None;
    }
    Some(inner.to_string())
}

fn toml_get<'a>(
    data: &'a BTreeMap<String, BTreeMap<String, String>>,
    section: &str,
    key: &str,
) -> &'a str {
    data.get(section)
        .and_then(|m| m.get(key))
        .map(String::as_str)
        .unwrap_or("")
}

/// `_split_host_port`（支持 `[ipv6]:port`）。
pub fn split_host_port(addr: &str) -> (String, String) {
    let addr = addr.trim();
    if let Some(rest) = addr.strip_prefix('[') {
        let (host, tail) = rest.split_once(']').unwrap_or((rest, ""));
        return (host.to_string(), tail.trim_start_matches(':').to_string());
    }
    match addr.rsplit_once(':') {
        Some((host, port)) => (host.to_string(), port.to_string()),
        None => (addr.to_string(), String::new()),
    }
}

pub fn bridge(c: &mut Check, sid: &str, ctx: &Ctx) {
    // 2.1 配置：桥是否存在以配置文件为准
    if !bridge_cfg(ctx).exists() {
        c.add(
            "bridge",
            "config",
            true,
            "not deployed (gateway → agent-gateway direct)",
            "",
        );
        return;
    }

    let text = match std::fs::read_to_string(bridge_cfg(ctx)) {
        Ok(t) => t,
        Err(e) => {
            c.add(
                "bridge",
                "config",
                false,
                &format!("Parse error: {e}"),
                "Check aimail_bridge.toml syntax",
            );
            return;
        }
    };
    let td = parse_toml_min(&text);
    let mode = {
        let a = toml_get(&td, "__top__", "mode");
        if !a.is_empty() {
            a.to_string()
        } else {
            toml_get(&td, "bridge", "mode").to_string()
        }
    };
    let addr = {
        let a = toml_get(&td, "__top__", "addr");
        if !a.is_empty() {
            a.to_string()
        } else {
            toml_get(&td, "bridge", "addr").to_string()
        }
    };
    let aimail_url = toml_get(&td, "pull", "aimail_url").to_string();
    let poll_int = toml_get(&td, "pull", "poll_interval_sec").to_string();
    let mut parts = vec![format!("mode={mode}")];
    if !addr.is_empty() {
        parts.push(format!("addr={addr}"));
    }
    if !aimail_url.is_empty() {
        parts.push(format!("aimail_url={aimail_url}"));
    }
    if !poll_int.is_empty() {
        parts.push(format!("poll={poll_int}s"));
    }
    c.add("bridge", "config", true, &parts.join(", "), "");

    // 2.2/2.3 进程与活跃度（仅本地桥有意义）
    let st = bridgectl::status(&bridge_bin(ctx), &bridge_pid(ctx));
    let local_pid = if st.get("running").and_then(Value::as_bool).unwrap_or(false) {
        st.get("pid").map(py_display).unwrap_or_default()
    } else {
        String::new()
    };
    if !local_pid.is_empty() {
        c.add(
            "bridge",
            "process",
            true,
            &format!("Local PID={local_pid}"),
            "",
        );
    } else {
        c.add(
            "bridge",
            "process",
            true,
            "not on this machine (check addr or PID file for local)",
            "",
        );
    }
    if !local_pid.is_empty() && bridge_log(ctx).exists() {
        bridge_activity(c, ctx);
    } else if !local_pid.is_empty() {
        c.add(
            "bridge",
            "activity",
            true,
            "running, no log yet (no emails processed)",
            "",
        );
    } else {
        c.add("bridge", "activity", true, "N/A — bridge is remote", "");
    }

    // 2.4 pull 通路（P0）
    bridge_pull_path(c, sid, ctx, &text);

    // 2.5 桥自健康（P1）
    if !addr.is_empty() {
        bridge_health(c, &addr);
    }

    // 2.6 跨配置一致性
    bridge_gateway_consistency(c, sid, ctx, &text);
}

fn py_display(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// 2.3 日志新鲜度（`_check_bridge_activity`：三条都是"不算失败"的信息记录）。
fn bridge_activity(c: &mut Check, ctx: &Ctx) {
    let path = bridge_log(ctx);
    let Ok(meta) = std::fs::metadata(&path) else {
        c.add(
            "bridge",
            "activity",
            true,
            "Cannot read: metadata unavailable",
            "",
        );
        return;
    };
    let Ok(modified) = meta.modified() else {
        c.add(
            "bridge",
            "activity",
            true,
            "Cannot read: mtime unavailable",
            "",
        );
        return;
    };
    let age = SystemTime::now()
        .duration_since(modified)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let secs = age as i64;
    if age < 30.0 {
        c.add(
            "bridge",
            "activity",
            true,
            &format!("log {secs}s ago — actively running"),
            "",
        );
    } else if age < 120.0 {
        c.add(
            "bridge",
            "activity",
            true,
            &format!("log {secs}s ago — may be idle"),
            "",
        );
    } else {
        c.add(
            "bridge",
            "activity",
            true,
            &format!("log {secs}s ago — idle ({}h)", secs / 3600),
            "",
        );
    }
}

/// 2.4 `_check_bridge_pull_path`：用真 TOML 重读（数组支持）。
fn bridge_pull_path(c: &mut Check, sid: &str, _ctx: &Ctx, text: &str) -> bool {
    let pull = real_toml(text)
        .and_then(|t| t.get("pull").cloned())
        .or_else(|| Some(json!({})))
        .unwrap_or_else(|| json!({}));
    let systems = pull
        .get("systems")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let (aimail_url, pull_key) = if !systems.is_empty() {
        let entry = if !sid.is_empty() {
            systems
                .iter()
                .find(|s| field_str(s, "system_id") == sid)
                .or_else(|| systems.first())
        } else {
            systems.first()
        }
        .cloned()
        .unwrap_or_else(|| json!({}));
        let key = {
            let a = field_str(&entry, "api_key");
            if !a.is_empty() {
                a.to_string()
            } else {
                field_str(&entry, "admin_key").to_string()
            }
        };
        (field_str(&entry, "aimail_url").to_string(), key)
    } else {
        let key = {
            let a = field_str(&pull, "admin_key");
            if !a.is_empty() {
                a.to_string()
            } else {
                field_str(&pull, "api_key").to_string()
            }
        };
        (field_str(&pull, "aimail_url").to_string(), key)
    };
    if aimail_url.is_empty() || pull_key.is_empty() {
        c.add(
            "bridge",
            "pull_path",
            false,
            "aimail_url or admin_key missing in bridge config",
            "Check [pull] section in aimail_bridge.toml",
        );
        return false;
    }

    let body = serde_json::to_vec(&json!({ "limit": 1 })).unwrap_or_default();
    let headers = sig::signed_headers(
        &pull_key,
        "POST",
        "/api/v1/admin/pending",
        Some(&body),
        field_str(&pull, "system_id"),
    );
    let (code, resp) = http::json_req(
        &format!("{}/api/v1/admin/pending", aimail_url.trim_end_matches('/')),
        &headers,
        Some(&body),
        Some("POST"),
        10,
    );
    if code == 200 {
        let batches = resp
            .get("batches")
            .and_then(Value::as_array)
            .map(|a| a.len())
            .unwrap_or(0);
        c.add(
            "bridge",
            "pull_path",
            true,
            &format!("API 200, {batches} pending batch(es)"),
            "",
        );
    } else if code == 400 {
        c.add(
            "bridge",
            "pull_path",
            false,
            "HTTP 400 — route table may be empty on bridge",
            "Ensure bridge has registered routes via admin API",
        );
    } else {
        c.add(
            "bridge",
            "pull_path",
            false,
            &format!("HTTP {code} — bridge cannot reach gateway's pending API"),
            "Check aimail_url and admin_key in aimail_bridge.toml",
        );
    }
    code == 200
}

/// 2.5 `_check_bridge_health`：GET `{addr}/health`（addr 无 scheme 时补 http://）。
fn bridge_health(c: &mut Check, addr: &str) {
    let url = if addr.contains("://") {
        format!("{addr}/health")
    } else {
        format!("http://{addr}/health")
    };
    let (code, body) = http::json_req(&url, &[], None, Some("GET"), 5);
    if code == 200 {
        let status = body
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("ok")
            .to_string();
        c.add(
            "bridge",
            "self_health",
            true,
            &format!("HTTP {code}, status={status}"),
            "",
        );
    } else {
        let err = body.get("error").map(py_display).unwrap_or_default();
        c.add(
            "bridge",
            "self_health",
            false,
            &format!("Unreachable at {url}: {err}"),
            "Bridge binary may not be running or addr is wrong",
        );
    }
}

/// 真 TOML 重读（失败则 None，由调用方回退）。
fn real_toml(text: &str) -> Option<Value> {
    let parsed: toml::Value = toml::from_str(text).ok()?;
    Some(crate::core::checks::l0::toml_to_json(&parsed))
}

/// 2.6 `_check_bridge_gateway_consistency`：三处比对（URL 主机 / system_id / addr↔webhook_host）。
fn bridge_gateway_consistency(c: &mut Check, sid: &str, ctx: &Ctx, text: &str) {
    let Some(gw) = read_gw_cfg(ctx, sid) else {
        return; // gateway 层会自己报错
    };
    let td = real_toml(text);
    let mut mismatches: Vec<String> = Vec::new();

    let pull = td
        .as_ref()
        .and_then(|t| t.get("pull"))
        .cloned()
        .unwrap_or_else(|| json!({}));
    let systems = pull
        .get("systems")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let (bridge_url, bridge_sid) = if !systems.is_empty() {
        let entry = if !sid.is_empty() {
            systems
                .iter()
                .find(|s| field_str(s, "system_id") == sid)
                .or_else(|| systems.first())
        } else {
            systems.first()
        }
        .cloned()
        .unwrap_or_else(|| json!({}));
        (
            field_str(&entry, "aimail_url").to_string(),
            field_str(&entry, "system_id").to_string(),
        )
    } else {
        (
            field_str(&pull, "aimail_url").to_string(),
            field_str(&pull, "system_id").to_string(),
        )
    };

    let gw_url = field_str(&gw, "gateway_url").trim_end_matches('/');
    if !bridge_url.is_empty() && !gw_url.is_empty() {
        let b_host = host_only(&bridge_url);
        let g_host = host_only(gw_url);
        if b_host != g_host {
            mismatches.push(format!(
                "bridge pulls from '{b_host}' but gateway is '{g_host}'"
            ));
        }
    }

    let gw_sid = field_str(&gw, "system_id");
    if !bridge_sid.is_empty() && !gw_sid.is_empty() && bridge_sid != gw_sid {
        mismatches.push(format!(
            "bridge system_id differs: '{}...' vs '{}...'",
            take_chars(&bridge_sid, 16),
            take_chars(gw_sid, 16)
        ));
    }

    let bridge_addr = {
        let a = td
            .as_ref()
            .and_then(|t| t.get("__top__"))
            .and_then(|t| t.get("addr"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !a.is_empty() {
            a
        } else {
            td.as_ref()
                .and_then(|t| t.get("bridge"))
                .and_then(|t| t.get("addr"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        }
    };
    let gw_wh = field_str(&gw, "webhook_host").to_string();
    if !bridge_addr.is_empty() && !gw_wh.is_empty() && bridge_addr != gw_wh {
        let (bhost, bport) = split_host_port(&bridge_addr);
        let (_ghost, gport) = split_host_port(&gw_wh);
        let local_hosts = ["0.0.0.0", "::", "", "127.0.0.1", "localhost", "[::1]"];
        let nat_ok = local_hosts.contains(&bhost.as_str()) && !bport.is_empty() && bport == gport;
        if !nat_ok {
            mismatches.push(format!(
                "bridge addr '{bridge_addr}' ≠ gateway webhook_host '{gw_wh}'"
            ));
        }
    }

    if mismatches.is_empty() {
        c.add(
            "bridge",
            "config_consistency",
            true,
            "bridge ↔ gateway configs match",
            "",
        );
    } else {
        c.add(
            "bridge",
            "config_consistency",
            false,
            &mismatches.join("; "),
            "Re-run: aimail install (同步配置)",
        );
    }
}

fn host_only(url: &str) -> String {
    url.replace("https://", "")
        .replace("http://", "")
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("")
        .to_string()
}

fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// 桥相关的路径集合（供命令面/其它探针复用）。
pub struct Paths {
    pub cfg: PathBuf,
    pub log: PathBuf,
    pub bin: PathBuf,
    pub pid: PathBuf,
}

impl Paths {
    pub fn from_ctx(ctx: &Ctx) -> Self {
        Self {
            cfg: bridge_cfg(ctx),
            log: bridge_log(ctx),
            bin: bridge_bin(ctx),
            pid: bridge_pid(ctx),
        }
    }
}

/// 让 `Path` 参与判断（保留给后续切片用：routes 表等）。
pub fn is_deployed(ctx: &Ctx) -> bool {
    Path::new(&bridge_cfg(ctx)).exists()
}

/// 桥配置里的 pull 段（真 TOML；无则空对象）—— `repair` 切片会复用。
pub fn pull_section(text: &str) -> Value {
    real_toml(text)
        .and_then(|t| t.get("pull").cloned())
        .unwrap_or_else(|| json!({}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::check::Check;
    use crate::core::config;
    use crate::core::testutil::TempDir;

    fn ctx_with_bridge(tag: &str, toml_text: &str) -> (TempDir, Ctx) {
        let tmp = TempDir::new(tag);
        let aimail_home = tmp.path().join("aimail");
        let user_home = tmp.path().join("home");
        std::fs::create_dir_all(aimail_home.join("systems").join("s1")).unwrap();
        std::fs::create_dir_all(aimail_home.join("bridge")).unwrap();
        std::fs::create_dir_all(&user_home).unwrap();
        std::fs::write(
            aimail_home
                .join("systems")
                .join("s1")
                .join(config::GATEWAY_CONFIG_NAME),
            br#"{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1",
                 "system_name":"s1","domain":"example.test","webhook_host":"mail.example.test:443"}"#,
        )
        .unwrap();
        std::fs::write(
            aimail_home.join("bridge").join("aimail_bridge.toml"),
            toml_text,
        )
        .unwrap();
        (
            tmp,
            Ctx {
                aimail_home,
                user_home,
            },
        )
    }

    fn detail(c: &Check, check_name: &str) -> String {
        c.checks
            .iter()
            .find(|r| r.level == "bridge" && r.check == check_name)
            .map(|r| r.detail.clone())
            .unwrap_or_default()
    }

    #[test]
    fn absent_config_means_not_deployed_and_is_not_a_failure() {
        let tmp = TempDir::new("l2-absent");
        let ctx = Ctx {
            aimail_home: tmp.path().join("aimail"),
            user_home: tmp.path().join("home"),
        };
        let mut c = Check::new();
        bridge(&mut c, "s1", &ctx);
        assert_eq!(c.checks.len(), 1);
        assert!(c.checks[0].pass);
        assert_eq!(
            c.checks[0].detail,
            "not deployed (gateway → agent-gateway direct)"
        );
    }

    #[test]
    fn config_record_summarises_mode_addr_and_pull_fields() {
        let (_t, ctx) = ctx_with_bridge(
            "l2-config",
            "mode = \"push\"\naddr = \"127.0.0.1:8788\"\n[pull]\naimail_url = \"http://127.0.0.1:1\"\npoll_interval_sec = 15\n",
        );
        let mut c = Check::new();
        bridge(&mut c, "s1", &ctx);
        assert_eq!(
            detail(&c, "config"),
            "mode=push, addr=127.0.0.1:8788, aimail_url=http://127.0.0.1:1, poll=15s"
        );
    }

    #[test]
    fn missing_pull_credentials_fail_with_pinned_message() {
        let (_t, ctx) = ctx_with_bridge("l2-nocred", "mode = \"pull\"\n[pull]\n");
        let mut c = Check::new();
        bridge(&mut c, "s1", &ctx);
        assert_eq!(
            detail(&c, "pull_path"),
            "aimail_url or admin_key missing in bridge config"
        );
        let rec = c.checks.iter().find(|r| r.check == "pull_path").unwrap();
        assert!(!rec.pass);
    }

    #[test]
    fn dead_gateway_pull_path_reports_http_zero() {
        let (_t, ctx) = ctx_with_bridge(
            "l2-deadpull",
            "mode = \"pull\"\n[[pull.systems]]\nsystem_id = \"s1\"\naimail_url = \"http://127.0.0.1:1\"\napi_key = \"k\"\n",
        );
        let mut c = Check::new();
        bridge(&mut c, "s1", &ctx);
        assert_eq!(
            detail(&c, "pull_path"),
            "HTTP 0 — bridge cannot reach gateway's pending API"
        );
    }

    #[test]
    fn gateway_host_mismatch_is_reported() {
        let (_t, ctx) = ctx_with_bridge(
            "l2-mismatch",
            "mode = \"pull\"\n[[pull.systems]]\nsystem_id = \"s1\"\naimail_url = \"http://other.example.test\"\napi_key = \"k\"\n",
        );
        let mut c = Check::new();
        bridge(&mut c, "s1", &ctx);
        let d = detail(&c, "config_consistency");
        assert!(d.contains("bridge pulls from 'other.example.test'"), "{d}");
        assert!(d.contains("gateway is '127.0.0.1'"), "{d}");
    }

    #[test]
    fn nat_local_addr_with_same_port_is_not_a_mismatch() {
        let (_t, ctx) = ctx_with_bridge("l2-nat", "mode = \"push\"\naddr = \"0.0.0.0:443\"\n");
        let mut c = Check::new();
        bridge(&mut c, "s1", &ctx);
        assert_eq!(
            detail(&c, "config_consistency"),
            "bridge ↔ gateway configs match"
        );
    }

    #[test]
    fn split_host_port_handles_ipv6_and_plain_hosts() {
        assert_eq!(
            split_host_port("127.0.0.1:8788"),
            ("127.0.0.1".into(), "8788".into())
        );
        assert_eq!(split_host_port("[::1]:443"), ("::1".into(), "443".into()));
        assert_eq!(
            split_host_port("host.test"),
            ("host.test".into(), String::new())
        );
    }
}
