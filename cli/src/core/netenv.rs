//! 网络环境事实 —— 入站 webhook 判定（`webhookplan`）与 check/repair 的**单真源**。
//!
//! 2026-10-13（owner 裁定）：判定要对着「本机 IP 列表」「某 IP 是公网/本机」来判，
//! 且 `AIMAIL_BRIDGE_HOST` / `AIMAIL_AGENT_HOST` 都要做 IP 识别。这里集中提供：
//! - 本机 IP 枚举（非回环的全局地址）；
//! - IP 类别（回环 / 私网 / 公网）；
//! - 域名解析（std `ToSocketAddrs`）；
//! - `host:port` 解析（BRIDGE_HOST 带 scheme、AGENT_HOST 不带）；
//! - AGENT_HOST 有效性（owner：不是公网且不在本机列表 ⇒ 视为不存在；回环拒绝）。
//!
//! `repair.rs` 里既有的 `is_loopback_host` / `is_private_host` / `detect_lan_ip`
//! 逐步委托到本模块 —— 单真源、不漂移。

use std::net::{IpAddr, ToSocketAddrs};

/// IP 类别。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Category {
    /// 127.0.0.0/8 / ::1
    Loopback,
    /// 私网（10/8、172.16/12、192.168/16）+ 链路本地（169.254/16、fe80::/10）
    /// + IPv6 唯一本地（fc00::/7）
    Private,
    /// 其余可路由地址
    Public,
}

/// 判定一个 IP 的类别。
pub fn classify(ip: IpAddr) -> Category {
    match ip {
        IpAddr::V4(v4) => {
            if v4.is_loopback() {
                Category::Loopback
            } else if v4.is_private() || v4.is_link_local() {
                Category::Private
            } else {
                Category::Public
            }
        }
        IpAddr::V6(v6) => {
            let head = v6.segments()[0];
            if v6.is_loopback() {
                Category::Loopback
            } else if (head & 0xfe00) == 0xfc00 || (head & 0xffc0) == 0xfe80 {
                // fc00::/7 唯一本地、fe80::/10 链路本地
                Category::Private
            } else {
                Category::Public
            }
        }
    }
}

pub fn is_loopback_ip(ip: IpAddr) -> bool {
    classify(ip) == Category::Loopback
}

pub fn is_public_ip(ip: IpAddr) -> bool {
    classify(ip) == Category::Public
}

/// 本机全局 IP（非回环、非链路本地）。
///
/// 主路：UDP connect 选路取主口（不发包，纯选路由）；补路：Linux `ip -4 -brief`
/// / macOS `ifconfig` 枚举所有全局 IPv4。两者合并去重。
pub fn local_ips() -> Vec<IpAddr> {
    let mut seen = std::collections::HashSet::new();
    let mut out: Vec<IpAddr> = Vec::new();
    // 1) 主口（UDP trick，跨平台）
    if let Ok(s) = std::net::UdpSocket::bind("0.0.0.0:0") {
        if s.connect("8.8.8.8:80").is_ok() {
            if let Ok(addr) = s.local_addr() {
                let ip = addr.ip();
                if !is_loopback_ip(ip) && seen.insert(ip) {
                    out.push(ip);
                }
            }
        }
    }
    // 2) 补路：所有全局 IPv4
    for ip in global_ipv4_from_cmd() {
        if !is_loopback_ip(ip) && seen.insert(ip) {
            out.push(ip);
        }
    }
    out
}

fn global_ipv4_from_cmd() -> Vec<IpAddr> {
    // Linux: `ip -4 -brief addr show scope global`
    if let Ok(o) = std::process::Command::new("ip")
        .args(["-4", "-brief", "addr", "show", "scope", "global"])
        .output()
    {
        if o.status.success() {
            let mut v = Vec::new();
            for line in String::from_utf8_lossy(&o.stdout).lines() {
                for p in line.split_whitespace() {
                    if p.contains('/') && p.chars().next().is_some_and(|c| c.is_ascii_digit()) {
                        if let Ok(ip) = p.split('/').next().unwrap_or("").parse::<IpAddr>() {
                            if !ip.is_ipv4() {
                                continue;
                            }
                            let v4 = match ip {
                                IpAddr::V4(v4) => v4,
                                _ => continue,
                            };
                            if v4.is_loopback() || v4.is_link_local() {
                                continue;
                            }
                            v.push(ip);
                        }
                    }
                }
            }
            if !v.is_empty() {
                return v;
            }
        }
    }
    // macOS: `ifconfig` 解析 inet
    if let Ok(o) = std::process::Command::new("ifconfig").output() {
        if o.status.success() {
            let mut v = Vec::new();
            for line in String::from_utf8_lossy(&o.stdout).lines() {
                let t = line.trim();
                if let Some(rest) = t.strip_prefix("inet ") {
                    if let Some(ipstr) = rest.split_whitespace().next() {
                        if let Ok(ip) = ipstr.parse::<IpAddr>() {
                            let v4 = match ip {
                                IpAddr::V4(v4) => v4,
                                _ => continue,
                            };
                            if v4.is_loopback() || v4.is_link_local() {
                                continue;
                            }
                            v.push(ip);
                        }
                    }
                }
            }
            return v;
        }
    }
    Vec::new()
}

/// 某 IP 是否「本机」：回环，或在全局 IP 列表里。
pub fn is_local_ip(ip: IpAddr) -> bool {
    is_loopback_ip(ip) || local_ips().contains(&ip)
}

