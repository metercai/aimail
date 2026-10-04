//! `cli/bridge_wire.py` 的 Rust 复刻 —— **CLI 拥有的桥路由接线**（零 SDK 依赖）。
//!
//! 为什么要这一层（照抄 Python 模块的原始理由，别拿 SDK 的桥 helper 替代）：SDK 侧的桥接线
//! （`aimail_base.ensure_bridge_route` 等）已排期删除；CLI 调那些符号会在 SDK 移除后立刻断。
//! 所以 CLI 自持一个桥客户端在这里。**本模块不 import pysdk/aimailsdk 的任何东西。**
//!
//! 契约（从桥仓读出：`~/aimail-bridge/src/admin.rs` + `router.rs`）—— admin API 服务在桥的
//! 配置地址上，与模式无关：
//! ```text
//!   GET    /health                -> {"status","uptime_secs","version"}
//!   GET    /api/v1/routes         -> [{"email","host","port"}, ...]
//!   POST   /api/v1/routes         <- {"email","host","port"}   (upsert; 幂等)
//!   DELETE /api/v1/routes/:email  -> "ok"                      (withdraw)
//! ```
//! `host` 字段**必须是完整绝对 URL**：桥对含 `://` 的 host 走 `ProfileRoute::from_url`（URL 保真、
//! 路径不丢）；传裸 `host:port` 会让桥套默认路径并把 host 当字面量，产出 `http://host:port:80/...`
//! 这类不可投递目标（2026-09-21 实测：重刷把 `127.0.0.1:9101` 写成 `127.0.0.1:9101:80` ⇒ 投递静默断）。
//! 因此本模块**只送校验过的绝对 URL，绝不自己拼一个**。
//!
//! 只读纪律：不写任何文件。桥自己的配置文件是"这里有桥"的信号，系统配置给 admin 端口，绑定给目标。
//!
//! 四态词表（不新造词）：`ok` / `skipped` / `no_bridge` / `failed`（failed = 已声明但不可用 ⇒
//! fail closed、什么都不改、说清原因）。`up`/`down` **不对称**（2026-09-28 裁决）：路由以地址为键，
//! 撤回只需要 email + 可达的桥；绑定端点为空的（pull）绑定**仍须撤回**曾经 push 时建的路由
//! —— 旧"空 ⇒ skip"的短路会把这行留到桥自身健康清理（最长 180s），期间邮件一直往已停收的宿主推。

use std::collections::BTreeMap;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::Path;
use std::time::Duration;

use serde_json::{json, Map, Value};

/// admin API 宿主：桥绑定在本机地址、admin API 默认仅限 localhost ⇒ CLI 一律走环回。
pub const ADMIN_HOST: &str = "127.0.0.1";
/// 系统配置与桥配置都没说时的 admin 端口兜底。
pub const DEFAULT_ADMIN_PORT: u16 = 38081;

pub const STATE_OK: &str = "ok";
pub const STATE_SKIPPED: &str = "skipped";
pub const STATE_NO_BRIDGE: &str = "no_bridge";
pub const STATE_FAILED: &str = "failed";

pub const ACTION_LIVE: &str = "live";
pub const ACTION_DOWN: &str = "down";

// ── helpers ────────────────────────────────────────────────────────────────────

/// `_valid_port`：非法/越界 ⇒ 0（叫"无效"）。
pub fn valid_port(value: &Value) -> u16 {
    let n = match value {
        Value::Number(n) => n.as_i64().unwrap_or(0),
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(0),
        _ => 0,
    };
    if n > 0 && n < 65536 {
        n as u16
    } else {
        0
    }
}

/// `_port_from_bind`：`"0.0.0.0:38080"` / `"[::1]:38081"` / `"38081"` 里的端口段。
pub fn port_from_bind(bind: &str) -> u16 {
    let s = bind.trim();
    if s.is_empty() {
        return 0;
    }
    if let Some(rest) = s.strip_prefix('[') {
        return match rest.split_once(']') {
            Some((_, after)) => valid_port(&Value::String(
                after.strip_prefix(':').unwrap_or("").to_string(),
            )),
            None => 0,
        };
    }
    if let Some((_, p)) = s.rsplit_once(':') {
        return valid_port(&Value::String(p.to_string()));
    }
    valid_port(&Value::String(s.to_string()))
}

