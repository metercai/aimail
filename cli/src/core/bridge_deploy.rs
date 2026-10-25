//! The **binary placement** part of `cli/deploy_bridge.py` (P2 slice 3a) —— zip extraction + disk write + executable bit.
//!
//! Differences from Python (**deliberate improvements, no semantic change**): Python is "try `unzip` executable first, fallback to pure
//! Python `zipfile`" (2026-10-02 L2 container test showed no unzip ⇒ the dual channel is precisely for this). The Rust side directly embeds zip
//! extraction (zip crate), **not even `unzip` is needed** ⇒ better aligns with the owner's stance of "zero environment dependencies for the CLI itself",
//! and eliminates the failure mode of "can't install bridge if host lacks one tool". Extracted filenames, `aimail-bridge` prefix filtering,
//! The semantics of temporary directory → atomic replacement → `0755` are aligned line-by-line with Python.
//!
//! Other specs copied: `bridge/` extraction dir is at `<program_root>/bridge`; zip name
//! `aimail-bridge-v<semver>-linux-<arch>.zip`; **version compared semantically** (v1.10.0 > v1.2.3 > v1.2);
//! if already placed (executable), do not touch zip at all (idempotent).

use std::path::{Path, PathBuf};

use crate::cmd::report::warn;

/// `_version_key`: extract (major, minor, patch) from `aimail-bridge-vX.Y[.Z]-…`.
pub fn version_key(name: &str) -> (u64, u64, u64) {
    let Some(i) = name.find("-v") else {
        return (0, 0, 0);
    };
    let rest: Vec<&str> = name[i + 2..].split(['.', '-', '_']).collect();
    let num = |k: usize| -> u64 {
        rest.get(k)
            .map(|s| {
                s.chars()
                    .take_while(|c| c.is_ascii_digit())
                    .collect::<String>()
            })
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(0)
    };
    (num(0), num(1), num(2))
}

/// Local architecture name (equivalent mapping of `platform.machine()`).
pub fn arch_name() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "amd64",
        "aarch64" | "arm64" => "arm64",
        _ => "amd64",
    }
}

/// `latest_bridge_zip`: the latest `aimail-bridge-v*-linux-{arch}.zip` in `<bridge_dir_local>`.
pub fn latest_bridge_zip(bridge_dir_local: &Path, arch: &str) -> String {
    let suffix = format!("-linux-{}.zip", arch);
    let mut best: Option<(String, (u64, u64, u64))> = None;
    if let Ok(entries) = std::fs::read_dir(bridge_dir_local) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if !name.starts_with("aimail-bridge-v") || !name.ends_with(&suffix) {
                continue;
            }
            let key = version_key(&name);
            if best.as_ref().map(|(_, k)| key > *k).unwrap_or(true) {
                best = Some((name, key));
            }
        }
    }
    match best {
        Some((name, _)) => bridge_dir_local.join(name).to_string_lossy().to_string(),
        None => String::new(),
    }
}

/// Whether the file is executable (equivalent to `os.access(X_OK)`).
/// unix = permission bits include execute bit; windows = file exists (no POSIX bits, .exe presence is executable).
#[cfg(unix)]
pub fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}
#[cfg(not(unix))]
pub fn is_executable(p: &Path) -> bool {
    p.is_file()
}

/// Local bridge **deployable** (decision function BR input, 2026-10-13): an in-place executable binary,
/// Or there is a zip for the current architecture in this repo's `bridge/` that can be extracted. Pure read-only check, does not trigger any deployment action.
pub fn bridge_deployable() -> bool {
    let bridge_dir = crate::core::home::aimail_home().join("bridge/bin");
    if is_executable(&bridge_dir.join("aimail-bridge")) {
        return true;
    }
    let arch = arch_name();
    !latest_bridge_zip(&crate::core::home::program_root().join("bridge"), arch).is_empty()
}

/// `_ensure_binary`: extract from this repo's `bridge/` zip when binary is missing; idempotent.
pub fn ensure_binary(bridge_bin: &Path, bridge_dir: &Path) -> bool {
    if !is_executable(bridge_bin) {
        let bridge_dir_local = crate::core::home::program_root().join("bridge");
        let arch = arch_name();
        let zip_path = latest_bridge_zip(&bridge_dir_local, arch);
        if zip_path.is_empty() {
            warn(&format!(
                "No bridge zip found in {} (aimail-bridge-v*-linux-{}.zip)",
                bridge_dir_local.display(),
                arch
            ));
            return false;
        }
        // Extract to a temporary directory and rename uniformly, not relying on internal zip naming (copied from Python)
        let Some(td) = temp_dir() else {
            warn("Failed to extract bridge: cannot create a temp dir");
            return false;
        };
        let extracted = extract_bridge(&zip_path, &td);
        let Some(src) = extracted else {
            let _ = std::fs::remove_dir_all(&td);
            return false;
        };
        if std::fs::create_dir_all(bridge_dir).is_err() {
            warn(&format!("Failed to create {}", bridge_dir.display()));
            let _ = std::fs::remove_dir_all(&td);
            return false;
        }
        let renamed = std::fs::rename(&src, bridge_bin).is_ok();
        let _ = std::fs::remove_dir_all(&td);
        if !renamed {
            warn("Failed to extract bridge: cannot place the binary");
            return false;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(bridge_bin, std::fs::Permissions::from_mode(0o755));
        }
    }
    is_executable(bridge_bin)
}

