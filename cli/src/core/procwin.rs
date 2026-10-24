//! Windows process/host fact extraction (W2 runtime unix command surface windows branch, single source of truth).
//!
//! Consumption points (one-to-one correspondence with unix's pgrep//proc/ip family):
//! - Bridge pid enumeration: `bridge_deploy::bridge_cleanup_pids` / `bridge_wire::bridge_pids`
//!   (unix = `pgrep -f` → scan `/proc/<pid>/cmdline`)
//! - Live python probe: `sdkroot::probe_agent_python` (unix = scan `/proc`)
//! - Local IPv4: `netenv::global_ipv4_from_cmd` (unix = `ip` → `ifconfig`)
//! - Hostname: `gateway::is_local_gateway` / `bridge_deploy::detect_ip` (unix = `/proc`/`/etc/hostname`)
//!
//! Semantic discipline (owner 2026-10-16): if the windows branch cannot get data locally = return empty/None (loud degradation),
//! **do not pretend success**; unix original paths remain untouched (each consumption point `#[cfg(windows)]` short-circuits on the first line).
//!
//! Enumeration source: PowerShell `Get-CimInstance Win32_Process` (built-in on Win7+ systems). `Name` is readable without privilege;
//! `ExecutablePath` requires privilege on some systems ⇒ empty string if unavailable, caller degrades as "no path".

use std::net::IpAddr;
/// Full process table `(pid, name, executable_path)`; PowerShell unavailable/parse failure ⇒ empty (do not pretend success).
pub fn processes() -> Vec<(u32, String, String)> {
    let out = std::process::Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "@(Get-CimInstance Win32_Process) | Select-Object -Property ProcessId,Name,ExecutablePath | ConvertTo-Json",
        ])
        .output();
    let Ok(o) = out else {
        return Vec::new();
    };
    if !o.status.success() {
        return Vec::new();
    }
    let text = String::from_utf8_lossy(&o.stdout).trim().to_string();
    let v: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };
    let arr = match v.as_array() {
        Some(a) => a,
        None => return Vec::new(),
    };
    arr.iter()
        .filter_map(|it| {
            let pid = it.get("ProcessId")?.as_u64()? as u32;
            let name = it
                .get("Name")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            let path = it
                .get("ExecutablePath")
                .and_then(|s| s.as_str())
                .unwrap_or("")
                .to_string();
            Some((pid, name, path))
        })
        .collect()
}

/// Enumerate bridges by **process name** (`aimail-bridge.exe`, case-insensitive).
///
/// The unix criterion is "the first token of the **path** of the executable file ends with `aimail-bridge`"——in the Windows process list
/// `Name` is the bare name (no path ambiguity), `aimail-bridge` is a unique name (does not collide with other programs) ⇒ name match is exact
/// (The root cause of the 2026-08-16/09-21 accidental kill incident was unix's loose `pgrep -f` colliding with path substrings, this surface does not have that issue).
pub fn bridge_pids_by_name() -> Vec<u32> {
    let mut pids: Vec<u32> = processes()
        .into_iter()
        .filter(|(_, name, _)| name.eq_ignore_ascii_case("aimail-bridge.exe"))
        .map(|(pid, _, _)| pid)
        .collect();
    pids.sort_unstable();
    pids.dedup();
    pids
}

/// Force terminate by pid (`taskkill /F`). The two-level TERM/KILL on unix has no strict equivalent on Windows
/// (planned decision: direct /F)——the caller's "TERM→wait→KILL" wait loop still works with the original semantics of `pid_alive`.
pub fn kill_pid(pid: i32) -> bool {
    std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/F"])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Hostname (`COMPUTERNAME` standard env; unix equivalent = `/proc/sys/kernel/hostname`).
pub fn hostname() -> Option<String> {
    std::env::var("COMPUTERNAME")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Local global IPv4 (the "IPv4 Address" line from `ipconfig`; unix equivalent = `ip` → `ifconfig`).
/// ipconfig output is in the system ANSI encoding, but the prefix and IP digits are ASCII ⇒ lossy is safe.
pub fn local_ipv4() -> Vec<IpAddr> {
    let mut v = Vec::new();
    if let Ok(o) = std::process::Command::new("ipconfig").output() {
        if o.status.success() {
            for line in String::from_utf8_lossy(&o.stdout).lines() {
                let t = line.trim();
                if let Some(rest) = t.strip_prefix("IPv4 Address") {
                    if let Some(colon) = rest.find(':') {
                        let ipstr = rest[colon + 1..].trim();
                        if let Ok(ip) = ipstr.parse::<IpAddr>() {
                            let v4 = match ip {
                                IpAddr::V4(v4) => v4,
                                _ => continue,
                            };
                            if !v4.is_loopback() && !v4.is_link_local() {
                                v.push(ip);
                            }
                        }
                    }
                }
            }
        }
    }
    v
}

/// Probe the interpreter used by the agent running on this machine (Windows mirror of unix `probe_agent_python`).
///
/// Criteria is isomorphic to unix: running python family processes, and their venv actually contains `aimail` (do not mistakenly collect unrelated venvs).
/// Windows venv layout mirror: `<venv>\Scripts\python.exe` → site-packages = `<venv>\Lib\site-packages`
/// (unix = `<venv>/bin/python` → `<venv>/lib/*/site-packages`)。
/// `ExecutablePath` cannot be retrieved (some systems require privileges) ⇒ cannot locate venv ⇒ skip that process (degrade, do not guess).
pub fn probe_agent_python() -> Option<String> {
    for (_, name, path) in processes() {
        if !name.to_ascii_lowercase().starts_with("python") || path.is_empty() {
            continue;
        }
        let pb = std::path::Path::new(&path);
        if !pb.is_file() {
            continue;
        }
        // Scripts/python.exe → two levels up = venv root
        if let Some(venv) = pb.parent().and_then(|b| b.parent()) {
            let sp = venv.join("Lib").join("site-packages").join("aimail");
            if sp.is_dir() {
                return Some(path);
            }
        }
    }
    None
}
