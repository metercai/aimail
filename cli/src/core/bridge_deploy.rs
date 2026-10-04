//! `cli/deploy_bridge.py` 的**二进制就位**部分（P2 切片3a）—— zip 取件 + 落盘 + 可执行位。
//!
//! 与 Python 的差异（**刻意的改进，不改变语义**）：Python 是"先试 `unzip` 可执行，失败再回退纯
//! Python `zipfile`"（2026-10-02 L2 容器实测无 unzip ⇒ 双通道正是为它）。Rust 侧直接内置 zip
//! 解压（zip crate），**连 `unzip` 都不需要** ⇒ 更贴合 owner 口径"CLI 自身零环境依赖"，
//! 且不再有"宿主少一个工具就装不了桥"的失败面。解出的文件名、`aimail-bridge` 前缀筛选、
//! 临时目录 → 原子替换 → `0755` 的语义与 Python 逐条对齐。
//!
//! 其余口径照抄：`bridge/` 取件目录在 `<program_root>/bridge`；zip 名
//! `aimail-bridge-v<semver>-linux-<arch>.zip`；**版本按语义比较**（v1.10.0 > v1.2.3 > v1.2）；
//! 已就位（可执行）则完全不动 zip（幂等）。

use std::path::{Path, PathBuf};

use crate::cmd::report::warn;

/// `_version_key`：从 `aimail-bridge-vX.Y[.Z]-…` 抽 (major, minor, patch)。
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

/// 本机架构名（`platform.machine()` 的等价映射）。
pub fn arch_name() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "amd64",
        "aarch64" | "arm64" => "arm64",
        _ => "amd64",
    }
}

/// `latest_bridge_zip`：`<bridge_dir_local>` 里最新的 `aimail-bridge-v*-linux-{arch}.zip`。
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

/// 文件是否可执行（`os.access(X_OK)` 等价）。
pub fn is_executable(p: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// `_ensure_binary`：二进制缺失时从本仓 `bridge/` 的 zip 解出；幂等。
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
        // 解到临时目录后统一改名，不依赖 zip 内部命名（照抄 Python）
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
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(bridge_bin, std::fs::Permissions::from_mode(0o755));
    }
    is_executable(bridge_bin)
}

/// 在 zip 里找**顶层**的 `aimail-bridge*` 常规文件并解到 `td`（返回落地路径）。
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
    // Python：`os.listdir(td)` ⇒ 只看**顶层**名字且需是常规文件
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
        warn(&format!("zip 内无 aimail-bridge 可执行文件: {zip_path}"));
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

// ── 配置写入（`_config_lines` / `write_bridge_config`）────────────────────────────

/// `_config_lines`：单桥多系统的 TOML 体。逐行照抄 Python（含注释与键序）。
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
            "# 单 bridge 多系统:每系统一条,独立 key/system_id/dedup/backoff",
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

/// `write_bridge_config` 的入参（单桥多系统的合并式写入）。
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

/// `write_bridge_config`：**合并**（单桥多系统）——已有 `[pull].systems` 里非本 sid 的条目保留。
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

    // 读旧配置（坏 TOML 静默忽略 —— 照抄 Python 的 `except Exception: pass`）
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
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(&spec.path, std::fs::Permissions::from_mode(0o600)); // 凭据文件（AUDIT-1 F6）
    Ok(())
}

// ── 桥生命周期（`_common.bridge_ctl_supported/status/stop` + `deploy_bridge.start_bridge`）──
//
// 契约（aimail-bridge >= 0.7.4）：`--status [--json]` / `--stop --pid-file <p>`;
// 退出码 0 成功|运行中 · 1 错误|拒绝 · 2 配置非法 · 3 未运行。
// 不支持的旧桥 ⇒ 回退"pid 文件 + 存活探测"（并告警），CLI 不再自行 kill 进程。

/// `bridge_ctl_supported`：二进制是否支持生命周期契约。
pub fn ctl_supported(bin_path: &str) -> bool {
    match run_capture(&[bin_path, "--status", "--json"], 10) {
        Some((rc, out, _)) => (rc == 0 || rc == 3) && out.contains("\"running\""),
        None => false,
    }
}