/// 解析 host（IP 字面量或域名）→ 全部 IP。解析失败返回空。
pub fn resolve(host: &str) -> Vec<IpAddr> {
    match (host, 0u16).to_socket_addrs() {
        Ok(addrs) => addrs.into_iter().map(|a| a.ip()).collect(),
        Err(_) => Vec::new(),
    }
}

/// 解析 `host:port`（**不带** scheme，AGENT_HOST 形态）。
/// 支持 `[v6]:port` 与 `v4:port`；纯 host 无 port ⇒ None。
pub fn parse_hostport(raw: &str) -> Option<(String, u16)> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    // IPv6 [host]:port
    if let Some(rest) = s.strip_prefix('[') {
        if let Some((host, tail)) = rest.split_once("]:") {
            let port: u16 = tail.trim_start_matches(':').parse().ok()?;
            return Some((host.to_string(), port));
        }
        return None;
    }
    let (host, port) = s.rsplit_once(':')?;
    let port: u16 = port.parse().ok()?;
    if host.is_empty() {
        return None;
    }
    Some((host.to_string(), port))
}

/// 解析带 scheme 的 `http(s)://host:port`（BRIDGE_HOST 形态）。
/// 无 scheme 时按裸 `host:port` 处理（宽容）。**不允许**带 path（owner 裁定）。
pub fn parse_bridge_host(raw: &str) -> Option<(String, u16)> {
    let mut s = raw.trim().to_string();
    if let Some(after) = s.split_once("://") {
        let scheme = after.0.to_ascii_lowercase();
        if scheme != "http" && scheme != "https" {
            return None;
        }
        s = after.1.to_string();
    }
    if s.contains('/') {
        return None; // owner：BRIDGE_HOST 无 path
    }
    parse_hostport(&s)
}

/// AGENT_HOST 有效性（owner 裁定逐字）：
/// 「值不正确（不是外网 IP 或本机 IP list，如果是域名要解析到 IP 来判断）⇒ 视为不存在」。
/// 回环 ⇒ 无效（loopback 拒绝守卫）。域名 ⇒ 任一解析 IP 命中（公网 ∪ 本机非回环）即有效。
pub fn agent_host_valid(raw: &str) -> bool {
    let (host, _port) = match parse_hostport(raw) {
        Some(hp) => hp,
        None => return false,
    };
    // 直接是 IP 字面量
    if let Ok(ip) = host.parse::<IpAddr>() {
        return is_public_ip(ip) || local_ips().contains(&ip);
    }
    // 域名：解析后任一 IP 命中
    resolve(&host)
        .iter()
        .any(|ip| is_public_ip(*ip) || local_ips().contains(ip))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_ipv4() {
        assert_eq!(classify("127.0.0.1".parse().unwrap()), Category::Loopback);
        assert_eq!(classify("10.0.0.5".parse().unwrap()), Category::Private);
        assert_eq!(classify("192.168.1.10".parse().unwrap()), Category::Private);
        assert_eq!(classify("169.254.1.1".parse().unwrap()), Category::Private);
        assert_eq!(classify("8.8.8.8".parse().unwrap()), Category::Public);
    }

    #[test]
    fn classify_ipv6() {
        assert_eq!(classify("::1".parse().unwrap()), Category::Loopback);
        assert_eq!(classify("fc00::1".parse().unwrap()), Category::Private);
        assert_eq!(classify("fe80::1".parse().unwrap()), Category::Private);
    }

    #[test]
    fn parse_hostport_v4() {
        assert_eq!(
            parse_hostport("1.2.3.4:9099"),
            Some(("1.2.3.4".into(), 9099))
        );
        assert_eq!(
            parse_hostport("myhost:8080"),
            Some(("myhost".into(), 8080))
        );
        assert_eq!(parse_hostport("noport"), None);
        assert_eq!(parse_hostport(""), None);
    }

    #[test]
    fn parse_hostport_ipv6() {
        assert_eq!(
            parse_hostport("[::1]:9099"),
            Some((String::from("::1"), 9099))
        );
    }

    #[test]
    fn parse_bridge_host_with_scheme() {
        assert_eq!(
            parse_bridge_host("https://bridge.example.com:8443"),
            Some(("bridge.example.com".into(), 8443))
        );
        assert_eq!(
            parse_bridge_host("http://203.0.113.7:38080"),
            Some(("203.0.113.7".into(), 38080))
        );
        // 无 scheme 也宽容接受
        assert_eq!(
            parse_bridge_host("203.0.113.7:38080"),
            Some(("203.0.113.7".into(), 38080))
        );
        // 带 path ⇒ 拒绝（owner：BRIDGE_HOST 无 path）
        assert_eq!(parse_bridge_host("http://x:80/some/path"), None);
        // 非法 scheme
        assert_eq!(parse_bridge_host("ftp://x:80"), None);
    }

    #[test]
    fn agent_host_valid_rejects_loopback() {
        // 127.0.0.1 是回环 ⇒ 无效（loopback 拒绝守卫）
        assert!(!agent_host_valid("127.0.0.1:9099"));
        assert!(!agent_host_valid("localhost:9099"));
    }

    #[test]
    fn agent_host_valid_accepts_public_ip() {
        // 公网 IP 字面量 ⇒ 有效（不依赖本机列表）
        assert!(agent_host_valid("203.0.113.7:9099"));
    }

    #[test]
    fn agent_host_valid_rejects_malformed() {
        assert!(!agent_host_valid(""));
        assert!(!agent_host_valid("noport"));
        assert!(!agent_host_valid("1.2.3.4:99999999")); // port 溢出
    }
}
