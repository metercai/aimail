//! 系统级环境文件（`aimail_gateway.json`）的读写 + per-agent 绑定文件的**只读**入口。
//!
//! 归属铁律（owner 2026-09-28 裁决，判定器 `tests/contract/check-file-ownership.py:5-17`）：
//!
//! ```text
//! 系统级环境文件  aimail_gateway.json  ← 只由 CLI 写（读双向允许）
//! per-agent 绑定文件                   ← 只由 SDK 写（读双向允许）
//! ```
//!
//! ⇒ 本模块**只实现系统级文件的写**；绑定文件只提供路径解析（供读），
//! **不得**出现任何写调用，也不得把绑定文件名写进写调用的实参。
//!
//! 现状码对照（逐字对齐字段与条件键）：
//! - 写：`cli/setup_system.py:331-370`（`_save_gateway_config`：基础键恒写，
//!   `domain/manager_address/webhook_host/system_home` **仅非空才写**）
//! - 原子落盘：`cli/aimail:2460-2467`（`_atomic_json_write`：`<path>.json.tmp` →
//!   chmod 0600 → `replace`）
//! - 读：`pysdk/gateway_api.py:16-31`（路径 = `<aimail_home>/systems/<sid>/` +
//!   文件名；读不到/解析不了 ⇒ `None`）

use crate::core::{contract, home, perms};
use serde_json::{Map, Value};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// 系统级环境文件名。**不属** agent 内部契约面（契约清单只约束注册名/绑定名/指针名/入站路径）
/// ⇒ 这里是本模块自己的常量，不是契约字面量的副本。
pub const GATEWAY_CONFIG_NAME: &str = "aimail_gateway.json";

/// 系统级环境文件的内容模型。
///
/// - 基础键恒写（与现状码一致，即使是空串）；
/// - `domain` / `manager_address` / `webhook_host` / `system_home` / `container_home` /
///   `default_agent_name` **仅非空才写**；
/// - 未知/未来键原样保留在 `extra`（读→写不丢字段，等价 Python dict 行为）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GatewayConfig {
    pub gateway_url: String,
    pub admin_key: String,
    pub system_id: String,
    pub system_name: String,
    pub save_raw_snapshots: bool,
    pub domain: String,
    pub manager_address: String,
    pub webhook_host: String,
    pub system_home: String,
    pub container_home: String,
    pub default_agent_name: String,
    pub extra: Map<String, Value>,
}

/// 基础键（恒写，不参与 extra）。
const BASE_KEYS: [&str; 5] = [
    "gateway_url",
    "admin_key",
    "system_id",
    "system_name",
    "save_raw_snapshots",
];

/// 条件键（仅非空才写）。
const CONDITIONAL_KEYS: [&str; 6] = [
    "domain",
    "manager_address",
    "webhook_host",
    "system_home",
    "container_home",
    "default_agent_name",
];

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            gateway_url: String::new(),
            admin_key: String::new(),
            system_id: String::new(),
            system_name: String::new(),
            save_raw_snapshots: true, // 现状默认 True（setup_system 构造默认）
            domain: String::new(),
            manager_address: String::new(),
            webhook_host: String::new(),
            system_home: String::new(),
            container_home: String::new(),
            default_agent_name: String::new(),
            extra: Map::new(),
        }
    }
}

impl GatewayConfig {
    /// 从 JSON 对象宽松读取：未知键进 `extra`；类型不符时按"缺失"处理（不抛）。
    pub fn from_json(obj: &Map<String, Value>) -> Self {
        let mut cfg = Self::default();
        let s = |k: &str| obj.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        cfg.gateway_url = s("gateway_url");
        cfg.admin_key = s("admin_key");
        cfg.system_id = s("system_id");
        cfg.system_name = s("system_name");
        cfg.save_raw_snapshots = obj
            .get("save_raw_snapshots")
            .and_then(Value::as_bool)
            .unwrap_or(true);
        cfg.domain = s("domain");
        cfg.manager_address = s("manager_address");
        cfg.webhook_host = s("webhook_host");
        cfg.system_home = s("system_home");
        cfg.container_home = s("container_home");
        cfg.default_agent_name = s("default_agent_name");
        for (k, v) in obj {
            if !BASE_KEYS.contains(&k.as_str()) && !CONDITIONAL_KEYS.contains(&k.as_str()) {
                cfg.extra.insert(k.clone(), v.clone());
            }
        }
        cfg
    }

    /// 转回 JSON 对象（键序 = 现状码的书写顺序；`extra` 追加在末尾）。
    pub fn to_json(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert(
            "gateway_url".into(),
            Value::String(self.gateway_url.clone()),
        );
        m.insert("admin_key".into(), Value::String(self.admin_key.clone()));
        m.insert("system_id".into(), Value::String(self.system_id.clone()));
        m.insert(
            "system_name".into(),
            Value::String(self.system_name.clone()),
        );
        m.insert(
            "save_raw_snapshots".into(),
            Value::Bool(self.save_raw_snapshots),
        );
        for (key, val) in [
            ("domain", &self.domain),
            ("manager_address", &self.manager_address),
            ("webhook_host", &self.webhook_host),
            ("system_home", &self.system_home),
            ("container_home", &self.container_home),
            ("default_agent_name", &self.default_agent_name),
        ] {
            if !val.is_empty() {
                m.insert(key.into(), Value::String(val.clone()));
            }
        }
        for (k, v) in &self.extra {
            m.insert(k.clone(), v.clone());
        }
        m
    }
}

