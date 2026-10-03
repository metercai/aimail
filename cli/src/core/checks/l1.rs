//! L1 探针：aimail-gateway（外部邮件网关）—— `cli/check_status.py:906-969` 的复刻。
//!
//! 四步：配置存在 → `/health` → SMTP 25 端口横幅 → 签名 `whoami` 的密钥范围判读。
//! 与 Python 一致地"早退"：health 不通就不再查 SMTP/密钥。

use crate::core::check::Check;
use crate::core::checks::l0::{field_str, read_gw_cfg, Ctx};
use crate::core::http;
use crate::core::sig;
use serde_json::Value;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python `f"{value}"` 的近似：字符串按原文，其余按 JSON 文本。
fn py_display(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// 取主机名（`gw_url.replace("https://","").replace("http://","").split("/")[0].split(":")[0]`）。
fn url_host_only(url: &str) -> String {
    url.replace("https://", "")
        .replace("http://", "")
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("")
        .to_string()
}

pub fn gateway(c: &mut Check, sid: &str, ctx: &Ctx) {
    let Some(gw) = read_gw_cfg(ctx, sid) else {
        c.add(
            "gateway",
            "config",
            false,
            &format!("{} not found", crate::core::config::GATEWAY_CONFIG_NAME),
            "Run: aimail install (激活系统)",
        );
        return;
    };

    let gw_url = field_str(&gw, "gateway_url")
        .trim_end_matches('/')
        .to_string();
    let ak = field_str(&gw, "admin_key").to_string();
    if gw_url.is_empty() {
        c.add(
            "gateway",
            "config",
            false,
            "gateway_url is empty in config",
            "Re-run: aimail install",
        );
        return;
    }

    // 1.1 Health
    let (code, body) = http::json_req(&format!("{gw_url}/health"), &[], None, None, 10);
    if code == 200 {
        let uptime = body
            .get("uptime_secs")
            .map(py_display)
            .unwrap_or_else(|| "?".to_string());
        c.add(
            "gateway",
            "health",
            true,
            &format!("HTTP {code}, uptime {uptime}s"),
            "",
        );
    } else {
        let err = if body.is_object() {
            body.get("error").cloned().unwrap_or_else(|| body.clone())
        } else {
            Value::String(body.to_string())
        };
        c.add(
            "gateway",
            "health",
            false,
            &format!("HTTP {code}: {}", py_display(&err)),
            "Start aimail-gateway service on the gateway server",
        );
        return;
    }

    // 1.2 SMTP 25 端口（连上即读横幅；超时 5s）
    let host = url_host_only(&gw_url);
    match smtp_banner(&host, 25, Duration::from_secs(5)) {
        Ok(banner) => c.add(
            "gateway",
            "smtp_port",
            true,
            &format!("Port 25 open, banner: {}", take_chars(&banner, 60)),
            "",
        ),
        Err(e) => c.add(
            "gateway",
            "smtp_port",
            false,
            &format!("Port 25 unreachable: {e}"),
            "Check firewall and aimail-gateway SMTP listener",
        ),
    }

    // 1.3 API key scope（签名 whoami）
    if ak.is_empty() {
        c.add(
            "gateway",
            "api_key",
            false,
            "No admin_key configured",
            "Run: aimail install --with-key (或 AIMAIL_ADMIN_KEY 激活)",
        );
        return;
    }
    let headers = sig::signed_headers(
        &ak,
        "GET",
        "/api/v1/whoami",
        None,
        field_str(&gw, "system_id"),
    );
    let (code, data) = http::json_req(
        &format!("{gw_url}/api/v1/whoami"),
        &headers,
        None,
        Some("GET"),
        10,
    );
    if code == 200 {
        let scope = field_str(&data, "scope").to_string();
        let category = field_str(&data, "category").to_string();
        let dsid = field_str(&data, "system_id").to_string();
        let ok = scope.contains("platform")
            || scope.contains("system")
            || scope.contains("agent_admin")
            || category == "agent_admin";
        let detail = if ok {
            format!(
                "scope={scope}, category={category}, system_id={}...",
                take_chars(&dsid, 16)
            )
        } else {
            format!("scope={scope} — need platform/system/agent_admin")
        };
        c.add(
            "gateway",
            "api_key",
            ok,
            &detail,
            "Use a key with platform, system or agent_admin scope",
        );
    } else {
        c.add(
            "gateway",
            "api_key",
            false,
            &format!("whoami HTTP {code}"),
            "Check admin_key is correct",
        );
    }
}

/// 连 SMTP 端口并读横幅（`socket.connect` + `recv(256)`）。
fn smtp_banner(host: &str, port: u16, timeout: Duration) -> std::io::Result<String> {
    let addr = (host, port)
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "no address"))?;
    let mut stream = TcpStream::connect_timeout(&addr, timeout)?;
    stream.set_read_timeout(Some(timeout))?;
    let mut buf = [0u8; 256];
    let n = stream.read(&mut buf)?;
    let _ = stream.flush();
    Ok(String::from_utf8_lossy(&buf[..n]).trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::check::Check;
    use crate::core::checks::l0::Ctx;
    use crate::core::testutil::TempDir;

    fn fixture(tag: &str) -> (TempDir, Ctx) {
        let tmp = TempDir::new(tag);
        let aimail_home = tmp.path().join("aimail");
        let user_home = tmp.path().join("home");
        std::fs::create_dir_all(aimail_home.join("systems").join("s1")).unwrap();
        std::fs::create_dir_all(&user_home).unwrap();
        std::fs::write(
            aimail_home
                .join("systems")
                .join("s1")
                .join(crate::core::config::GATEWAY_CONFIG_NAME),
            br#"{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1",
                 "system_name":"s1","domain":"example.test"}"#,
        )
        .unwrap();
        (
            tmp,
            Ctx {
                aimail_home,
                user_home,
            },
        )
    }

    #[test]
    fn missing_config_is_one_failing_record() {
        let tmp = TempDir::new("l1-missing");
        let ctx = Ctx {
            aimail_home: tmp.path().join("aimail"),
            user_home: tmp.path().join("home"),
        };
        let mut c = Check::new();
        gateway(&mut c, "s1", &ctx);
        assert_eq!(c.checks.len(), 1);
        assert_eq!(c.checks[0].check, "config");
        assert!(!c.checks[0].pass);
    }

    #[test]
    fn dead_gateway_reports_health_and_stops() {
        let (_t, ctx) = fixture("l1-dead");
        let mut c = Check::new();
        gateway(&mut c, "s1", &ctx);
        let names: Vec<&str> = c.checks.iter().map(|r| r.check.as_str()).collect();
        assert_eq!(names, vec!["health"], "health 不通应当早退：{names:?}");
        assert!(!c.checks[0].pass);
        assert!(
            c.checks[0].detail.starts_with("HTTP 0: "),
            "{:?}",
            c.checks[0]
        );
    }

    #[test]
    fn empty_gateway_url_short_circuits_before_network() {
        let (_t, ctx) = fixture("l1-empty-url");
        std::fs::write(
            ctx.aimail_home
                .join("systems")
                .join("s1")
                .join(crate::core::config::GATEWAY_CONFIG_NAME),
            br#"{"gateway_url":"","admin_key":"k","system_id":"s1"}"#,
        )
        .unwrap();
        let mut c = Check::new();
        gateway(&mut c, "s1", &ctx);
        assert_eq!(c.checks.len(), 1);
        assert_eq!(c.checks[0].detail, "gateway_url is empty in config");
    }

    #[test]
    fn url_host_only_matches_python_slicing() {
        assert_eq!(
            url_host_only("https://mail.example.test:8443/x"),
            "mail.example.test"
        );
        assert_eq!(url_host_only("http://127.0.0.1:1"), "127.0.0.1");
    }
}
