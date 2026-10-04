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

// ── 公告地址链（U2 tail）────────────────────────────────────────────────────────
//
// 读自桥仓（aimail-bridge/src/{config.rs,admin.rs}）：`hostname` 是桥**对外宣告**的顶层键；
// `POST /api/v1/routes` 用它回 `http(s)://{hostname or bind}<push path>`。没写 `hostname`
// 时桥拿 `bind`（监听指令，默认 0.0.0.0:38080）当目标 ⇒ 注册出去没人能投递（U2）。
// 因此：写入即判定 —— 凡注册出去的必须是**可投递的绝对 http(s) URL**，不可投递绝不静默写。

/// 桥自己读取的公告地址环境变量（config.rs:394）。
pub const ANNOUNCE_ENV: &str = "AIMAIL_BRIDGE_HOSTNAME";
/// 桥自报 push 入口时追加的路径（admin.rs:143-148）——**mirror**，不是本仓定义。
#[allow(dead_code)]
pub const BRIDGE_ANNOUNCE_PATH: &str = "/webhooks/aimail-inbound"; // contract-allowed: mirrored from aimail-bridge/src/admin.rs:143-148 (the bridge's own hardcoded self-report path), not defined here.
/// 未指定地址 = 监听指令，投递到那里永远到不了。
pub const WILDCARD_HOSTS: [&str; 4] = ["0.0.0.0", "::", "0:0:0:0:0:0:0:0", "[::]"];
/// 桥自己的默认监听端口。
pub const DEFAULT_BIND_PORT: u64 = 38080;

/// `_split_host_port`：拆 `host:port` / `[v6]:port` / `[v6]` / `host`。
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
                return (s.to_string(), String::new()); // 裸 IPv6（无方括号）
            }
            return (host.to_string(), port.to_string());
        }
    }
    (s.to_string(), String::new())
}

/// `_valid_port`：0 < n < 65536 才算。
pub fn valid_port(value: &str) -> u64 {
    value
        .trim()
        .parse::<u64>()
        .ok()
        .filter(|n| *n > 0 && *n < 65536)
        .unwrap_or(0)
}

/// `normalize_announced`：`(host:port, note)` —— 写进桥 `hostname` 的值。
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

/// `_announce_input`（显式输入：仅 `--bridge-hostname` 参数与两个环境变量）。
pub fn announce_input(arg_value: &str) -> String {
    if !arg_value.is_empty() {
        return arg_value.to_string();
    }
    let env1 = std::env::var(ANNOUNCE_ENV).unwrap_or_default();
    if !env1.is_empty() {
        return env1;
    }
    std::env::var("AIMAIL_WEBHOOK_HOST").unwrap_or_default()
}

/// `announced_url`：本环境为桥的 push 入口宣告/注册的 URL。
pub fn announced_url(hostport: &str) -> String {
    format!("http://{}{}", hostport, BRIDGE_ANNOUNCE_PATH)
}

/// `judge_deliverable`：**(大声判定**）云端到底能不能 POST 到这个 URL。
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

/// `bind_address`：桥**实际可以 bind** 的值 —— IP 公告绑该地址，域名公告绑 0.0.0.0+端口。
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