/// Find the **top-level** `aimail-bridge*` regular file in the zip and extract it to `td` (returns the landing path).
fn extract_bridge(zip_path: &str, td: &Path) -> Option<PathBuf> {
    let file = match std::fs::File::open(zip_path) {
        Ok(f) => f,
        Err(e) => {
            warn(&format!("Failed to extract bridge: {e}"));
            return None;
        }
    };
    let mut archive = match zip::ZipArchive::new(file) {
        Ok(a) => a,
        Err(e) => {
            warn(&format!("Failed to extract bridge: {e}"));
            return None;
        }
    };
    // Python: `os.listdir(td)` ⇒ only look at **top-level** names and must be regular files
    let mut idx: Option<usize> = None;
    for i in 0..archive.len() {
        let name = match archive.by_index_raw(i) {
            Ok(f) => f.name().to_string(),
            Err(_) => continue,
        };
        if name.contains('/') || !name.starts_with("aimail-bridge") {
            continue;
        }
        idx = Some(i);
        break;
    }
    let Some(i) = idx else {
        warn(&format!("no aimail-bridge executable in zip: {zip_path}"));
        return None;
    };
    let mut entry = archive.by_index(i).ok()?;
    let dest = td.join("aimail-bridge-extracted");
    let mut out = std::fs::File::create(&dest).ok()?;
    if std::io::copy(&mut entry, &mut out).is_err() {
        warn("Failed to extract bridge: write error");
        return None;
    }
    Some(dest)
}

fn temp_dir() -> Option<PathBuf> {
    let base = std::env::temp_dir();
    for _ in 0..64 {
        let cand = base.join(format!("aimail-bridge-{}", std::process::id()));
        let cand = cand.join(format!("{}", nanos()));
        if std::fs::create_dir_all(&cand).is_ok() {
            return Some(cand);
        }
    }
    None
}

fn nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

// ── Config writing (`_config_lines` / `write_bridge_config`) ────────────────────────────

/// `_config_lines`: TOML body for single bridge multi-system. Line-by-line copy from Python (including comments and key order).
pub fn config_lines(
    addr: &str,
    mode: &str,
    merged: &[toml::Value],
    log_path: &str,
    hostname: &str,
) -> Vec<String> {
    let mut lines = vec![
        "schema_version = 1".to_string(),
        format!("bind = \"{}\"", addr),
        format!("mode = \"{}\"", mode),
    ];
    if !hostname.is_empty() {
        lines.push(format!("hostname = \"{}\"", hostname));
    }
    lines.extend(
        [
            "",
            "[logging]",
            &format!("file = \"{}\"", log_path),
            "level = \"info\"",
            "",
            "[pull]",
            "# Single bridge multi-system: one entry per system, independent key/system_id/dedup/backoff",
            "systems = [",
        ]
        .iter()
        .map(|s| s.to_string()),
    );
    for (i, s) in merged.iter().enumerate() {
        let comma = if i + 1 < merged.len() { "," } else { "" };
        let get = |k: &str| s.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
        let mut parts = vec![
            format!("aimail_url = \"{}\"", get("aimail_url")),
            format!("admin_key = \"{}\"", get("admin_key")),
            format!("system_id = \"{}\"", get("system_id")),
            format!(
                "poll_interval_sec = {}",
                s.get("poll_interval_sec")
                    .and_then(|v| v.as_integer())
                    .unwrap_or(2)
            ),
        ];
        if !get("api_key").is_empty() {
            parts.push(format!("api_key = \"{}\"", get("api_key")));
        }
        if !get("webhook_secret").is_empty() {
            parts.push(format!("webhook_secret = \"{}\"", get("webhook_secret")));
        }
        lines.push(format!("  {{ {} }}{}", parts.join(", "), comma));
    }
    lines.extend(
        [
            "]",
            "",
            "[health]",
            "check_interval_sec = 30",
            "fail_threshold = 6",
            "connect_timeout_sec = 3",
        ]
        .iter()
        .map(|s| s.to_string()),
    );
    lines
}

/// Input parameters for `write_bridge_config` (merged writing for single bridge multi-system).
pub struct BridgeConfigSpec {
    pub path: PathBuf,
    pub mode: String,
    pub addr: String,
    pub gateway_url: String,
    pub admin_key: String,
    pub system_id: String,
    pub api_key: String,
    pub webhook_secret: String,
    pub hostname: String,
}

/// `write_bridge_config`: **merge** (single bridge multiple systems) —— entries in existing `[pull].systems` that are not this sid are preserved.
pub fn write_bridge_config(spec: &BridgeConfigSpec) -> Result<(), String> {
    let log_path = crate::core::home::aimail_home()
        .join("bridge")
        .join("aimail-bridge.log");
    let log_path = log_path.to_string_lossy().to_string();

    let mut new_entry = toml::value::Table::new();
    new_entry.insert(
        "aimail_url".into(),
        toml::Value::String(spec.gateway_url.clone()),
    );
    new_entry.insert(
        "admin_key".into(),
        toml::Value::String(spec.admin_key.clone()),
    );
    new_entry.insert(
        "system_id".into(),
        toml::Value::String(spec.system_id.clone()),
    );
    new_entry.insert("poll_interval_sec".into(), toml::Value::Integer(2));
    if !spec.api_key.is_empty() {
        new_entry.insert("api_key".into(), toml::Value::String(spec.api_key.clone()));
    }
    if !spec.webhook_secret.is_empty() {
        new_entry.insert(
            "webhook_secret".into(),
            toml::Value::String(spec.webhook_secret.clone()),
        );
    }
    let new_entry = toml::Value::Table(new_entry);

    // Read old config (silently ignore bad TOML — mirrors Python's `except Exception: pass`)
    let mut existing: Vec<toml::Value> = Vec::new();
    if spec.path.exists() {
        if let Ok(text) = std::fs::read_to_string(&spec.path) {
            if let Ok(old) = text.parse::<toml::Value>() {
                if let Some(arr) = old
                    .get("pull")
                    .and_then(|p| p.get("systems"))
                    .and_then(|s| s.as_array())
                {
                    existing = arr.clone();
                }
            }
        }
    }
    let mut merged: Vec<toml::Value> = existing
        .into_iter()
        .filter(|s| s.get("system_id").and_then(|v| v.as_str()) != Some(spec.system_id.as_str()))
        .collect();
    merged.push(new_entry);

    if let Some(dir) = spec.path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let body =
        config_lines(&spec.addr, &spec.mode, &merged, &log_path, &spec.hostname).join("\n") + "\n";
    std::fs::write(&spec.path, body).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&spec.path, std::fs::Permissions::from_mode(0o600));
        // Credential file (AUDIT-1 F6)
    }
    Ok(())
}

