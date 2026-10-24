//! Read/write of the system-level env file (`aimail_gateway.json`) + **read-only** entry for per-agent binding files.
//!
//! Ownership iron rule (owner 2026-09-28 ruling, checker `tests/contract/check-file-ownership.py:5-17`):
//!
//! ```text
//! System-level environment file  aimail_gateway.json  ← written only by CLI (bidirectional read allowed)
//! per-agent binding file                   ← written only by SDK (bidirectional read allowed)
//! ```
//!
//! ⇒ This module **only implements writing system-level files**; for binding files it only provides path resolution (for reading),
//! **must not** contain any write calls, nor write binding file names into write call arguments.
//!
//! Current code comparison (field and condition key alignment verbatim):
//! - Write: `cli/setup_system.py:331-370` (`_save_gateway_config`: base keys always written,
//!   `domain/manager_address/webhook_host/system_home` **only written if non-empty**)
//! - Atomic write: `cli/aimail:2460-2467` (`_atomic_json_write`: `<path>.json.tmp` →
//!   chmod 0600 → `replace`）
//! - Read: `pysdk/gateway_api.py:16-31` (path = `<aimail_home>/systems/<sid>/` +
//!   filename; unreadable/unparseable ⇒ `None`)

use crate::core::{contract, home, perms};
use serde_json::{Map, Value};
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

/// System-level environment file name. **Not part of** agent internal contract surface (contract list only constrains registration name/binding name/pointer name/inbound path)
/// ⇒ These are this module's own constants, not copies of contract literals.
pub const GATEWAY_CONFIG_NAME: &str = "aimail_gateway.json";

/// Content model for system-level environment file.
///
/// - Base keys always written (consistent with current code, even if empty string);
/// - `domain` / `manager_address` / `system_home` / `container_home` **only written if non-empty**;
/// - `webhook_host` is **tri-state** (owner 2026-10-13): `None` = key absent (S3 direct),
///   `Some("")` = empty string (S2 bridge pull), `Some(url)` = S1 via bridge push BRIDGE_HOST.
///   Using `Option` instead of `String` is precisely to distinguish "absent" from "empty string" — currently `String` conflates the two
///   to empty string, unable to express S3;
/// - Unknown/future keys are preserved as-is in `extra` (read→write does not lose fields, equivalent to Python dict behavior).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GatewayConfig {
    pub gateway_url: String,
    pub admin_key: String,
    pub system_id: String,
    pub system_name: String,
    pub save_raw_snapshots: bool,
    pub domain: String,
    pub manager_address: String,
    pub webhook_host: Option<String>,
    pub system_home: String,
    pub container_home: String,
    pub extra: Map<String, Value>,
}

/// Base keys (always written, not part of extra).
const BASE_KEYS: [&str; 5] = [
    "gateway_url",
    "admin_key",
    "system_id",
    "system_name",
    "save_raw_snapshots",
];

/// Conditional keys (written only if non-empty). `webhook_host` is not in this list — it is tri-state (see struct comment),
/// handled separately in `to_json` (`None` not written, `Some("")` writes empty string, `Some(url)` writes url).
const CONDITIONAL_KEYS: [&str; 4] = ["domain", "manager_address", "system_home", "container_home"];

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            gateway_url: String::new(),
            admin_key: String::new(),
            system_id: String::new(),
            system_name: String::new(),
            save_raw_snapshots: true, // Current default True (setup_system construction default)
            domain: String::new(),
            manager_address: String::new(),
            webhook_host: None,
            system_home: String::new(),
            container_home: String::new(),
            extra: Map::new(),
        }
    }
}

impl GatewayConfig {
    /// Loose read from JSON object: unknown keys go to `extra`; type mismatch treated as "missing" (no throw).
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
        // webhook_host tri-state: key absent ⇒ None; key present (even if empty string) ⇒ Some(value).
        cfg.webhook_host = obj
            .get("webhook_host")
            .and_then(Value::as_str)
            .map(|v| v.to_string());
        cfg.system_home = s("system_home");
        cfg.container_home = s("container_home");
        for (k, v) in obj {
            if !BASE_KEYS.contains(&k.as_str()) && !CONDITIONAL_KEYS.contains(&k.as_str()) {
                cfg.extra.insert(k.clone(), v.clone());
            }
        }
        cfg
    }

    /// Convert back to JSON object (key order = current code writing order; `extra` appended at end).
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
        // Conditional keys in current code writing order; webhook_host is Option: None not written (S3 key absent),
        // Some("")/Some(url) both written (S2 empty string / S1 URL). Other keys still "only written if non-empty".
        let entries: [(&str, Option<&String>); 5] = [
            ("domain", Some(&self.domain)),
            ("manager_address", Some(&self.manager_address)),
            ("webhook_host", self.webhook_host.as_ref()),
            ("system_home", Some(&self.system_home)),
            ("container_home", Some(&self.container_home)),
        ];
        for (key, val) in entries {
            if let Some(v) = val {
                // webhook_host allows empty string (Some("") persisted); other keys empty string not written
                let skip = key != "webhook_host" && v.is_empty();
                if !skip {
                    m.insert(key.into(), Value::String(v.clone()));
                }
            }
        }
        for (k, v) in &self.extra {
            m.insert(k.clone(), v.clone());
        }
        m
    }
}