/// `detect_ip`：本机全局地址（IPv4 → IPv6 → 主机名解析 → 127.0.0.1）。
pub fn detect_ip() -> String {
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

/// `format_webhook_host`：IP → 带默认端口（IPv6 加方括号）。
pub fn format_webhook_host(ip: &str) -> String {
    if ip.contains(':') && !ip.contains('.') {
        format!("[{}]:38081", ip)
    } else {
        format!("{}:38081", ip)
    }
}

// ── 部署编排（`deploy_bridge.main()` 的 deploy 分支）──────────────────────────────

pub struct DeploySpec {
    pub gateway_url: String,
    pub admin_key: String,
    pub system_id: String,
    pub domain: String,
    /// `WEBHOOK_MODE == "bridge"` ⇒ pull（云端来取，桥不需要 hostname）
    pub webhook_mode_bridge: bool,
    /// `--bridge-hostname` 参数值（显式输入优先）
    pub announce_arg: String,
    /// `systems/<sid>/aimail_gateway.json` 里已落盘的 `webhook_host`（声明回退源）
    pub cfg_webhook_host: String,
}

/// 部署本机桥（二进制就位 → 公告判定 → 配置合并 → 起桥）。返回 rc（0 ok / 1 fail）。
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
        warn("bridge 二进制不可用");
        return 1;
    }

    let bridge_mode = if spec.webhook_mode_bridge {
        "pull"
    } else {
        "push"
    };

    // ── 公告地址：**写任何东西之前**判可投递性（不可投递 = 邮件黑洞 U2 ⇒ 大声失败）
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

    // 桥用的 key：优先系统级 raw key，回退 admin key；**幂等复用**已落盘的 bridge key
    // （否则每次 install 都会 mint 一把新 key —— 网关库里 orphan key 泛滥，2026-09-04 定调）
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

    // webhook secret：env 优先，其次宿主 hermes config.yaml 的 webhook.extra.secret
    let mut webhook_secret = std::env::var("AIMAIL_WEBHOOK_SECRET").unwrap_or_default();
    if webhook_secret.is_empty() {
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

    // 回写 `webhook_host`（只由 CLI 写，SDK 只读）：值必须是**可投递的绝对 URL**；
    // pull = 显式空串；值没变就不写（幂等）。
    if !spec.system_id.is_empty() {
        let sub = crate::core::config::gateway_config_path(&spec.system_id);
        if sub.is_file() {
            if let Some(mut cfg) = crate::core::config::load_gateway_config(&spec.system_id) {
                let cur = cfg.webhook_host.clone();
                if cur != registration_url {
                    cfg.webhook_host = registration_url.clone();
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

    // 起桥
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

/// `_bridge_upgrade`：按本仓 `bridge/` 的最新 zip 升级二进制（官方取件通道）。
///
/// 取最新 zip → 解到临时目录 → sha256 比对 → 相同即 no-op；不同则：运行中先**契约** `--stop`
/// 优雅停止（旧桥无契约 ⇒ 回退按 pid TERM→10s→KILL→5s）→ 原子替换 → `start_bridge` 重启 → 校验。
pub fn upgrade_bridge() -> i32 {
    use crate::cmd::report::{fail, ok, warn};

    let cfg = crate::core::bridge_wire::bridge_cfg_file();
    if !cfg.exists() {
        return fail(&format!("bridge 配置不存在: {}(未部署?)", cfg.display()));
    }
    let bin_path = crate::core::home::aimail_home().join("bridge/bin/aimail-bridge");
    let repo_bridge = crate::core::home::program_root().join("bridge");
    let arch = arch_name();
    let zip_path = latest_bridge_zip(&repo_bridge, arch);
    if zip_path.is_empty() {
        return fail(&format!(
            "本仓无 {} 平台 zip: {}(先完成发布流程: Release 资产→zip→本目录)",
            arch,
            repo_bridge.display()
        ));
    }
    let Some(td) = temp_dir() else {
        return fail("解包失败: 无法创建临时目录");
    };
    let new_bin = extract_bridge(&zip_path, &td);
    let Some(new_bin) = new_bin else {
        let _ = std::fs::remove_dir_all(&td);
        return fail(&format!("zip 内无 aimail-bridge: {}", zip_path));
    };
    let new_sha = sha256_file(&new_bin).unwrap_or_default();
    let old_sha = if bin_path.is_file() {
        sha256_file(&bin_path).unwrap_or_default()
    } else {
        String::new()
    };
    println!(
        "  来源: {}",
        std::path::Path::new(&zip_path)
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
    );
    println!(
        "  现行 sha={}  新件 sha={}",
        if old_sha.is_empty() {
            "(无)".to_string()
        } else {
            old_sha.chars().take(12).collect()
        },
        new_sha.chars().take(12).collect::<String>()
    );
    if !old_sha.is_empty() && old_sha == new_sha {
        ok("已是最新(sha 相同) —— 无需升级");
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
            warn("契约停止不可用 → 回退按 pid 终止(旧桥 < 0.7.4)");
            for p in &pids {
                let _ = run_capture(&["kill", "-15", &p.to_string()], 5);
            }
            // 桥对 SIGTERM 是**优雅**处理，且 pid 文件正常退出时由桥自己删 ⇒ 给足 10s 窗口再 KILL
            for _ in 0..40 {
                if bridge_cleanup_pids().is_empty() {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(250));
            }
            for p in bridge_cleanup_pids() {
                let _ = run_capture(&["kill", "-9", &p.to_string()], 5);
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
            return fail("桥仍在运行, 中止升级(避免替换出双实例)");
        }
    }

    if let Some(parent) = bin_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // 原子替换：运行中的旧 inode 不受影响，不留半截二进制
    if std::fs::rename(&new_bin, &bin_path).is_err() {
        let _ = std::fs::remove_dir_all(&td);
        return fail("替换二进制失败");
    }
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(&bin_path, std::fs::Permissions::from_mode(0o755));
    let _ = std::fs::remove_dir_all(&td);
    ok(&format!(
        "二进制已更新(sha {} → {})",
        if old_sha.is_empty() {
            "无".to_string()
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
        ok(&format!("bridge 已以新二进制启动 (pid={})", pid));
        0
    } else {
        fail(&format!(
            "新二进制启动失败——查日志 {}",
            crate::core::bridge_wire::bridge_log_file().display()
        ))
    }
}

/// `_sha256`：文件 sha256 十六进制。
pub fn sha256_file(p: &std::path::Path) -> Option<String> {
    use sha2::{Digest, Sha256};
    let bytes = std::fs::read(p).ok()?;
    let mut h = Sha256::new();
    h.update(&bytes);
    Some(format!("{:x}", h.finalize()))
}