// ── Bridge lifecycle (`_common.bridge_ctl_supported/status/stop` + `deploy_bridge.start_bridge`) ──
//
// Contract (aimail-bridge >= 0.7.4): `--status [--json]` / `--stop --pid-file <p>`;
// Exit codes 0 success|running · 1 error|rejected · 2 invalid config · 3 not running.
// Unsupported old bridge ⇒ fallback to "pid file + liveness probe" (with warning), CLI no longer kills processes itself.

/// `bridge_ctl_supported`: whether the binary supports the lifecycle contract.
pub fn ctl_supported(bin_path: &str) -> bool {
    match run_capture(&[bin_path, "--status", "--json"], 10) {
        Some((rc, out, _)) => (rc == 0 || rc == 3) && out.contains("\"running\""),
        None => false,
    }
}

/// `bridge_status`: contract first; old bridge falls back to pid file + liveness probe.
pub fn bridge_status(bin_path: &str, pid_path: &str) -> serde_json::Value {
    if ctl_supported(bin_path) {
        return match run_capture(
            &[bin_path, "--status", "--json", "--pid-file", pid_path],
            10,
        ) {
            Some((_, out, _)) => {
                let last = out.lines().last().unwrap_or("").trim().to_string();
                let mut d: serde_json::Value =
                    serde_json::from_str(&last).unwrap_or_else(|_| serde_json::json!({}));
                if !d.is_object() {
                    d = serde_json::json!({});
                }
                d["running"] =
                    serde_json::json!(d.get("running").and_then(|v| v.as_bool()).unwrap_or(false));
                d["_via"] = serde_json::json!("bridge-ctl");
                d
            }
            None => {
                serde_json::json!({"running": false, "reason": "status-failed", "_via": "bridge-ctl"})
            }
        };
    }
    let pid = read_pid_file(pid_path);
    serde_json::json!({
        "running": pid.map(pid_alive).unwrap_or(false),
        "pid": pid,
        "reason": "legacy-pid-check",
        "_via": "pid-file",
    })
}

/// `bridge_stop`: contract first (including identity verification, non-bridge rejected); old bridge falls back to TERM→KILL.
pub fn bridge_stop(bin_path: &str, pid_path: &str) -> i32 {
    if ctl_supported(bin_path) {
        return match run_capture(&[bin_path, "--stop", "--pid-file", pid_path], 40) {
            Some((rc, _, err)) => {
                let lines: Vec<&str> = err.lines().filter(|l| !l.trim().is_empty()).collect();
                for line in lines.iter().rev().take(2).rev() {
                    println!("    {}", line.trim());
                }
                rc
            }
            None => {
                println!("    stop failed");
                1
            }
        };
    }
    let Some(pid) = read_pid_file(pid_path) else {
        return 0;
    };
    if !pid_alive(pid) {
        return 0;
    }
    println!("    (Old bridge has no contract capability → fallback to TERM/KILL(windows = taskkill); recommend upgrading bridge to >= 0.7.4)");
    kill_pid_compat(pid as u32, false);
    std::thread::sleep(std::time::Duration::from_secs(1));
    if pid_alive(pid) {
        kill_pid_compat(pid as u32, true);
    }
    0
}

/// `_read_pid_file`。
pub fn read_pid_file(pid_path: &str) -> Option<i32> {
    std::fs::read_to_string(pid_path)
        .ok()
        .and_then(|t| t.trim().parse::<i32>().ok())
}

/// `pid_alive` (unix: `kill -0`; windows: tasklist, same criteria as bridge.rs).
pub fn pid_alive(pid: i32) -> bool {
    #[cfg(windows)]
    {
        let o = std::process::Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}")])
            .output();
        return o
            .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
            .unwrap_or(false);
    }
    #[cfg(unix)]
    {
        std::process::Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
}

/// Platform-adaptive termination (unix = `kill -15`/`-9` two levels; windows = `taskkill /F` direct force kill,
/// No strict graceful stop counterpart — W2 plan decision 2026-10-16; caller's wait loop still follows pid_alive original semantics).
#[cfg(unix)]
fn kill_pid_compat(pid: u32, force: bool) {
    let _ = run_capture(
        &["kill", if force { "-9" } else { "-15" }, &pid.to_string()],
        5,
    );
}

#[cfg(windows)]
fn kill_pid_compat(pid: u32, _force: bool) {
    let _ = crate::core::procwin::kill_pid(pid as i32);
}

/// Generic pattern-based pid enumeration (Python `_common.pids_by_pattern`; falls back to scanning `/proc` when pgrep is unavailable).
/// The pattern family here is the cleanup scope for `start_bridge`: `aimail-bridge` **and** with `--config` or `.toml`.
pub fn bridge_cleanup_pids() -> Vec<u32> {
    #[cfg(windows)]
    {
        // Windows has no cmdline matching (cannot get reliable command line from process table) ⇒ enumerate by dedicated process name
        // (aimail-bridge is a dedicated name, no name collision; unix's path first token criterion has no equivalent here).
        return crate::core::procwin::bridge_pids_by_name();
    }
    #[cfg(unix)]
    {
        let matches = |cmd: &str| {
            cmd.contains("aimail-bridge") && (cmd.contains("--config") || cmd.contains(".toml"))
        };
        let mut pids: Vec<u32> = Vec::new();
        if let Some((_, out, _)) = run_capture(
            &[
                "pgrep",
                "-f",
                r"aimail-bridge.*--config|aimail-bridge.*\.toml",
            ],
            5,
        ) {
            pids = out
                .lines()
                .filter_map(|l| l.trim().parse::<u32>().ok())
                .collect();
        } else if let Ok(entries) = std::fs::read_dir("/proc") {
            for e in entries.flatten() {
                let pid: u32 = match e.file_name().to_string_lossy().parse() {
                    Ok(p) => p,
                    Err(_) => continue,
                };
                let raw = match std::fs::read(e.path().join("cmdline")) {
                    Ok(b) => b,
                    Err(_) => continue,
                };
                let cmd = String::from_utf8_lossy(&raw).replace('\u{0}', " ");
                if matches(&cmd) {
                    pids.push(pid);
                }
            }
        }
        pids.sort_unstable();
        pids.dedup();
        pids
    }
}