// ── Path ───────────────────────────────────────────────────────────────────

/// `<home>/systems` (`home` is passed explicitly for testing and `-H`).
pub fn systems_root_in(home_dir: &Path) -> PathBuf {
    home_dir.join("systems")
}

/// `<home>/systems/<sid>`。
pub fn system_dir_in(home_dir: &Path, sid: &str) -> PathBuf {
    systems_root_in(home_dir).join(sid)
}

/// List of valid system ids on this machine (`<home>/systems/<sid>/` containing `aimail_gateway.json`),
/// sorted stably (caller determines "uniqueness", order has no business meaning).
pub fn list_system_ids_in(home_dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(rd) = fs::read_dir(systems_root_in(home_dir)) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if name.starts_with('.') {
                continue;
            }
            if gateway_config_path_in(home_dir, &name).is_file() {
                out.push(name);
            }
        }
    }
    out.sort();
    out
}

/// System-level environment file path: `<home>/systems/<sid>/<GATEWAY_CONFIG_NAME>`.
pub fn gateway_config_path_in(home_dir: &Path, sid: &str) -> PathBuf {
    system_dir_in(home_dir, sid).join(GATEWAY_CONFIG_NAME)
}

/// Same as above, using the local main root (`AIMAIL_HOME` > `~/.aimail`).
pub fn gateway_config_path(sid: &str) -> PathBuf {
    gateway_config_path_in(&home::aimail_home(), sid)
}

/// per-agent binding file path —— **read-only entry** (write authority lies with SDK, see ownership iron rule in module header).
///
/// Directory name = address normalized by `clean_agent_dir_name` (same formula as Python);
/// File name = contract constant (`contract::binding_file()`), **do not duplicate the literal in this file**.
pub fn binding_file_path_in(home_dir: &Path, sid: &str, addr: &str) -> PathBuf {
    system_dir_in(home_dir, sid)
        .join(clean_agent_dir_name(addr))
        .join(contract::binding_file())
}

/// Same as above, using the local main root.
pub fn binding_file_path(sid: &str, addr: &str) -> PathBuf {
    binding_file_path_in(&home::aimail_home(), sid, addr)
}

/// Address → directory name. Consistent with `cli/_common.py:37-39` / `pysdk/aimail_base._clean_agent_dir_name`
/// `re.sub(r"[^\w.\-]", "_", addr, flags=re.ASCII)`
/// —— **ASCII** semantics: non-ASCII letters are also considered invalid characters and replaced with `_`.
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

// ── Read/Write ───────────────────────────────────────────────────────────────────

/// Read system-level environment file: missing or unparseable ⇒ `None` (same semantics as current `load_gateway_config`).
pub fn load_gateway_config_in(home_dir: &Path, sid: &str) -> Option<GatewayConfig> {
    let raw = fs::read_to_string(gateway_config_path_in(home_dir, sid)).ok()?;
    let value: Value = serde_json::from_str(&raw).ok()?;
    let obj = value.as_object()?;
    Some(GatewayConfig::from_json(obj))
}

/// Same as above, using the local main root.
pub fn load_gateway_config(sid: &str) -> Option<GatewayConfig> {
    load_gateway_config_in(&home::aimail_home(), sid)
}

/// Read-modify-write (atomic) system-level environment file; returns the persisted path.
///
/// Missing files start with `GatewayConfig::default()` (equivalent to current "treat as empty dict if unreadable").
pub fn update_gateway_config_in<F>(home_dir: &Path, sid: &str, mutate: F) -> io::Result<PathBuf>
where
    F: FnOnce(&mut GatewayConfig),
{
    let mut cfg = load_gateway_config_in(home_dir, sid).unwrap_or_default();
    mutate(&mut cfg);
    save_gateway_config_in(home_dir, sid, &cfg)
}

/// Write entire system-level environment file (atomic + 0600).
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

/// Atomic JSON write + tighten permissions (aligned with `cli/aimail:2460-2467`):
/// Same directory `<path>.json.tmp` → chmod 0600 → `rename` overwrite.
///
/// Serialization follows Python current state: 2-space indent + UTF-8 as-is (no escaping of non-ASCII) + **no trailing newline**.
/// 0600 private **text** write (used by `_persist_system_raw_key`: `os.open(..., 0o600)` + write).
pub fn write_private_text(path: &Path, text: &str) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(path)?;
    f.write_all(text.as_bytes())
}

pub fn write_private_json(path: &Path, value: &Value) -> io::Result<()> {
    let tmp = path.with_extension("json.tmp");
    let text = serde_json::to_string_pretty(value).map_err(io::Error::other)?;
    fs::write(&tmp, text)?;
    // Best effort: permission tightening failure does not block (Python side also does except OSError: pass).
    let _ = perms::set_user_only(&tmp);
    fs::rename(&tmp, path)
}

/// `_env_val`: shell env **priority**, then `$AIMAIL_HOME/.env` (machine-level config persisted by bootstrap),
/// Final fallback. install / ensure-system must recognize .env —— the invoked shell often lacks export.
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