/// 桥声明（`load_declaration` 的返回）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Declaration {
    pub declared: bool,
    pub usable: bool,
    pub reason: String,
    pub mode: String,
    pub bind: String,
    pub admin_host: String,
    pub admin_port: u16,
    pub source: String,
    pub gateway_port: u16,
}

/// 只读判定：本机有没有声明桥、它的 admin API 在哪。
///
/// `declared` 在"桥自己的配置文件在位"或"系统配置带 `bridge_admin_port`"时为真；
/// `usable=false` 表示声明了但坏了（不可读/TOML 非法）—— 这种必须 **fail closed**，
/// 绝不能被当成"这里没有桥"。**永不抛错。**
pub fn load_declaration(gateway_cfg: &Value, bridge_cfg_path: &Path) -> Declaration {
    let mut out = Declaration {
        declared: false,
        usable: true,
        reason: String::new(),
        mode: String::new(),
        bind: String::new(),
        admin_host: ADMIN_HOST.to_string(),
        admin_port: DEFAULT_ADMIN_PORT,
        source: String::new(),
        gateway_port: 0,
    };
    let gw_port = valid_port(gateway_cfg.get("bridge_admin_port").unwrap_or(&Value::Null));
    out.gateway_port = gw_port;
    if gw_port != 0 {
        out.declared = true;
        out.admin_port = gw_port;
        out.source = "system config bridge_admin_port".to_string();
    }

    if !bridge_cfg_path.is_file() {
        if !out.declared {
            out.reason = format!(
                "no bridge config at {} and no bridge_admin_port declared",
                bridge_cfg_path.display()
            );
        }
        return out;
    }

    let parsed: Result<toml::Value, String> = std::fs::read(bridge_cfg_path)
        .map_err(|e| e.to_string())
        .and_then(|bytes| {
            std::str::from_utf8(&bytes)
                .map_err(|e| e.to_string())
                .and_then(|t| t.parse::<toml::Value>().map_err(|e| e.to_string()))
        });
    let toml_cfg = match parsed {
        Ok(v) => v,
        Err(e) => {
            // 坏声明 = fail closed 态（Python 报 `{ExcName}: {e}`；此处给等价文本）
            out.declared = true;
            out.usable = false;
            out.source = bridge_cfg_path.display().to_string();
            out.reason = format!(
                "bridge config {} is unreadable/invalid TOML ({}) — nothing changed",
                bridge_cfg_path.display(),
                e
            );
            return out;
        }
    };

    out.declared = true;
    out.source = bridge_cfg_path.display().to_string();
    out.mode = toml_cfg
        .get("mode")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    out.bind = toml_cfg
        .get("bind")
        .or_else(|| toml_cfg.get("addr"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if gw_port == 0 {
        let bind_port = port_from_bind(&out.bind);
        if bind_port != 0 {
            out.admin_port = bind_port;
        }
    }
    out
}

/// 真 TCP 探测：`host:port` 上有没有人在听。
pub fn tcp_open(host: &str, port: u16, timeout: Duration) -> bool {
    let addrs = match (host, port).to_socket_addrs() {
        Ok(a) => a,
        Err(_) => return false,
    };
    for a in addrs {
        if TcpStream::connect_timeout(&a, timeout).is_ok() {
            return true;
        }
    }
    false
}

/// `GET /health` → admin API 应答时的 JSON 对象，否则 None（仅信息用；可达性判据是 TCP 探测）。
pub fn admin_health(host: &str, port: u16, timeout_secs: u64) -> Option<Value> {
    let url = format!("http://{}:{}/health", host, port);
    let (code, body) = crate::core::http::raw_req(&url, None, None, timeout_secs).ok()?;
    if !(200..300).contains(&code) {
        return None;
    }
    let v: Value = serde_json::from_str(if body.is_empty() { "{}" } else { &body }).ok()?;
    if v.is_object() {
        Some(v)
    } else {
        None
    }
}

/// `(reachable, detail)` —— reachable = admin 端口接受连接。
pub fn admin_reachable(host: &str, port: u16) -> (bool, String) {
    if tcp_open(host, port, Duration::from_millis(500)) {
        match admin_health(host, port, 1) {
            Some(h) if !h.get("status").unwrap_or(&Value::Null).is_null() => {
                let status = h.get("status").and_then(|v| v.as_str()).unwrap_or("");
                let version = h
                    .get("version")
                    .map(|v| match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    })
                    .unwrap_or_else(|| "?".to_string());
                (
                    true,
                    format!(
                        "admin {}:{} is up (health status={}, version={})",
                        host, port, status, version
                    ),
                )
            }
            _ => (
                true,
                format!("admin {}:{} is up (no /health contract answer)", host, port),
            ),
        }
    } else {
        (false, format!("admin {}:{} is not reachable", host, port))
    }
}

/// 解析出的绝对 URL（`urlparse` + `u.port` 的语义子集）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParsedUrl {
    pub scheme: String,
    pub netloc: String,
    pub hostname: String,
    pub port: Option<u16>,
}