/// `start_bridge`: **single instance** start. Returns whether it is already running.
pub fn start_bridge(bin_path: &str, cfg_path: &str, pid_path: &str) -> bool {
    let ctl = ctl_supported(bin_path);
    if ctl {
        let st = bridge_status(bin_path, pid_path);
        if st.get("running").and_then(|v| v.as_bool()).unwrap_or(false) {
            let pid = st.get("pid").cloned().unwrap_or(serde_json::Value::Null);
            crate::cmd::report::ok(&format!(
                "Detected running bridge (pid={}) → graceful stop",
                pid
            ));
            let rc = bridge_stop(bin_path, pid_path);
            if rc != 0 {
                warn(&format!(
                    "Bridge stop failed (rc={}) → abort this start, please run `aimail-bridge --stop` to investigate",
                    rc
                ));
                return false;
            }
        }
    } else {
        warn("Bridge binary does not support lifecycle contract (--status/--stop) → fallback to old cleanup logic; recommend upgrading bridge to >= 0.7.4");
        if let Some(old) = read_pid_file(pid_path) {
            kill_pid_compat(old as u32, false);
            std::thread::sleep(std::time::Duration::from_secs(1));
            if pid_alive(old) {
                kill_pid_compat(old as u32, true);
            }
        }
    }

    // pgrep fallback: kill by PID one by one (does not rely on pattern matching "missed kill" assumption)
    for pid in bridge_cleanup_pids() {
        kill_pid_compat(pid, false);
    }
    std::thread::sleep(std::time::Duration::from_secs(1));
    for pid in bridge_cleanup_pids() {
        kill_pid_compat(pid, true);
    }
    if std::path::Path::new(pid_path).exists() {
        let _ = std::fs::remove_file(pid_path);
    }

    if ctl {
        // Bridge self-daemon: --daemon is forked and detached by the bridge itself and writes pid (cross-platform correct approach)
        let _ = run_capture(
            &[bin_path, "-c", cfg_path, "--daemon", "--pid-file", pid_path],
            30,
        );
        for _ in 0..20 {
            std::thread::sleep(std::time::Duration::from_millis(500));
            if bridge_status(bin_path, pid_path)
                .get("running")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                return true;
            }
        }
        warn("Bridge not ready within 10s after start (check with `aimail-bridge --status`)");
        return false;
    }

    // Old bridge: starts detached from the session; if still alive after 1.5s, write pid
    let devnull = std::fs::OpenOptions::new().write(true).open("/dev/null");
    let mut cmd = std::process::Command::new(bin_path);
    cmd.arg("-c").arg(cfg_path);
    if let Ok(f) = devnull {
        if let Ok(f2) = f.try_clone() {
            cmd.stdout(std::process::Stdio::from(f))
                .stderr(std::process::Stdio::from(f2));
        }
    }
    match cmd.spawn() {
        Ok(mut child) => {
            std::thread::sleep(std::time::Duration::from_millis(1500));
            if child.try_wait().map(|s| s.is_none()).unwrap_or(false) {
                let _ = std::fs::write(pid_path, child.id().to_string());
                true
            } else {
                false
            }
        }
        Err(e) => {
            warn(&format!("Bridge startup failed: {e}"));
            false
        }
    }
}

/// Run subprocess and get (rc, stdout, stderr); timeout/startup failure ⇒ None.
fn run_capture(argv: &[&str], timeout_secs: u64) -> Option<(i32, String, String)> {
    use std::sync::mpsc;
    let argv: Vec<String> = argv.iter().map(|s| s.to_string()).collect();
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let out = std::process::Command::new(&argv[0])
            .args(&argv[1..])
            .output();
        let _ = tx.send(out);
    });
    match rx.recv_timeout(std::time::Duration::from_secs(timeout_secs)) {
        Ok(Ok(o)) => Some((
            o.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&o.stdout).to_string(),
            String::from_utf8_lossy(&o.stderr).to_string(),
        )),
        _ => None,
    }
}

/// Public probe entry for `bridge_pids` (shared implementation for `cmd_bridge` status view and tests to avoid duplication).
pub fn bridge_pids_for_probe() -> Vec<u32> {
    crate::core::bridge_wire::bridge_pids()
}

// ── Announcement address chain (U2 tail)────────────────────────────────────────────────────────
//
// Read from bridge repo (aimail-bridge/src/{config.rs,admin.rs}): `hostname` is the bridge's **externally announced** top-level key;
// `POST /api/v1/routes` uses it to return `http(s)://{hostname or bind}<push path>`. If `hostname` is not set,
// Time bridge takes `bind` (listen directive, default 0.0.0.0:38080) as target ⇒ registered but nobody can deliver (U2).
// Therefore: write-time validation — anything registered must be a **deliverable absolute http(s) URL**, never silently write if undeliverable.

/// Environment variable for the announcement address read by the bridge itself (config.rs:394).
pub const ANNOUNCE_ENV: &str = "AIMAIL_BRIDGE_HOSTNAME";
/// Path appended when bridge self-reports push entry (admin.rs:143-148) — **mirror**, not defined in this repo.
#[allow(dead_code)]
pub const BRIDGE_ANNOUNCE_PATH: &str = "/webhooks/aimail-inbound"; // contract-allowed: mirrored from aimail-bridge/src/admin.rs:143-148 (the bridge's own hardcoded self-report path), not defined here.
/// Unspecified address = listen directive; delivery there will never arrive.
pub const WILDCARD_HOSTS: [&str; 4] = ["0.0.0.0", "::", "0:0:0:0:0:0:0:0", "[::]"];
/// The bridge's own default listen port.
pub const DEFAULT_BIND_PORT: u64 = 38080;