/// `bridge_status`：契约优先；旧桥回退 pid 文件 + 存活探测。
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

/// `bridge_stop`：契约优先（含身份校验，非桥拒绝）；旧桥回退 TERM→KILL。
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
    println!("    (旧桥无契约能力 → 回退 TERM/KILL; 建议升级桥到 >= 0.7.4)");
    let _ = run_capture(&["kill", "-15", &pid.to_string()], 5);
    std::thread::sleep(std::time::Duration::from_secs(1));
    if pid_alive(pid) {
        let _ = run_capture(&["kill", "-9", &pid.to_string()], 5);
    }
    0
}

/// `_read_pid_file`。
pub fn read_pid_file(pid_path: &str) -> Option<i32> {
    std::fs::read_to_string(pid_path)
        .ok()
        .and_then(|t| t.trim().parse::<i32>().ok())
}

/// `pid_alive`（unix：`kill -0`）。
pub fn pid_alive(pid: i32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// 通用按模式枚举 pid（Python `_common.pids_by_pattern`；无 pgrep 时回退扫 `/proc`）。
/// 这里的模式族是 `start_bridge` 的清理口径：`aimail-bridge` **且**带 `--config` 或 `.toml`。
pub fn bridge_cleanup_pids() -> Vec<u32> {
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

/// `start_bridge`：**单实例**启动。返回是否已运行。
pub fn start_bridge(bin_path: &str, cfg_path: &str, pid_path: &str) -> bool {
    let ctl = ctl_supported(bin_path);
    if ctl {
        let st = bridge_status(bin_path, pid_path);
        if st.get("running").and_then(|v| v.as_bool()).unwrap_or(false) {
            let pid = st.get("pid").cloned().unwrap_or(serde_json::Value::Null);
            crate::cmd::report::ok(&format!("检测到已运行桥(pid={}) → 优雅停止", pid));
            let rc = bridge_stop(bin_path, pid_path);
            if rc != 0 {
                warn(&format!(
                    "桥停止失败(rc={}) → 放弃本次启动, 请先 `aimail-bridge --stop` 排查",
                    rc
                ));
                return false;
            }
        }
    } else {
        warn("桥二进制不支持生命周期契约(--status/--stop) → 回退旧清理逻辑; 建议升级桥到 >= 0.7.4");
        if let Some(old) = read_pid_file(pid_path) {
            let _ = run_capture(&["kill", "-15", &old.to_string()], 5);
            std::thread::sleep(std::time::Duration::from_secs(1));
            if pid_alive(old) {
                let _ = run_capture(&["kill", "-9", &old.to_string()], 5);
            }
        }
    }

    // pgrep 兜底：按 PID 逐个杀（不依赖模式匹配的"漏杀"假设）
    for pid in bridge_cleanup_pids() {
        let _ = run_capture(&["kill", "-15", &pid.to_string()], 5);
    }
    std::thread::sleep(std::time::Duration::from_secs(1));
    for pid in bridge_cleanup_pids() {
        let _ = run_capture(&["kill", "-9", &pid.to_string()], 5);
    }
    if std::path::Path::new(pid_path).exists() {
        let _ = std::fs::remove_file(pid_path);
    }

    if ctl {
        // 桥自守护：--daemon 由桥自己 fork 脱离并写 pid（跨平台的正确做法）
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
        warn("桥启动后 10s 内未就绪(`aimail-bridge --status` 查)");
        return false;
    }

    // 旧桥：脱离会话直接起，1.5s 后仍活则写 pid
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
            warn(&format!("桥启动失败: {e}"));
            false
        }
    }
}

/// 跑子进程并取 (rc, stdout, stderr)；超时/启动失败 ⇒ None。
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

/// `bridge_pids` 的公开探测入口（`cmd_bridge` 的状态视图与测试共用同一实现，避免复刻）。
pub fn bridge_pids_for_probe() -> Vec<u32> {
    crate::core::bridge_wire::bridge_pids()
}