/// 手写解析，镜像 `urlparse` 的关键行为：`scheme://netloc[/path]`，
/// 端口非数字或越界 ⇒ Err（对应 Python `u.port` 抛 `ValueError`）。
pub fn parse_abs_url(raw: &str) -> Result<ParsedUrl, String> {
    let (scheme, rest) = match raw.split_once("://") {
        Some((s, r)) => (s.to_lowercase(), r),
        None => (String::new(), raw),
    };
    let netloc = rest.split(['/', '?', '#']).next().unwrap_or("").to_string();
    // userinfo 不属于本场景；取 @ 之后
    let hostpart = netloc.rsplit('@').next().unwrap_or("").to_string();
    let (hostname, port_str) = if let Some(rest2) = hostpart.strip_prefix('[') {
        match rest2.split_once(']') {
            Some((h, after)) => (
                h.to_string(),
                after.strip_prefix(':').map(|s| s.to_string()),
            ),
            None => (String::new(), None),
        }
    } else if let Some((h, p)) = hostpart.rsplit_once(':') {
        (h.to_string(), Some(p.to_string()))
    } else {
        (hostpart.clone(), None)
    };
    let port = match port_str {
        None => None,
        Some(p) => {
            let n: i64 = p
                .trim()
                .parse()
                .map_err(|_| format!("Port could not be cast to integer value as '{}'", p))?;
            if !(0..65536).contains(&n) {
                return Err("Port out of range 0-65535".to_string());
            }
            Some(n as u16)
        }
    };
    Ok(ParsedUrl {
        scheme,
        netloc,
        hostname,
        port,
    })
}

/// `(absolute_url, reason)` —— 目标必须已经是可投递的绝对 URL。`reason` 为空表示接受。
///
/// 裸 `host:port`（或 `host:port/path`）**拒绝**而不是补前缀：补前缀恰好产出 2026-09-27 实测的
/// 不可投递形态 `http://host:port:80/...`。被接受的 URL **原样透传，绝不重建**。
pub fn validate_target(value: &Value) -> (String, String) {
    let raw = match value {
        Value::Null => String::new(),
        Value::String(s) => s.trim().to_string(),
        other => other.to_string().trim().to_string(),
    };
    if raw.is_empty() {
        return (
            String::new(),
            "the binding carries no webhook_url (pull mode: the gateway fetches the mail, \
             there is no local endpoint to route to)"
                .to_string(),
        );
    }
    let u = match parse_abs_url(&raw) {
        Ok(u) => u,
        Err(e) => {
            return (
                String::new(),
                format!("'{}' is not a usable URL ({})", raw, e),
            )
        }
    };
    if u.scheme != "http" && u.scheme != "https" {
        return (
            String::new(),
            format!(
                "not an absolute http(s) URL: '{}' has no http(s):// scheme \
                 (a bare host:port cannot be delivered to)",
                raw
            ),
        );
    }
    if u.netloc.is_empty() || u.hostname.is_empty() {
        return (String::new(), format!("'{}' carries no host", raw));
    }
    if u.port == Some(0) {
        return (String::new(), format!("'{}' carries an invalid port", raw));
    }
    (raw, String::new())
}

// ── bridge client ──────────────────────────────────────────────────────────────