/// `_split_host_port`: splits `host:port` / `[v6]:port` / `[v6]` / `host`.
pub fn split_host_port(value: &str) -> (String, String) {
    let s = value.trim();
    if s.is_empty() {
        return (String::new(), String::new());
    }
    if let Some(rest) = s.strip_prefix('[') {
        let (host, tail) = match rest.split_once(']') {
            Some((h, t)) => (h, t),
            None => (rest, ""),
        };
        let tail = tail.trim_start_matches(':');
        return (host.to_string(), tail.to_string());
    }
    if s.contains(':') {
        if let Some((host, port)) = s.rsplit_once(':') {
            if host.contains(':') {
                return (s.to_string(), String::new()); // Bare IPv6 (no brackets)
            }
            return (host.to_string(), port.to_string());
        }
    }
    (s.to_string(), String::new())
}

/// `_valid_port`: only valid if 0 < n < 65536.
pub fn valid_port(value: &str) -> u64 {
    value
        .trim()
        .parse::<u64>()
        .ok()
        .filter(|n| *n > 0 && *n < 65536)
        .unwrap_or(0)
}

/// `normalize_announced`: `(host:port, note)` — value written to bridge `hostname`.
pub fn normalize_announced(value: &str, default_port: u64) -> (String, String) {
    let raw = value.trim();
    if raw.is_empty() {
        return (String::new(), "no address announced".into());
    }
    let lower = raw.to_lowercase();
    let (host, port, scheme) = if lower.starts_with("http://") || lower.starts_with("https://") {
        let u = match crate::core::bridge_wire::parse_abs_url(raw) {
            Ok(u) => u,
            Err(e) => {
                return (
                    String::new(),
                    format!("'{}' is not a usable URL ({})", raw, e),
                )
            }
        };
        let path_ok = u.path.is_empty() || u.path == "/";
        if !path_ok || !u.query.is_empty() || !u.fragment.is_empty() {
            return (
                String::new(),
                format!(
                    "'{}' carries a path/query — the bridge's `hostname` is an address, not a URL",
                    raw
                ),
            );
        }
        if u.netloc.contains('@') {
            return (
                String::new(),
                format!("'{}' carries userinfo — not an address", raw),
            );
        }
        let (h, p) = split_host_port(&u.netloc);
        (h, p, u.scheme.clone())
    } else {
        if raw.contains('/') || raw.contains('?') || raw.contains('#') {
            return (
                String::new(),
                format!(
                    "'{}' carries a path — the bridge's `hostname` is an address, not a URL",
                    raw
                ),
            );
        }
        let (h, p) = split_host_port(raw);
        (h, p, String::new())
    };
    if host.is_empty() {
        return (String::new(), format!("'{}' carries no host", raw));
    }
    let hl = host.to_lowercase();
    if WILDCARD_HOSTS.contains(&hl.as_str()) || host == "::" {
        return (
            String::new(),
            format!(
                "'{}' is an unspecified/bind address — inbound mail POSTed to it never arrives (U2); announce the public address instead",
                host
            ),
        );
    }
    let port = if port.is_empty() {
        default_port.to_string()
    } else if valid_port(&port) == 0 {
        return (
            String::new(),
            format!("'{}' carries an invalid port '{}'", raw, port),
        );
    } else {
        port
    };
    let shown = if host.contains(':') {
        format!("[{}]", host)
    } else {
        host
    };
    let note = if scheme == "https" {
        " (announced over https — the bridge still self-reports http://, see admin.rs:143-148)"
            .to_string()
    } else {
        String::new()
    };
    (format!("{}:{}", shown, port), note)
}

/// `_announce_input` (explicit input: only `--bridge-hostname` argument and two environment variables).
pub fn announce_input(arg_value: &str) -> String {
    if !arg_value.is_empty() {
        return arg_value.to_string();
    }
    let env1 = std::env::var(ANNOUNCE_ENV).unwrap_or_default();
    if !env1.is_empty() {
        return env1;
    }
    String::new()
}

/// `announced_url`: URL announced/registered for this environment's bridge push entry.
pub fn announced_url(hostport: &str) -> String {
    format!("http://{}{}", hostport, BRIDGE_ANNOUNCE_PATH)
}

/// `judge_deliverable`: **(loud judgment)** whether the cloud can actually POST to this URL.
pub fn judge_deliverable(url: &str) -> (bool, String) {
    let raw = url.trim();
    if raw.is_empty() {
        return (
            false,
            "the URL is empty (pull mode: nothing is registered)".into(),
        );
    }
    let u = match crate::core::bridge_wire::parse_abs_url(raw) {
        Ok(u) => u,
        Err(e) => return (false, format!("'{}' is not a usable URL ({})", raw, e)),
    };
    let scheme = u.scheme.to_lowercase();
    if scheme != "http" && scheme != "https" {
        return (
            false,
            format!(
                "'{}' is not an absolute http(s) URL — a bare host or host:port cannot be POSTed to (reqwest: builder error)",
                raw
            ),
        );
    }
    if u.netloc.contains('@') {
        return (false, format!("'{}' carries userinfo", raw));
    }
    let host = u.hostname.trim().to_string();
    if host.is_empty() {
        return (false, format!("'{}' carries no host", raw));
    }
    if WILDCARD_HOSTS.contains(&host.to_lowercase().as_str()) {
        return (
            false,
            format!(
                "'{}' points at {} — a bind/unspecified address, not a destination (U2: nothing can deliver there)",
                raw, host
            ),
        );
    }
    if let Some(p) = u.port {
        if valid_port(&p.to_string()) == 0 {
            return (false, format!("'{}' carries an invalid port", raw));
        }
    }
    (true, String::new())
}

