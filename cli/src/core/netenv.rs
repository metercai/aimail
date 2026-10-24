//! network-environment facts — the **single source** of the inbound webhook decision (`webhookplan`) and of check/repair.
//!
//! 2026-10-13 (owner ruling): determination must be made against "local IP list" and "whether a given IP is public/local",
//! and both `AIMAIL_BRIDGE_HOST` / `AIMAIL_AGENT_HOST` must perform IP identification. This module centrally provides:
//! - Local IP enumeration (non-loopback global addresses);
//! - IP category (loopback / private / public);
//! - Domain name resolution (std `ToSocketAddrs`);
//! - `host:port` parsing (BRIDGE_HOST with scheme, AGENT_HOST without);
//! - AGENT_HOST validity (owner: not public and not in local list ⇒ considered non-existent; loopback rejected).
//!
//! Legacy `is_loopback_host` / `is_private_host` / `detect_lan_ip` in `repair.rs` have been removed with
//! as webhook determination converged to this module (2026-10-14) —— single source of truth, no drift.

use std::net::{IpAddr, ToSocketAddrs};

/// IP category.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Category {
    /// 127.0.0.0/8 / ::1
    Loopback,
    /// Private (10/8, 172.16/12, 192.168/16) + link-local (169.254/16, fe80::/10)
    /// + IPv6 unique local (fc00::/7)
    Private,
    /// Other routable addresses
    Public,
}

/// **Classification result** of a single TCP reachability probe (with aimail-gateway `probe-webhook`, bridge
/// `POST /api/v1/probe` return).
///
/// `error` values (consistent classification vocabulary for both endpoints):
/// - `connected`            connected (reachable=true)
/// - `connection_refused`   packet reached host, port not listening (path open, process not started)
/// - `timeout`              no route / packet dropped
/// - `unreachable`          other IO errors (including no route to host)
/// - `loopback_not_allowed` gateway-side rejection of loopback targets (GW endpoint only)
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProbeResult {
    pub reachable: bool,
    pub error: String,
}

/// Determine the category of an IP.
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
                // fc00::/7 unique local, fe80::/10 link-local
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

/// Local global IP (not loopback, not link-local).
///
/// Primary path: UDP connect to select the primary interface (no packet sent, pure routing); Fallback path: Linux `ip -4 -brief`
/// / macOS `ifconfig` to enumerate all global IPv4. Merge and deduplicate both.
pub fn local_ips() -> Vec<IpAddr> {
    let mut seen = std::collections::HashSet::new();
    let mut out: Vec<IpAddr> = Vec::new();
    // 1) Primary interface (UDP trick, cross-platform)
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
    // 2) Fallback path: all global IPv4
    for ip in global_ipv4_from_cmd() {
        if !is_loopback_ip(ip) && seen.insert(ip) {
            out.push(ip);
        }
    }
    out
}

fn global_ipv4_from_cmd() -> Vec<IpAddr> {
    #[cfg(windows)]
    {
        // Windows: `ipconfig` third branch (primary path UDP trick cross-platform already covers primary interface, this branch supplements full global IPv4)
        return crate::core::procwin::local_ipv4();
    }
    #[cfg(unix)]
    {
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
        // macOS: `ifconfig` parse inet
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
}

/// Resolve host (IP literal or domain name) → all IPs. Returns empty on resolution failure.
pub fn resolve(host: &str) -> Vec<IpAddr> {
    match (host, 0u16).to_socket_addrs() {
        Ok(addrs) => addrs.into_iter().map(|a| a.ip()).collect(),
        Err(_) => Vec::new(),
    }
}

/// Parse `host:port` (**without** scheme, AGENT_HOST format).
/// Supports `[v6]:port` and `v4:port`; pure host without port ⇒ None.
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

/// Parse `http(s)://host:port` with scheme (BRIDGE_HOST format).
/// If no scheme, treat as bare `host:port` (lenient). **Not allowed** to have path (owner decision).
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
        return None; // owner: BRIDGE_HOST has no path
    }
    parse_hostport(&s)
}

/// AGENT_HOST validity (owner decision verbatim):
/// "Value is incorrect (not an external IP or local IP list; if it is a domain name, it must resolve to an IP to determine) ⇒ treated as non-existent".
/// Loopback ⇒ invalid (loopback rejection guard). Domain name ⇒ valid if any resolved IP hits (public ∪ local non-loopback).
pub fn agent_host_valid(raw: &str) -> bool {
    let (host, _port) = match parse_hostport(raw) {
        Some(hp) => hp,
        None => return false,
    };
    // Directly an IP literal
    if let Ok(ip) = host.parse::<IpAddr>() {
        return is_public_ip(ip) || local_ips().contains(&ip);
    }
    // Domain name: any IP hits after resolution
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
        assert_eq!(parse_hostport("myhost:8080"), Some(("myhost".into(), 8080)));
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
        // No scheme is also leniently accepted
        assert_eq!(
            parse_bridge_host("203.0.113.7:38080"),
            Some(("203.0.113.7".into(), 38080))
        );
        // With path ⇒ reject (owner: BRIDGE_HOST has no path)
        assert_eq!(parse_bridge_host("http://x:80/some/path"), None);
        // Illegal scheme
        assert_eq!(parse_bridge_host("ftp://x:80"), None);
    }

    #[test]
    fn agent_host_valid_rejects_loopback() {
        // 127.0.0.1 is loopback ⇒ invalid (loopback rejection guard)
        assert!(!agent_host_valid("127.0.0.1:9099"));
        assert!(!agent_host_valid("localhost:9099"));
    }

    #[test]
    fn agent_host_valid_accepts_public_ip() {
        // Public IP literal ⇒ valid (does not depend on local list)
        assert!(agent_host_valid("203.0.113.7:9099"));
    }

    #[test]
    fn agent_host_valid_rejects_malformed() {
        assert!(!agent_host_valid(""));
        assert!(!agent_host_valid("noport"));
        assert!(!agent_host_valid("1.2.3.4:99999999")); // port overflow
    }
}