/// `(ok, detail)` —— 一次 admin 调用。**永不抛错**，原因以文本回传。
pub fn admin_request(
    method: &str,
    host: &str,
    port: u16,
    path: &str,
    body: Option<&Value>,
    timeout_secs: u64,
) -> (bool, String) {
    let url = format!("http://{}:{}{}", host, port, path);
    let data = body.map(|b| b.to_string().into_bytes());
    match crate::core::http::raw_req_method(
        method,
        &url,
        data.as_deref(),
        Some("application/json"),
        timeout_secs,
    ) {
        Ok((code, body_text)) => {
            if (200..300).contains(&code) {
                (true, body_text.trim().to_string())
            } else {
                // 镜像 Python 的 `HTTPError` 分档：状态码 + 原因短语
                (
                    false,
                    format!("bridge answered HTTP {} ({})", code, body_text),
                )
            }
        }
        Err(e) => (false, e),
    }
}

/// `POST /api/v1/routes {email, host, port}` —— 幂等 upsert（桥侧表）。
///
/// `port` 是占位（host 为完整 URL 时桥忽略它）；只能给 80，因为 `admin.rs` 拒绝 0。
pub fn upsert_route(email: &str, target_url: &str, host: &str, port: u16) -> (bool, String) {
    let body = json!({"email": email, "host": target_url, "port": 80});
    admin_request("POST", host, port, "/api/v1/routes", Some(&body), 5)
}

/// `DELETE /api/v1/routes/:email` —— 撤回该地址的路由。
pub fn withdraw_route(email: &str, host: &str, port: u16) -> (bool, String) {
    let path = format!("/api/v1/routes/{}", percent_encode(email));
    admin_request("DELETE", host, port, &path, None, 5)
}

/// `urllib.parse.quote(s, safe='')` 的最小实现（非 unreserved 字符全部 %XX）。
fn percent_encode(s: &str) -> String {
    let mut out = String::new();
    for b in s.as_bytes() {
        let c = *b as char;
        if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '~') {
            out.push(c);
        } else {
            out.push_str(&format!("%{:02X}", b));
        }
    }
    out
}

// ── the three-step decision ────────────────────────────────────────────────────

/// 判这一个地址该做什么，并去做。**永不抛错**。
///
/// 1. 只有 `action="live"`（up）才看绑定的本地端点：空值 = 合法 pull 绑定 ⇒ `skipped` + 原因；
///    非空但不可投递 = 缺陷 ⇒ `failed`（拒绝并给原因，绝不静默放过）。`down` **完全不看 URL**：
///    路由以 email 为键，绑定端点变空时反而**更要**撤回（2026-09-28）；
/// 2. 已声明的桥必须可达，否则 `failed` + 原因且**什么都不动**（fail closed：半配置的桥不许写）；
/// 3. 没有声明桥 ⇒ `no_bridge`：刻意的 no-op，不是错误。
pub fn sync_route(
    action: &str,
    email: &str,
    target_url: &Value,
    gateway_cfg: &Value,
    bridge_cfg_path: &Path,
    admin_host: &str,
) -> Value {
    let act = if action.to_lowercase() == ACTION_DOWN {
        ACTION_DOWN
    } else {
        ACTION_LIVE
    };
    let mut out = Map::new();
    out.insert("state".into(), json!(STATE_FAILED));
    out.insert("action".into(), json!(act));
    out.insert("email".into(), json!(email));
    out.insert("target".into(), json!(""));
    out.insert("reason".into(), json!(""));
    out.insert("admin_port".into(), json!(DEFAULT_ADMIN_PORT));
    out.insert("mode".into(), json!(""));
    out.insert("bind".into(), json!(""));
    out.insert("source".into(), json!(""));

    if act != ACTION_DOWN {
        let raw = match target_url {
            Value::Null => String::new(),
            Value::String(s) => s.trim().to_string(),
            other => other.to_string().trim().to_string(),
        };
        let (target, why) = validate_target(target_url);
        if target.is_empty() {
            out.insert(
                "state".into(),
                json!(if raw.is_empty() {
                    STATE_SKIPPED
                } else {
                    STATE_FAILED
                }),
            );
            out.insert("reason".into(), json!(why));
            return Value::Object(out);
        }
        out.insert("target".into(), json!(target));
    }

    let decl = load_declaration(gateway_cfg, bridge_cfg_path);
    out.insert("admin_port".into(), json!(decl.admin_port));
    out.insert("mode".into(), json!(decl.mode));
    out.insert("bind".into(), json!(decl.bind));
    out.insert("source".into(), json!(decl.source));
    if !decl.declared {
        out.insert("state".into(), json!(STATE_NO_BRIDGE));
        out.insert("reason".into(), json!(decl.reason));
        return Value::Object(out);
    }
    if !decl.usable {
        out.insert("state".into(), json!(STATE_FAILED));
        out.insert("reason".into(), json!(decl.reason));
        return Value::Object(out);
    }

    let (reachable, detail) = admin_reachable(admin_host, decl.admin_port);
    if !reachable {
        out.insert("state".into(), json!(STATE_FAILED));
        out.insert(
            "reason".into(),
            json!(format!(
                "bridge declared ({}) but {} — nothing changed",
                decl.source, detail
            )),
        );
        return Value::Object(out);
    }

    if act == ACTION_DOWN {
        let (ok, detail) = withdraw_route(email, admin_host, decl.admin_port);
        if ok {
            out.insert("state".into(), json!(STATE_OK));
            out.insert(
                "reason".into(),
                json!(if detail.is_empty() {
                    "route withdrawn".to_string()
                } else {
                    detail
                }),
            );
        } else {
            out.insert("state".into(), json!(STATE_FAILED));
            out.insert(
                "reason".into(),
                json!(format!("could not withdraw the route: {}", detail)),
            );
        }
        return Value::Object(out);
    }

    let target = out
        .get("target")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let (ok, detail) = upsert_route(email, &target, admin_host, decl.admin_port);
    if ok {
        out.insert("state".into(), json!(STATE_OK));
        out.insert(
            "reason".into(),
            json!(if detail.is_empty() {
                "route upserted".to_string()
            } else {
                detail
            }),
        );
    } else {
        out.insert("state".into(), json!(STATE_FAILED));
        out.insert(
            "reason".into(),
            json!(format!("could not upsert the route: {}", detail)),
        );
    }
    Value::Object(out)
}