/// `bind_address`: value the bridge **can actually bind** to — IP announcement binds to that address, domain announcement binds to 0.0.0.0+port.
pub fn bind_address(hostport: &str) -> (String, String) {
    let (host, port) = split_host_port(hostport);
    let is_v4 = host.parse::<std::net::Ipv4Addr>().is_ok();
    if is_v4 {
        return (format!("{}:{}", host, port), String::new());
    }
    let is_v6 = host.parse::<std::net::Ipv6Addr>().is_ok();
    if is_v6 {
        return (format!("[{}]:{}", host, port), String::new());
    }
    let p: u64 = port.parse().unwrap_or(DEFAULT_BIND_PORT);
    let p = if p == 0 { DEFAULT_BIND_PORT } else { p };
    (
        format!("0.0.0.0:{}", p),
        format!(
            "announced host '{}' is a domain (not an IP) — the bridge cannot bind to it, so it listens on 0.0.0.0:{} and announces the domain",
            host, p
        ),
    )
}

/// `detect_ip`: local global address (IPv4 → IPv6 → hostname resolution → 127.0.0.1).
pub fn detect_ip() -> String {
    #[cfg(windows)]
    {
        // ipconfig takes the first global IPv4 ⇒ hostname resolution ⇒ 127.0.0.1 (unix ip//etc/hostname chain mirror)
        if let Some(ip) = crate::core::procwin::local_ipv4().into_iter().next() {
            return ip.to_string();
        }
        #[cfg(unix)]
        {
            if let Some(h) = crate::core::procwin::hostname() {
                use std::net::ToSocketAddrs;
                if let Ok(mut it) = (h.as_str(), 0u16).to_socket_addrs() {
                    if let Some(a) = it.next() {
                        return a.ip().to_string();
                    }
                }
            }
            return "127.0.0.1".to_string();
        }
    }
    if let Ok(out) = std::process::Command::new("ip")
        .args(["-4", "addr", "show", "scope", "global"])
        .output()
    {
        let text = String::from_utf8_lossy(&out.stdout).to_string();
        if let Some(ip) = text.split("inet ").nth(1).and_then(|r| r.split('/').next()) {
            let ip = ip.trim();
            if !ip.is_empty() && ip != "127.0.0.1" {
                return ip.to_string();
            }
        }
    }
    if let Ok(out) = std::process::Command::new("ip")
        .args(["-6", "addr", "show", "scope", "global"])
        .output()
    {
        let text = String::from_utf8_lossy(&out.stdout).to_string();
        if let Some(ip) = text
            .split("inet6 ")
            .nth(1)
            .and_then(|r| r.split('/').next())
        {
            let ip = ip.trim();
            if !ip.is_empty() && ip != "::1" && !ip.starts_with("fe80") {
                return ip.to_string();
            }
        }
    }
    let host = std::fs::read_to_string("/etc/hostname")
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if !host.is_empty() {
        use std::net::ToSocketAddrs;
        if let Ok(mut it) = (host.as_str(), 0u16).to_socket_addrs() {
            if let Some(a) = it.next() {
                return a.ip().to_string();
            }
        }
    }
    "127.0.0.1".to_string()
}

/// `format_webhook_host`: IP → with default port (IPv6 adds square brackets).
pub fn format_webhook_host(ip: &str) -> String {
    if ip.contains(':') && !ip.contains('.') {
        format!("[{}]:38081", ip)
    } else {
        format!("{}:38081", ip)
    }
}

// ── Deployment orchestration (`deploy_bridge.main()` deploy branch)──────────────────────────────

pub struct DeploySpec {
    pub gateway_url: String,
    pub admin_key: String,
    pub system_id: String,
    pub domain: String,
    /// S2 (bridge pull state, `webhookplan::WebhookPlan::BridgePull`) ⇒ pull (cloud fetches, bridge does not need hostname)
    pub webhook_mode_bridge: bool,
    /// `--bridge-hostname` argument value (explicit input takes priority)
    pub announce_arg: String,
    /// `webhook_host` already persisted in `systems/<sid>/aimail_gateway.json` (declared fallback source)
    pub cfg_webhook_host: String,
}