// ── 路径 ───────────────────────────────────────────────────────────────────

/// `<home>/systems`（`home` 显式传入，便于测试与 `-H`）。
pub fn systems_root_in(home_dir: &Path) -> PathBuf {
    home_dir.join("systems")
}

/// `<home>/systems/<sid>`。
pub fn system_dir_in(home_dir: &Path, sid: &str) -> PathBuf {
    systems_root_in(home_dir).join(sid)
}

/// 系统级环境文件路径：`<home>/systems/<sid>/<GATEWAY_CONFIG_NAME>`。
pub fn gateway_config_path_in(home_dir: &Path, sid: &str) -> PathBuf {
    system_dir_in(home_dir, sid).join(GATEWAY_CONFIG_NAME)
}

/// 同上，用本机主根（`AIMAIL_HOME` > `~/.aimail`）。
pub fn gateway_config_path(sid: &str) -> PathBuf {
    gateway_config_path_in(&home::aimail_home(), sid)
}

/// per-agent 绑定文件路径 —— **只读入口**（写权在 SDK，见模块头部的归属铁律）。
///
/// 目录名 = 地址按 `clean_agent_dir_name` 归一（与 Python 同一公式）；
/// 文件名 = 契约常量（`contract::binding_file()`），**不在本文件里复制字面量**。
pub fn binding_file_path_in(home_dir: &Path, sid: &str, addr: &str) -> PathBuf {
    system_dir_in(home_dir, sid)
        .join(clean_agent_dir_name(addr))
        .join(contract::binding_file())
}

/// 同上，用本机主根。
pub fn binding_file_path(sid: &str, addr: &str) -> PathBuf {
    binding_file_path_in(&home::aimail_home(), sid, addr)
}