/// 回执行（与 SDK 的路由行同形）。
pub fn format_line(outcome: &Value) -> String {
    let state = outcome.get("state").and_then(|v| v.as_str()).unwrap_or("");
    let email = outcome.get("email").and_then(|v| v.as_str()).unwrap_or("");
    if state == STATE_OK && outcome.get("action").and_then(|v| v.as_str()) == Some(ACTION_DOWN) {
        return format!("route withdrawn: {}", email);
    }
    if state == STATE_OK {
        return format!(
            "route: {} -> {}",
            email,
            outcome.get("target").and_then(|v| v.as_str()).unwrap_or("")
        );
    }
    if state == STATE_SKIPPED {
        let r = outcome.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return format!(
            "route skipped for {}: {}",
            email,
            if r.is_empty() { "no local endpoint" } else { r }
        );
    }
    if state == STATE_NO_BRIDGE {
        let r = outcome.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return format!(
            "route skipped for {}: {}",
            email,
            if r.is_empty() { "no local bridge" } else { r }
        );
    }
    if state == STATE_FAILED {
        let r = outcome.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return format!(
            "route FAILED for {}: {} -- run 'aimail repair'",
            email,
            if r.is_empty() { "unknown" } else { r }
        );
    }
    format!(
        "route: {} state={}",
        email,
        if state.is_empty() { "unknown" } else { state }
    )
}

// ── 本地路由表 / 归属判据（`cmd_bridge` 与对账共用）────────────────────────────

/// `_read_routes`：读 `aimail_routes.toml`（email → target URL）。
/// 注意：**逐行解析**（不是真 TOML —— 值里带 URL，且历史文件是 `key = "value"` 形式）。
pub fn read_routes(path: &Path) -> BTreeMap<String, String> {
    let mut routes = BTreeMap::new();
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return routes,
    };
    for line in text.lines() {
        let s = line.trim();
        if s.is_empty() || s.starts_with('#') || !s.contains('=') {
            continue;
        }
        let (k, v) = s.split_once('=').unwrap();
        routes.insert(
            k.trim().trim_matches('"').to_string(),
            v.trim().trim_matches('"').to_string(),
        );
    }
    routes
}