/// Deploy local bridge (binary in place → announcement check → config merge → start bridge). Returns rc (0 ok / 1 fail).
pub fn deploy(spec: &DeploySpec) -> i32 {
    use crate::cmd::report::{ok, warn};

    if spec.gateway_url.is_empty()
        || spec.admin_key.is_empty()
        || spec.system_id.is_empty()
        || spec.domain.is_empty()
    {
        warn("Required vars missing: GATEWAY_URL, ADMIN_KEY, SYSTEM_ID, AIMAIL_DOMAIN");
        return 1;
    }
    let bridge_dir = crate::core::home::aimail_home().join("bridge/bin");
    let bridge_bin = bridge_dir.join("aimail-bridge");
    let _ = std::fs::create_dir_all(&bridge_dir);
    if !ensure_binary(&bridge_bin, &bridge_dir) {
        warn("bridge binary unavailable");
        return 1;
    }

    let bridge_mode = if spec.webhook_mode_bridge {
        "pull"
    } else {
        "push"
    };

    // ── Announcement address: **before writing anything** check deliverability (undeliverable = email black hole U2 ⇒ fail loudly)
    let mut announced = String::new();
    let mut bind = "127.0.0.1:38081".to_string();
    let mut registration_url = String::new();
    if bridge_mode == "push" {
        let inputs = [
            (
                format!("--bridge-hostname / {}", ANNOUNCE_ENV),
                announce_input(&spec.announce_arg),
            ),
            (
                "systems/<sid>/aimail_gateway.json:webhook_host".to_string(),
                spec.cfg_webhook_host.clone(),
            ),
        ];
        let mut chosen: Option<(String, String)> = None;
        for (src, val) in inputs {
            if val.is_empty() {
                continue;
            }
            let (norm, note) = normalize_announced(&val, 38081);
            if norm.is_empty() {
                println!("  ✘ announced address unusable ({}): {}", src, note);
                println!(
                    "    a bridge registered from an address like that can never be POSTed to — inbound mail would vanish. Announce the public address, e.g. `--bridge-hostname 203.0.113.9:38081` or {} =bridge.example.com:38081",
                    ANNOUNCE_ENV
                );
                return 1;
            }
            chosen = Some((norm, format!("{}{}", src, note)));
            break;
        }
        let (norm, src) = match chosen {
            Some(v) => v,
            None => {
                let (norm, note) = normalize_announced(&format_webhook_host(&detect_ip()), 38081);
                if norm.is_empty() {
                    println!("  ✘ no usable announced address: {}", note);
                    println!(
                        "    pass --bridge-hostname <host:port> (or {}) — a push deployment must announce where inbound mail can arrive",
                        ANNOUNCE_ENV
                    );
                    return 1;
                }
                (
                    norm,
                    "auto-detected from this machine's global address".to_string(),
                )
            }
        };
        announced = norm;
        let announce_src = src;
        registration_url = announced_url(&announced);
        let (deliverable, why) = judge_deliverable(&registration_url);
        if !deliverable {
            println!(
                "  ✘ the URL this deploy would register is not deliverable: {}",
                why
            );
            println!("    (announced {} via {})", announced, announce_src);
            return 1;
        }
        let (host_only, _) = split_host_port(&announced);
        if ["127.0.0.1", "localhost", "::1"].contains(&host_only.as_str()) {
            warn(&format!(
                "announced address {} is loopback — only a callback from this very host can reach it",
                announced
            ));
        }
        let (b, note) = bind_address(&announced);
        if !note.is_empty() {
            warn(&note);
        }
        bind = b;
        ok(&format!(
            "Announced address: {} (from {})",
            announced, announce_src
        ));
        ok(&format!(
            "Registration URL: {} (deliverable: absolute http(s))",
            registration_url
        ));
    }

    let cfg_dir = crate::core::home::aimail_home().join("bridge");
    let _ = std::fs::create_dir_all(&cfg_dir);
    let bridge_cfg = cfg_dir.join("aimail_bridge.toml");

    // Key used by bridge: prefer system-level raw key, fallback to admin key; **idempotent reuse** of persisted bridge key
    // (otherwise every install mints a new key — orphan keys flood the gateway DB, decided 2026-09-04)
    let system_key = crate::core::config::read_system_raw_key(&spec.system_id);
    let bridge_ak = if !system_key.is_empty() {
        system_key
    } else {
        spec.admin_key.clone()
    };
    let mut bridge_key = String::new();
    if bridge_cfg.exists() {
        if let Ok(text) = std::fs::read_to_string(&bridge_cfg) {
            if let Ok(old) = text.parse::<toml::Value>() {
                if let Some(arr) = old
                    .get("pull")
                    .and_then(|p| p.get("systems"))
                    .and_then(|v| v.as_array())
                {
                    for sys in arr {
                        let sid_ok = sys.get("system_id").and_then(|v| v.as_str())
                            == Some(spec.system_id.as_str());
                        let key = sys.get("api_key").and_then(|v| v.as_str()).unwrap_or("");
                        if sid_ok && !key.is_empty() {
                            bridge_key = key.to_string();
                            ok("reuse existing bridge api_key (idempotent install)");
                            break;
                        }
                    }
                }
            }
        }
    }
    if bridge_key.is_empty() {
        let bridge_domain = format!("bridge-{}", short_hex(8));
        let created = crate::core::gateway::create_api_key(
            &spec.gateway_url,
            &bridge_ak,
            &spec.system_id,
            &bridge_domain,
            &["bridge".to_string()],
            "bridge",
        );
        bridge_key = created
            .get("raw_key")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if bridge_key.is_empty() {
            warn("bridge API key creation failed — aborting deploy (auth would fail)");
            return 1;
        }
    }

    // webhook secret: single source = host hermes config.yaml webhook.extra.secret
    let mut webhook_secret = String::new();
    let hc = crate::core::home::user_home().join(".hermes/config.yaml");
    if let Ok(text) = std::fs::read_to_string(&hc) {
        if let Ok(docs) = yaml_rust2::YamlLoader::load_from_str(&text) {
            if let Some(doc) = docs.into_iter().next() {
                webhook_secret = doc["platforms"]["webhook"]["extra"]["secret"]
                    .as_str()
                    .unwrap_or("")
                    .to_string();
            }
        }
    }

    let cfg_spec = BridgeConfigSpec {
        path: bridge_cfg.clone(),
        mode: bridge_mode.to_string(),
        addr: bind.clone(),
        gateway_url: spec.gateway_url.clone(),
        admin_key: spec.admin_key.clone(),
        system_id: spec.system_id.clone(),
        api_key: bridge_key.clone(),
        webhook_secret,
        hostname: announced.clone(),
    };
    if let Err(e) = write_bridge_config(&cfg_spec) {
        warn(&format!("bridge config write failed: {e}"));
        return 1;
    }

    // Write back `webhook_host` (only written by CLI, SDK read-only): value must be a **deliverable absolute URL**;
    // pull = Some("") (empty string, S2); push = Some(url) (S1); do not write if value unchanged (idempotent).
    if !spec.system_id.is_empty() {
        let sub = crate::core::config::gateway_config_path(&spec.system_id);
        if sub.is_file() {
            if let Some(mut cfg) = crate::core::config::load_gateway_config(&spec.system_id) {
                let target = Some(registration_url.clone());
                if cfg.webhook_host != target {
                    cfg.webhook_host = target;
                    let _ = crate::core::config::save_gateway_config_in(
                        &crate::core::home::aimail_home(),
                        &spec.system_id,
                        &cfg,
                    );
                    ok(&format!(
                        "webhook_host = {} ({})",
                        if registration_url.is_empty() {
                            "(empty = pull)"
                        } else {
                            &registration_url
                        },
                        sub.display()
                    ));
                }
            }
        }
    }

    // Start bridge
    let pid_path = cfg_dir.join("bridge.pid");
    if start_bridge(
        &bridge_bin.to_string_lossy(),
        &bridge_cfg.to_string_lossy(),
        &pid_path.to_string_lossy(),
    ) {
        ok(&format!(
            "bridge started (mode={}, {})",
            bridge_mode,
            if registration_url.is_empty() {
                "no announcement (pull)".to_string()
            } else {
                registration_url.clone()
            }
        ));
        if !bridge_key.is_empty() {
            ok("bridge API key created (category=bridge)");
        }
        0
    } else {
        warn("bridge failed to start — check ~/.aimail/bridge/aimail-bridge.log");
        1
    }
}

