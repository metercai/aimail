//! Windows 进程/主机事实提取(W2 运行期 unix 命令面的 windows 分支, 单真源)。
//!
//! 消费点(与 unix 的 pgrep//proc/ip 族一一对应):
//! - 桥 pid 枚举: `bridge_deploy::bridge_cleanup_pids` / `bridge_wire::bridge_pids`
//!   (unix = `pgrep -f` → 扫 `/proc/<pid>/cmdline`)
//! - 活 python 探测: `sdkroot::probe_agent_python`(unix = 扫 `/proc`)
//! - 本机 IPv4: `netenv::global_ipv4_from_cmd`(unix = `ip` → `ifconfig`)
//! - 主机名: `gateway::is_local_gateway` / `bridge_deploy::detect_ip`(unix = `/proc`/`/etc/hostname`)
//!
//! 语义纪律(owner 2026-10-16):windows 分支若本机拿不到数据 = 返回空/None(响亮降级),
//! **不假装成功**;unix 原路全部不动(各消费点 `#[cfg(windows)]` 首行短路)。
//!
//! 枚举源: PowerShell `Get-CimInstance Win32_Process`(Win7+ 系统自带)。`Name` 非特权可读;
//! `ExecutablePath` 在个别系统需特权 ⇒ 取不到时空串, 调用方按"无路径"降级。

use std::net::IpAddr;
/// 全进程表 `(pid, name, executable_path)`;PowerShell 不可用/解析失败 ⇒ 空(不假装成功)。
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

/// 按**进程名**枚举桥(`aimail-bridge.exe`, 大小写不敏感)。
///
/// unix 的判据是"可执行文件**路径**首 token 以 `aimail-bridge` 结尾"——Windows 进程表里
/// `Name` 就是裸名(无路径歧义), `aimail-bridge` 是专属名(不与其他程序撞名)⇒ 名匹配即精确
/// (2026-08-16/09-21 误杀事故的根因是 unix 宽松 `pgrep -f` 撞路径子串, 此处无此面)。
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

/// 按 pid 强制终止(`taskkill /F`)。unix 的 TERM/KILL 两级在 Windows 无严格对应
/// (计划裁决: 直接 /F)——调用方的"TERM→等→KILL"等待循环仍按 `pid_alive` 原语义生效。
pub fn kill_pid(pid: i32) -> bool {
    std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/F"])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// 主机名(`COMPUTERNAME` 标准 env;unix 对应物 = `/proc/sys/kernel/hostname`)。
pub fn hostname() -> Option<String> {
    std::env::var("COMPUTERNAME")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// 本机全局 IPv4(`ipconfig` 的 "IPv4 Address" 行;unix 对应物 = `ip` → `ifconfig`)。
/// ipconfig 输出按系统 ANSI 编码, 但前缀与 IP 数字均为 ASCII ⇒ lossy 安全。
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

/// 探测本机正在运行的 agent 所用解释器(unix `probe_agent_python` 的 Windows 镜像)。
///
/// 判据与 unix 同构: 运行中的 python 族进程, 且其 venv 内确有 `aimail`(不误采无关 venv)。
/// Windows venv 布局镜像: `<venv>\Scripts\python.exe` → site-packages = `<venv>\Lib\site-packages`
/// (unix = `<venv>/bin/python` → `<venv>/lib/*/site-packages`)。
/// `ExecutablePath` 取不到(个别系统需特权)⇒ 无法定位 venv ⇒ 跳过该进程(降级, 不猜)。
pub fn probe_agent_python() -> Option<String> {
    for (_, name, path) in processes() {
        if !name.to_ascii_lowercase().starts_with("python") || path.is_empty() {
            continue;
        }
        let pb = std::path::Path::new(&path);
        if !pb.is_file() {
            continue;
        }
        // Scripts/python.exe → 上两级 = venv 根
        if let Some(venv) = pb.parent().and_then(|b| b.parent()) {
            let sp = venv.join("Lib").join("site-packages").join("aimail");
            if sp.is_dir() {
                return Some(path);
            }
        }
    }
    None
}