/// `_target_to_route_fields`：接收端 URL → 桥 admin API 的 `{host, port}` 字段。
/// host 必须是完整 URL（含 `://`）⇒ 返回 `(url, 80)`；裸 `host:port` 兜底拆两字段。
pub fn target_to_route_fields(target: &str) -> (String, u16) {
    let t = target.trim();
    if t.contains("://") {
        return (t.to_string(), 80);
    }
    if let Some((h, p)) = t.rsplit_once(':') {
        if !h.is_empty() && p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty() {
            return (h.to_string(), p.parse().unwrap_or(80));
        }
    }
    (t.to_string(), 80)
}

/// `_system_route_scope`：本系统路由行判据。判不出归属（缺 domain）⇒ None。
///
/// shared（配置带 `system_name`）：后缀 `.{system_name}@{domain}`；非 shared：后缀 `@{domain}`。
pub type RouteScope = Box<dyn Fn(&str) -> bool>;

pub fn system_route_scope(cfg: &Value) -> Option<RouteScope> {
    let domain = cfg
        .get("domain")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if domain.is_empty() {
        return None;
    }
    let sname = cfg
        .get("system_name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if sname.is_empty() {
        let suffix = format!("@{}", domain);
        Some(Box::new(move |em: &str| em.ends_with(&suffix)))
    } else {
        let suffix = format!(".{}@{}", sname, domain);
        Some(Box::new(move |em: &str| em.ends_with(&suffix)))
    }
}

// ── 对账编排（`_reconcile_inbound_routes` / `_ensure_inbound_routes`）──────────────

/// 本机**已注册**绑定的 `(email, webhook_url)` —— 对账的期望态来源。
///
/// 只取"绑定文件可读且带 email"的项：平台枚举里的**未注册**期望项在路由层没有意义
/// （路由以地址为键，未注册地址没有可投递的云端身份）。
pub fn registered_bindings(sid: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    if sid.is_empty() {
        return out;
    }
    let base = crate::core::config::systems_root_in(&crate::core::home::aimail_home()).join(sid);
    let entries = match std::fs::read_dir(&base) {
        Ok(e) => e,
        Err(_) => return out,
    };
    let mut dirs: Vec<_> = entries.flatten().map(|e| e.path()).collect();
    dirs.sort();
    for d in dirs {
        if !d.is_dir() {
            continue;
        }
        let jf = d.join(crate::core::contract::binding_file());
        let text = match std::fs::read_to_string(&jf) {
            Ok(t) => t,
            Err(_) => continue, // 权限/IO 异常按未注册处理，列举不中断
        };
        let v: Value = match serde_json::from_str(&text) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let em = v.get("email").and_then(|x| x.as_str()).unwrap_or("");
        if em.is_empty() {
            continue;
        }
        out.push((
            em.to_string(),
            v.get("webhook_url")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string(),
        ));
    }
    out
}