fn short_hex(n: usize) -> String {
    let seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
        ^ (std::process::id() as u128);
    let hex = format!("{:032x}", seed);
    hex.chars().take(n).collect()
}

/// `_bridge_upgrade`: upgrade binary using latest zip from this repo's `bridge/` (official retrieval channel).
///
/// Fetch latest zip → extract to temp dir → sha256 compare → no-op if same; if different: if running, first **contract** `--stop`
/// graceful stop (old bridge without contract ⇒ fallback to pid TERM→10s→KILL→5s) → atomic replace → `start_bridge` restart → verify.
pub fn upgrade_bridge() -> i32 {
    use crate::cmd::report::{fail, ok, warn};

    let cfg = crate::core::bridge_wire::bridge_cfg_file();
    if !cfg.exists() {
        return fail(&format!(
            "bridge configuration does not exist: {}(not deployed?)",
            cfg.display()
        ));
    }
    let bin_path = crate::core::home::aimail_home().join("bridge/bin/aimail-bridge");
    let repo_bridge = crate::core::home::program_root().join("bridge");
    let arch = arch_name();
    let zip_path = latest_bridge_zip(&repo_bridge, arch);
    if zip_path.is_empty() {
        return fail(&format!(
            "No {} platform zip in this repo: {}(complete the release process first: Release assets→zip→this directory)",
            arch,
            repo_bridge.display()
        ));
    }
    let Some(td) = temp_dir() else {
        return fail("Unpack failed: unable to create temporary directory");
    };
    let new_bin = extract_bridge(&zip_path, &td);
    let Some(new_bin) = new_bin else {
        let _ = std::fs::remove_dir_all(&td);
        return fail(&format!("zip contains no aimail-bridge: {}", zip_path));
    };
    let new_sha = sha256_file(&new_bin).unwrap_or_default();
    let old_sha = if bin_path.is_file() {
        sha256_file(&bin_path).unwrap_or_default()
    } else {
        String::new()
    };
    println!(
        "  Source: {}",
        std::path::Path::new(&zip_path)
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
    );
    println!(
        "  Current sha={}  New sha={}",
        if old_sha.is_empty() {
            "(none)".to_string()
        } else {
            old_sha.chars().take(12).collect()
        },
        new_sha.chars().take(12).collect::<String>()
    );
    if !old_sha.is_empty() && old_sha == new_sha {
        ok("Already up to date (sha identical) —— no upgrade needed");
        let _ = std::fs::remove_dir_all(&td);
        return 0;
    }

    let pids = bridge_cleanup_pids();
    if !pids.is_empty() {
        let mut stopped = false;
        if bin_path.is_file() {
            if let Some((rc, out, _)) = run_capture(&[&bin_path.to_string_lossy(), "--stop"], 30) {
                print!("{}", out);
                stopped = rc == 0;
            }
        }
        if !stopped {
            warn("Contract stop unavailable → falling back to kill by pid (old bridge < 0.7.4)");
            for p in &pids {
                kill_pid_compat(*p, false);
            }
            // The bridge handles SIGTERM **gracefully**, and the pid file is deleted by the bridge itself on normal exit ⇒ allow a 10s window before KILL
            for _ in 0..40 {
                if bridge_cleanup_pids().is_empty() {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(250));
            }
            for p in bridge_cleanup_pids() {
                kill_pid_compat(p, true);
            }
            for _ in 0..20 {
                if bridge_cleanup_pids().is_empty() {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(250));
            }
        }
        if !bridge_cleanup_pids().is_empty() {
            let _ = std::fs::remove_dir_all(&td);
            return fail(
                "Bridge still running, aborting upgrade (to avoid replacing into dual instances)",
            );
        }
    }

    if let Some(parent) = bin_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // Atomic replacement: the running old inode is unaffected, no half-written binary left behind
    if std::fs::rename(&new_bin, &bin_path).is_err() {
        let _ = std::fs::remove_dir_all(&td);
        return fail("Failed to replace binary");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&bin_path, std::fs::Permissions::from_mode(0o755));
    }
    let _ = std::fs::remove_dir_all(&td);
    ok(&format!(
        "Binary updated (sha {} → {})",
        if old_sha.is_empty() {
            "none".to_string()
        } else {
            old_sha.chars().take(12).collect()
        },
        new_sha.chars().take(12).collect::<String>()
    ));

    let pid_path = crate::core::bridge_wire::bridge_pid_file();
    if start_bridge(
        &bin_path.to_string_lossy(),
        &cfg.to_string_lossy(),
        &pid_path.to_string_lossy(),
    ) {
        let pid = std::fs::read_to_string(&pid_path)
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|_| "?".to_string());
        ok(&format!("bridge started with new binary (pid={})", pid));
        0
    } else {
        fail(&format!(
            "New binary failed to start——check log {}",
            crate::core::bridge_wire::bridge_log_file().display()
        ))
    }
}

/// `_sha256`: file sha256 in hexadecimal.
pub fn sha256_file(p: &std::path::Path) -> Option<String> {
    use sha2::{Digest, Sha256};
    let bytes = std::fs::read(p).ok()?;
    let mut h = Sha256::new();
    h.update(&bytes);
    Some(format!("{:x}", h.finalize()))
}