/// 地址 → 目录名。与 `cli/_common.py:37-39` / `pysdk/aimail_base._clean_agent_dir_name`
/// 同一公式：`re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)`
/// —— **ASCII** 语义：非 ASCII 字母也算非法字符，替换成 `_`。
pub fn clean_agent_dir_name(addr: &str) -> String {
    addr.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

// ── 读写 ───────────────────────────────────────────────────────────────────

/// 读系统级环境文件：缺失或不可解析 ⇒ `None`（与现状 `load_gateway_config` 同语义）。
pub fn load_gateway_config_in(home_dir: &Path, sid: &str) -> Option<GatewayConfig> {
    let raw = fs::read_to_string(gateway_config_path_in(home_dir, sid)).ok()?;
    let value: Value = serde_json::from_str(&raw).ok()?;
    let obj = value.as_object()?;
    Some(GatewayConfig::from_json(obj))
}

/// 同上，用本机主根。
pub fn load_gateway_config(sid: &str) -> Option<GatewayConfig> {
    load_gateway_config_in(&home::aimail_home(), sid)
}

/// 读-改-写（原子）系统级环境文件；返回落盘路径。
///
/// 缺失文件按 `GatewayConfig::default()` 起步（等价现状"读不到当空 dict"）。
pub fn update_gateway_config_in<F>(home_dir: &Path, sid: &str, mutate: F) -> io::Result<PathBuf>
where
    F: FnOnce(&mut GatewayConfig),
{
    let mut cfg = load_gateway_config_in(home_dir, sid).unwrap_or_default();
    mutate(&mut cfg);
    save_gateway_config_in(home_dir, sid, &cfg)
}

/// 整体写入系统级环境文件（原子 + 0600）。
pub fn save_gateway_config_in(
    home_dir: &Path,
    sid: &str,
    cfg: &GatewayConfig,
) -> io::Result<PathBuf> {
    let path = gateway_config_path_in(home_dir, sid);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    write_private_json(&path, &Value::Object(cfg.to_json()))?;
    Ok(path)
}

/// 原子写 JSON + 收紧权限（对齐 `cli/aimail:2460-2467`）：
/// 同目录 `<path>.json.tmp` → chmod 0600 → `rename` 覆盖。
///
/// 序列化沿用 Python 现状：2 空格缩进 + UTF-8 原样（不转义非 ASCII）+ **无尾部换行**。
pub fn write_private_json(path: &Path, value: &Value) -> io::Result<()> {
    let tmp = path.with_extension("json.tmp");
    let text = serde_json::to_string_pretty(value).map_err(io::Error::other)?;
    fs::write(&tmp, text)?;
    // 尽力而为：权限收紧失败不阻断（Python 侧同样 except OSError: pass）。
    let _ = perms::set_user_only(&tmp);
    fs::rename(&tmp, path)
}

/// `_env_val`：shell env **优先**，其次 `$AIMAIL_HOME/.env`（bootstrap 落盘的机器级配置），
/// 最后 fallback。install / ensure-system 必须认 .env —— 调用的 shell 里常常没有 export。
pub fn env_val(key: &str, fallback: &str) -> String {
    if let Ok(v) = std::env::var(key) {
        if !v.is_empty() {
            return v;
        }
    }
    let env_file = crate::core::home::aimail_home().join(".env");
    if let Ok(text) = std::fs::read_to_string(&env_file) {
        let prefix = format!("{key}=");
        for line in text.lines() {
            let line = line.trim();
            if let Some(rest) = line.strip_prefix(&prefix) {
                return rest.trim().to_string();
            }
        }
    }
    fallback.to_string()
}

/// `_GATEWAY_URL_DEFAULT`（`cli/aimail:146`）：网关生产默认地址（非契约清单值）。
pub const GATEWAY_URL_DEFAULT: &str = "https://aimail.token.tm";

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn clean_agent_dir_name_matches_python_ascii_semantics() {
        assert_eq!(clean_agent_dir_name("agent@host.test"), "agent_host.test");
        assert_eq!(clean_agent_dir_name("a+b@c"), "a_b_c");
        assert_eq!(clean_agent_dir_name("keep._-"), "keep._-");
        // ASCII 语义：非 ASCII 字母同样算非法字符（rust 侧不按 unicode 放宽）
        assert_eq!(clean_agent_dir_name("агент@x"), "______x");
    }

    #[test]
    fn paths_follow_repo_layout() {
        let h = Path::new("/tmp/aimail-x");
        assert_eq!(
            gateway_config_path_in(h, "sid1"),
            Path::new("/tmp/aimail-x/systems/sid1/aimail_gateway.json")
        );
        let b = binding_file_path_in(h, "sid1", "agent@host.test");
        assert_eq!(
            b.parent().unwrap(),
            Path::new("/tmp/aimail-x/systems/sid1/agent_host.test")
        );
        assert_eq!(b.file_name().unwrap(), contract::binding_file());
    }

    #[test]
    fn gateway_config_round_trip_preserves_unknown_keys() {
        let tmp = TempDir::new("config");
        let h = tmp.path();
        let mut cfg = GatewayConfig {
            gateway_url: "http://127.0.0.1:38999".into(),
            admin_key: "k".into(),
            system_id: "sid1".into(),
            system_name: "name1".into(),
            ..Default::default()
        };
        cfg.extra
            .insert("future_key".into(), Value::String("keep-me".into()));
        save_gateway_config_in(h, "sid1", &cfg).expect("save");

        let back = load_gateway_config_in(h, "sid1").expect("load");
        assert_eq!(back, cfg, "round trip must be lossless");
        assert!(back.save_raw_snapshots, "default must stay true");
        assert!(back.domain.is_empty() && back.webhook_host.is_empty());
    }

    #[test]
    fn update_merges_and_only_writes_conditional_keys_when_set() {
        let tmp = TempDir::new("config-merge");
        let h = tmp.path();
        update_gateway_config_in(h, "sid1", |c| {
            c.gateway_url = "http://g".into();
            c.admin_key = "k".into();
            c.system_id = "sid1".into();
        })
        .expect("first write");

        let raw = std::fs::read_to_string(gateway_config_path_in(h, "sid1")).unwrap();
        assert!(
            !raw.contains("domain"),
            "empty conditional key must not be written: {raw}"
        );
        assert!(
            raw.trim_end().ends_with('}'),
            "no trailing newline expected"
        );

        update_gateway_config_in(h, "sid1", |c| c.default_agent_name = "agent".into())
            .expect("second write");
        let after = load_gateway_config_in(h, "sid1").unwrap();
        assert_eq!(after.default_agent_name, "agent");
        assert_eq!(
            after.gateway_url, "http://g",
            "earlier fields must survive the merge"
        );
        assert_eq!(after.admin_key, "k");
    }

    #[test]
    fn credential_file_is_user_only_and_tmp_is_gone() {
        let tmp = TempDir::new("config-perm");
        let h = tmp.path();
        let p = save_gateway_config_in(h, "sid1", &GatewayConfig::default()).expect("save");
        assert!(p.is_file());
        assert!(
            !p.with_extension("json.tmp").exists(),
            "tmp file must be renamed away"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&p).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "credential file must be 0600, got {mode:o}");
        }
    }

    #[test]
    fn unreadable_or_missing_file_is_none_not_a_panic() {
        let tmp = TempDir::new("config-missing");
        assert!(load_gateway_config_in(tmp.path(), "nosuch").is_none());
        let bad = gateway_config_path_in(tmp.path(), "broken");
        fs::create_dir_all(bad.parent().unwrap()).unwrap();
        fs::write(&bad, b"{not json").unwrap();
        assert!(load_gateway_config_in(tmp.path(), "broken").is_none());
    }
}