/// `_reconcile_inbound_routes`：live 对账 —— 期望态 = 本系统全部注册绑定，实际态 = 桥路由表。
///
/// 缺/变 ⇒ upsert；配置已不需要（绑定消失/变 pull）的**本系统**行 ⇒ 撤回；归属判不出的行只告警不动。
/// 返回 `(reported, anchor_out)`；`anchor_out=None` 表示锚点无事可报。
pub fn reconcile_inbound_routes(
    sid: &str,
    cfg: &Value,
    anchor_email: &str,
    bridge_cfg: &Path,
) -> (usize, Option<Value>) {
    use crate::cmd::report::{ok, warn};

    let mut upsert: BTreeMap<String, String> = BTreeMap::new();
    for (em, url) in registered_bindings(sid) {
        if !url.trim().is_empty() {
            upsert.insert(em, url);
        }
    }
    let routes = read_routes(&crate::core::bridge_wire::routes_file());
    let pred = system_route_scope(cfg);
    let mut withdraw: Vec<String> = Vec::new();
    match pred {
        None => {
            if !routes.is_empty() {
                warn(
                    "route reconcile: cannot attribute existing routes to this system \
                     (config domain missing) — stale rows left untouched",
                );
            }
        }
        Some(p) => {
            withdraw = routes
                .keys()
                .filter(|em| !upsert.contains_key(*em) && p(em))
                .cloned()
                .collect();
        }
    }

    let mut reported = 0usize;
    let mut anchor_out: Option<Value> = None;
    let mut no_bridge = false;
    for (em, url) in &upsert {
        let out = sync_route(ACTION_LIVE, em, &json!(url), cfg, bridge_cfg, ADMIN_HOST);
        reported += 1;
        if em == anchor_email {
            anchor_out = Some(out.clone());
        } else {
            let line = format_line(&out);
            if out.get("state").and_then(|v| v.as_str()) == Some(STATE_OK) {
                ok(&line);
            } else {
                warn(&line);
            }
        }
        if out.get("state").and_then(|v| v.as_str()) == Some(STATE_NO_BRIDGE) {
            no_bridge = true;
            break;
        }
    }
    if !no_bridge {
        for em in &withdraw {
            let out = sync_route(ACTION_DOWN, em, &Value::Null, cfg, bridge_cfg, ADMIN_HOST);
            reported += 1;
            if em == anchor_email {
                anchor_out = Some(out.clone());
            } else {
                let line = format_line(&out);
                if out.get("state").and_then(|v| v.as_str()) == Some(STATE_OK) {
                    ok(&line);
                } else {
                    warn(&line);
                }
            }
        }
    }
    (reported, anchor_out)
}

/// 桥运行时目录下的路由表路径（`ROUTES_FILE`）。
pub fn routes_file() -> std::path::PathBuf {
    crate::core::home::aimail_home()
        .join("bridge")
        .join("aimail_routes.toml")
}

/// 桥配置文件路径（`BRIDGE_CFG`）。
pub fn bridge_cfg_file() -> std::path::PathBuf {
    crate::core::home::aimail_home()
        .join("bridge")
        .join("aimail_bridge.toml")
}

/// `_ensure_inbound_routes`：按系统配置做一次 live 对账。**best-effort**：永不改调用方 rc。
pub fn ensure_inbound_routes(sid: &str) -> i32 {
    use crate::cmd::report::warn;
    if sid.is_empty() {
        return 0;
    }
    // Python：读不到系统配置时 cfg={} ⇒ 仍走对账，但归属判据缺 domain ⇒ 只告警不动
    let cfg: Value = match crate::core::config::load_gateway_config(sid) {
        Some(c) => Value::Object(c.to_json()),
        None => json!({}),
    };
    let _ = reconcile_inbound_routes(sid, &cfg, "", &bridge_cfg_file());
    let _ = warn; // 保持与 Python 同形（失败只 warn）
    0
}

// ── 桥维护（`cmd_bridge` 用：进程探测 / 路径 / 期望路由）────────────────────────────

/// 桥运行时目录（`BRIDGE_DIR`）。
pub fn bridge_dir() -> std::path::PathBuf {
    crate::core::home::aimail_home().join("bridge")
}

/// 桥 pid 文件（`BRIDGE_PID`）。
pub fn bridge_pid_file() -> std::path::PathBuf {
    bridge_dir().join("bridge.pid")
}

/// 桥日志（`BRIDGE_LOG`）。
pub fn bridge_log_file() -> std::path::PathBuf {
    bridge_dir().join("aimail-bridge.log")
}

/// bridge 维护命令用的 admin 地址（`BRIDGE_ADDR` = 默认 38081；与"声明式"的 `sync_route` 不同：
/// `cmd_bridge` 的刷新**直接打这个固定地址**，照抄 Python）。
pub const BRIDGE_ADDR: &str = "127.0.0.1:38081";