/// `_GATEWAY_URL_DEFAULT` (`cli/aimail:146`): gateway production default address (not a contract list value).
pub const GATEWAY_URL_DEFAULT: &str = "https://aimail.token.tm";

/// System layer **raw** key file (persisted by `setup_system._persist_system_raw_key`, 0600 three-layer closure).
/// Read-only; missing/unreadable ⇒ empty string (caller then proceeds with admin_key from config, see `cmd_reset`/`uninstall`).
pub const SYSTEM_RAW_KEY_FILE: &str = ".system_raw_key.key";

pub fn read_system_raw_key(sid: &str) -> String {
    let p = system_dir_in(&crate::core::home::aimail_home(), sid).join(SYSTEM_RAW_KEY_FILE);
    std::fs::read_to_string(p)
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

/// `_load_env` (`cli/aimail:86-108`): inject **machine-level and repository** `.env` into process environment (**never overwrite** existing values).
///
/// Order: `$HOME/.aimail/.env` → `<program_root>/aimail-src/.env` (= Python's `<cli>/../.env`)
/// → `<program_root>/aimail-src/cli/.env`. Skip empty lines/comments/lines without `=`, strip quotes from values.
/// Why CLI must do this: `.env` is machine-level config persisted by bootstrap, the caller's shell often does not have export,
/// and install/reset/check value retrieval criteria are based on process environment (empirically: missing it causes fields like manager_address to diverge).
pub fn load_env() {
    let prog = crate::core::home::program_root();
    let cands = [
        crate::core::home::user_home().join(".aimail").join(".env"),
        prog.join("aimail-src").join(".env"),
        prog.join("aimail-src").join("cli").join(".env"),
    ];
    for c in cands {
        let Ok(text) = std::fs::read_to_string(&c) else {
            continue;
        };
        for line in text.lines() {
            let s = line.trim();
            if s.is_empty() || s.starts_with('#') || !s.contains('=') {
                continue;
            }
            let (k, v) = s.split_once('=').unwrap();
            let k = k.trim();
            let v = v.trim().trim_matches('"').trim_matches('\'');
            if !k.is_empty() && std::env::var_os(k).is_none() {
                std::env::set_var(k, v);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn clean_agent_dir_name_matches_python_ascii_semantics() {
        assert_eq!(clean_agent_dir_name("agent@host.test"), "agent_host.test");
        assert_eq!(clean_agent_dir_name("a+b@c"), "a_b_c");
        assert_eq!(clean_agent_dir_name("keep._-"), "keep._-");
        // ASCII semantics: non-ASCII letters are also considered invalid characters (rust side does not relax by unicode)
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
        assert!(back.domain.is_empty() && back.webhook_host.is_none());
    }

    #[test]
    fn webhook_host_tri_state_round_trips() {
        let tmp = TempDir::new("config-3state");
        let h = tmp.path();
        let write_and_check = |wh: Option<String>, expect_key: bool, expect_val: &str| {
            let cfg = GatewayConfig {
                gateway_url: "http://g".into(),
                webhook_host: wh.clone(),
                ..Default::default()
            };
            save_gateway_config_in(h, "sid3", &cfg).expect("save");
            let raw = std::fs::read_to_string(gateway_config_path_in(h, "sid3")).unwrap();
            let v: Value = serde_json::from_str(&raw).unwrap();
            if expect_key {
                assert_eq!(
                    v.get("webhook_host").and_then(Value::as_str),
                    Some(expect_val),
                    "raw = {raw}"
                );
            } else {
                assert!(
                    !v.get("webhook_host").is_some(),
                    "key must be absent (S3), raw = {raw}"
                );
            }
            let back = load_gateway_config_in(h, "sid3").unwrap();
            assert_eq!(back.webhook_host, wh, "round trip must be lossless");
        };
        write_and_check(Some("".into()), true, ""); // S2 empty string
        write_and_check(Some("http://b:38080".into()), true, "http://b:38080"); // S1
        write_and_check(None, false, ""); // S3 key absent
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

        update_gateway_config_in(h, "sid1", |c| c.system_home = "/h".into()).expect("second write");
        let after = load_gateway_config_in(h, "sid1").unwrap();
        assert_eq!(after.system_home, "/h");
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

    #[test]
    fn list_system_ids_only_counts_dirs_with_gateway_config() {
        let tmp = TempDir::new("config-list");
        let h = tmp.path();
        // Valid systems (with cfg) + directories without cfg + dot-prefixed directories + "bad" directories without cfg
        save_gateway_config_in(h, "sid_a", &GatewayConfig::default()).unwrap();
        save_gateway_config_in(h, "sid_b", &GatewayConfig::default()).unwrap();
        fs::create_dir_all(system_dir_in(h, "no_cfg")).unwrap();
        fs::create_dir_all(h.join("systems").join(".hidden")).unwrap();
        let ids = list_system_ids_in(h);
        assert_eq!(
            ids,
            vec!["sid_a", "sid_b"],
            "Only count systems containing aimail_gateway.json: {ids:?}"
        );
    }
}