/// `_bridge_pids`：锚定**可执行文件路径**的精确匹配（不许宽松匹配 —— 2026-08-16/09-21 两次误杀事故的口径）。
///
/// 先试 `pgrep -f '^[^ ]*/aimail-bridge( |$)'`；宿主没装 procps（deerflow 镜像，F12）时回退扫
/// `/proc/<pid>/cmdline` 的**首 token**，判据相同（以 `aimail-bridge` 结尾）⇒ 精确度不变、不崩。
pub fn bridge_pids() -> Vec<u32> {
    let is_bridge_token = |tok: &str| tok == "aimail-bridge" || tok.ends_with("/aimail-bridge");
    if let Ok(out) = std::process::Command::new("pgrep")
        .args(["-f", r"^[^ ]*/aimail-bridge( |$)"])
        .output()
    {
        if out.status.success() {
            let mut pids: Vec<u32> = String::from_utf8_lossy(&out.stdout)
                .lines()
                .filter_map(|l| l.trim().parse::<u32>().ok())
                .collect();
            pids.sort_unstable();
            pids.dedup();
            return pids;
        }
        return Vec::new(); // pgrep 在、但没匹配到
    }
    let mut pids = Vec::new();
    if let Ok(entries) = std::fs::read_dir("/proc") {
        for e in entries.flatten() {
            let pid: u32 = match e.file_name().to_string_lossy().parse() {
                Ok(p) => p,
                Err(_) => continue,
            };
            let raw = match std::fs::read(e.path().join("cmdline")) {
                Ok(b) => b,
                Err(_) => continue,
            };
            let first = String::from_utf8_lossy(&raw)
                .split('\u{0}')
                .next()
                .unwrap_or("")
                .to_string();
            if is_bridge_token(&first) {
                pids.push(pid);
            }
        }
    }
    pids.sort_unstable();
    pids.dedup();
    pids
}

/// `_addr_clean`：地址 → 目录键（`re.sub(r"[^\w.\-]", "_", email, flags=ASCII)`）。
pub fn addr_clean(email: &str) -> String {
    email
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// `_default_recv_url`：平台默认接收端（hermes ⇒ 读宿主 config.yaml 的 webhook 端口，默认 8646，
/// 路径取契约的 hermes 入站路径；否则 openclaw 的 `127.0.0.1:8799/hook`）。
pub fn default_recv_url(sid: &str, email: &str) -> String {
    let aj = crate::core::config::systems_root_in(&crate::core::home::aimail_home())
        .join(sid)
        .join(addr_clean(email))
        .join(crate::core::contract::binding_file());
    if let Ok(text) = std::fs::read_to_string(&aj) {
        if let Ok(v) = serde_json::from_str::<Value>(&text) {
            let wu = v.get("webhook_url").and_then(|x| x.as_str()).unwrap_or("");
            if !wu.is_empty() {
                return wu.to_string();
            }
        }
    }
    let cfg = crate::core::config::load_gateway_config(sid);
    if let Some(c) = cfg {
        let sh = c.system_home.clone();
        if !sh.is_empty() && std::path::Path::new(&sh).join("hermes-agent").exists() {
            let base = std::path::Path::new(&sh);
            let tail = email
                .split('@')
                .next()
                .unwrap_or("")
                .rsplit('.')
                .next()
                .unwrap_or("");
            let mut p = base.join("profiles").join(tail).join("config.yaml");
            if !p.exists() {
                p = base.join("config.yaml");
            }
            let port = std::fs::read_to_string(&p)
                .ok()
                .and_then(|t| yaml_rust2::YamlLoader::load_from_str(&t).ok())
                .and_then(|docs| docs.into_iter().next())
                .and_then(|doc| {
                    let wh = &doc["platforms"]["webhook"];
                    wh["port"].as_i64().or_else(|| wh["extra"]["port"].as_i64())
                })
                .unwrap_or(8646);
            // hermes 的入站路径是**契约值**（hermes 是唯一路径例外）⇒ 走常量，不写字面量
            return format!(
                "http://127.0.0.1:{}{}",
                port,
                crate::core::contract::hermes_inbound_path()
            );
        }
    }
    "http://127.0.0.1:8799/hook".to_string()
}

/// `_system_agents`：该系统所有 agent 的 `(email, 接收端 URL)`。
/// 接收端**优先取绑定的 webhook_url**（唯一信任源，含平台真实路径）；
/// 回退历史路由表，再回退平台默认（照抄 Python 的三级顺序）。
pub fn system_agents(sid: &str) -> Vec<(String, String)> {
    let routes = read_routes(&routes_file());
    let mut out = Vec::new();
    for (em, wu) in registered_bindings(sid) {
        let target = if !wu.is_empty() {
            wu
        } else if let Some(r) = routes.get(&em) {
            r.clone()
        } else {
            default_recv_url(sid, &em)
        };
        out.push((em, target));
    }
    out
}
