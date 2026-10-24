//! CLI side SMTP sending client (`cli/_common.smtp_cmd` + `ping_test._smtp_send_ping`).
//!
//! Two mechanisms (copied from Python, including its historical pitfalls):
//! · **host parsing**: stripping scheme alone is not enough —— `gateway_url` with port (`http://127.0.0.1:34401`)
//!   must take the **pure hostname**, otherwise `connect(("127.0.0.1:34401", 25))` directly `gaierror`
//!   (2026-09-29 CLI L2 / J5-1 measured). Port fixed at **25** (SMTP convention; gateway SMTP not on 25 is a registered item).
//! · **STARTTLS**: the gateway enforces TLS for `auth.local` senders ⇒ upgrade if the server advertises STARTTLS, and after upgrading
//!   RFC 3207 **re-EHLO**; certificate verified against system roots (gateway certificate managed by ACME).
//!
//! TLS uses `rustls` + `ring` + `webpki-roots` (all in ureq's dependency tree ⇒ zero new downloads).

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Arc;

/// Two forms of connection (plaintext / after STARTTLS), uniformly used as read/write streams.
pub enum Conn {
    Plain(TcpStream),
    Tls(Box<rustls::StreamOwned<rustls::ClientConnection, TcpStream>>),
}

impl Read for Conn {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        match self {
            Conn::Plain(s) => s.read(buf),
            Conn::Tls(s) => s.read(buf),
        }
    }
}
impl Write for Conn {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        match self {
            Conn::Plain(s) => s.write(buf),
            Conn::Tls(s) => s.write(buf),
        }
    }
    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            Conn::Plain(s) => s.flush(),
            Conn::Tls(s) => s.flush(),
        }
    }
}

/// Equivalent of `urlparse(raw).hostname` (prepends `http://` if raw has no `//`; strips brackets for IPv6).
pub fn smtp_host_from_url(gw_url: &str) -> String {
    let raw = if gw_url.contains("//") {
        gw_url.to_string()
    } else {
        format!("http://{}", gw_url)
    };
    match crate::core::bridge_wire::parse_abs_url(&raw) {
        Ok(u) => u.hostname,
        Err(_) => String::new(),
    }
}

/// `_common.smtp_cmd`: send one line, read multi-line response, join lines with `" | "` (line start `NNN-` indicates continuation).
pub fn smtp_cmd(c: &mut Conn, cmd: &str) -> String {
    let _ = c.write_all(format!("{}\r\n", cmd).as_bytes());
    let _ = c.flush();
    let mut all: Vec<String> = Vec::new();
    let mut buf = [0u8; 4096];
    loop {
        let n = match c.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => n,
        };
        let chunk = String::from_utf8_lossy(&buf[..n]).to_string();
        for l in chunk.split('\n') {
            let l = l.trim_end_matches('\r');
            if !l.is_empty() || !all.is_empty() {
                all.push(l.to_string());
            }
        }
        let last = all.last().cloned().unwrap_or_default();
        // `NNN-` continuation / `NNN ` final line (4th char not '-' means end)
        if last.len() < 4 || last.as_bytes()[3] != b'-' {
            break;
        }
    }
    all.iter()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .collect::<Vec<_>>()
        .join(" | ")
}

/// `detect_edition`: `GET {gw}/health` version contains `advanced-` ⇒ advanced, otherwise base; on failure use default.
pub fn detect_edition(gateway_url: &str, default: &str) -> String {
    let url = format!("{}/health", gateway_url.trim_end_matches('/'));
    match crate::core::http::raw_req(&url, None, None, 10) {
        Ok((code, body)) => {
            if (200..300).contains(&code) {
                match serde_json::from_str::<serde_json::Value>(&body) {
                    Ok(v) => {
                        let ver = v.get("version").and_then(|x| x.as_str()).unwrap_or("");
                        if ver.contains("advanced-") {
                            "advanced".to_string()
                        } else {
                            "base".to_string()
                        }
                    }
                    Err(_) => default.to_string(),
                }
            } else {
                default.to_string()
            }
        }
        Err(_) => default.to_string(),
    }
}

/// `gateway_smtp_port`: the `smtp_port` field of `GET {gw}/health` (gateway SMTP listening port,
/// parsed by the gateway from `[smtp] bind` and reported). Health check failure / old gateway without this field ⇒ fallback 25
/// (SMTP public protocol contract, always 25 in production).
pub fn gateway_smtp_port(gateway_url: &str) -> u16 {
    let url = format!("{}/health", gateway_url.trim_end_matches('/'));
    match crate::core::http::raw_req(&url, None, None, 10) {
        Ok((code, body)) if (200..300).contains(&code) => {
            match serde_json::from_str::<serde_json::Value>(&body) {
                Ok(v) => parse_smtp_port(&v),
                Err(_) => 25,
            }
        }
        _ => 25,
    }
}

/// Parsing `smtp_port` from /health body: missing / non-numeric / out of range => 25.
fn parse_smtp_port(v: &serde_json::Value) -> u16 {
    v.get("smtp_port")
        .and_then(|x| x.as_u64())
        .filter(|&p| (1..=65535).contains(&p))
        .map(|p| p as u16)
        .unwrap_or(25)
}

/// MAIL FROM format of `_smtp_send_ping`: advanced => `base64(hex_decode(api_key))=<manager @->=>@auth.local`;
/// base ⇒ use manager directly (already auto-whitelisted).
pub fn auth_from(api_key: &str, manager: &str, edition: &str) -> String {
    if edition == "advanced" {
        let key_hex_decoded = hex_decode(api_key);
        let b64_key = base64_std(&key_hex_decoded)
            .trim_end_matches('=')
            .to_string();
        let encoded_manager = manager.replace('@', "=");
        format!("{}={}@auth.local", b64_key, encoded_manager)
    } else {
        manager.to_string()
    }
}

/// Generic send (ping and welcome use the same mechanism): connect host:port → banner → EHLO → optional STARTTLS → re-EHLO
/// → MAIL FROM → RCPT TO → DATA → message (byte-by-byte, `\n`→`\r\n`; welcome side has an existing difference of "append a line if not ending with \n")
/// → `.` → read DATA end response. Returns response string (caller checks for `250` prefix).
pub struct SmtpJob<'a> {
    /// URL used to fetch HOST (port included will be correctly stripped)
    pub url_for_host: &'a str,
    pub port: u16,
    pub ehlo_name: &'a str,
    pub mail_from: &'a str,
    pub rcpt: &'a str,
    pub raw_message: &'a str,
    /// Existing difference on welcome side: append a line if message does not end with `\n` (ping is always false, message itself ends with `\n`)
    pub ensure_trailing_newline: bool,
    pub timeout_secs: u64,
}

pub fn send_raw(job: &SmtpJob) -> String {
    let (
        url_for_host,
        port,
        ehlo_name,
        mail_from,
        rcpt,
        raw_message,
        ensure_trailing_newline,
        timeout_secs,
    ) = (
        job.url_for_host,
        job.port,
        job.ehlo_name,
        job.mail_from,
        job.rcpt,
        job.raw_message,
        job.ensure_trailing_newline,
        job.timeout_secs,
    );
    let host = smtp_host_from_url(url_for_host);
    let sock = match TcpStream::connect((host.as_str(), port)) {
        Ok(s) => s,
        Err(e) => return format!("connect failed: {}", e),
    };
    let _ = sock.set_read_timeout(Some(std::time::Duration::from_secs(timeout_secs)));
    let _ = sock.set_write_timeout(Some(std::time::Duration::from_secs(timeout_secs)));
    let mut c = Conn::Plain(sock);
    let mut buf = [0u8; 4096];
    let mut banner = String::new();
    if let Ok(n) = c.read(&mut buf) {
        banner = String::from_utf8_lossy(&buf[..n]).trim().to_string();
    }
    if !banner.starts_with("220") {
        return format!("SMTP banner failed: {}", banner);
    }
    let mut resp = smtp_cmd(&mut c, &format!("EHLO {}", ehlo_name));
    if resp.to_uppercase().contains("STARTTLS") {
        resp = smtp_cmd(&mut c, "STARTTLS");
        if !resp.starts_with("220") {
            return format!("STARTTLS failed: {}", resp);
        }
        match starttls(c, &host) {
            Ok(tls) => c = tls,
            Err(e) => return format!("STARTTLS TLS handshake failed: {}", e),
        }
        let _ = smtp_cmd(&mut c, &format!("EHLO {}", ehlo_name));
    }
    let resp = smtp_cmd(&mut c, &format!("MAIL FROM:<{}>", mail_from));
    if !resp.starts_with("250") {
        return format!("MAIL FROM failed: {}", resp);
    }
    let resp = smtp_cmd(&mut c, &format!("RCPT TO:<{}>", rcpt));
    if !resp.starts_with("250") {
        return format!("RCPT TO failed: {}", resp);
    }
    let resp = smtp_cmd(&mut c, "DATA");
    if !resp.starts_with("354") {
        return format!("DATA failed: {}", resp);
    }
    let _ = c.write_all(raw_message.replace('\n', "\r\n").as_bytes());
    if ensure_trailing_newline && !raw_message.ends_with('\n') {
        let _ = c.write_all(b"\r\n");
    }
    let _ = c.write_all(b".\r\n");
    let _ = c.flush();
    let out = smtp_cmd(&mut c, "");
    let _ = c.write_all(b"QUIT\r\n");
    let _ = c.flush();
    out
}

/// `_smtp_send_ping`: returns the response string from DATA end (`250` prefix indicates success).
pub fn send_ping(
    gw_url: &str,
    api_key: &str,
    email: &str,
    manager: &str,
    ping_id: &str,
    edition: &str,
) -> String {
    let mail_from = auth_from(api_key, manager, edition);

    let body = format!(
        "From: {}\nTo: {}\nSubject: {}{}\nMessage-ID: <ping-{}@aimail.token.tm>\n\nPing test message\n",
        manager, email, crate::core::ping::PING_PREFIX, ping_id, ping_id
    );
    // Existing format on ping side: message ends with \n ⇒ ensure_trailing_newline branch is not triggered (same as Python)
    send_raw(&SmtpJob {
        url_for_host: gw_url,
        port: gateway_smtp_port(gw_url),
        ehlo_name: "aimail-ping-test",
        mail_from: &mail_from,
        rcpt: email,
        raw_message: &body,
        ensure_trailing_newline: false,
        timeout_secs: 15,
    })
}

fn starttls(mut c: Conn, host: &str) -> Result<Conn, String> {
    let mut roots = rustls::RootCertStore::empty();
    roots.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    let cfg = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_no_client_auth();
    let server_name =
        rustls::pki_types::ServerName::try_from(host.to_string()).map_err(|e| format!("{}", e))?;
    let conn =
        rustls::ClientConnection::new(Arc::new(cfg), server_name).map_err(|e| format!("{}", e))?;
    // Wrap plaintext socket with TLS after extraction
    let sock = match &mut c {
        Conn::Plain(s) => s.try_clone().map_err(|e| format!("{}", e))?,
        Conn::Tls(_) => return Err("already tls".to_string()),
    };
    let tls = rustls::StreamOwned::new(conn, sock);
    // Handshake (triggered by writing empty data once)
    let mut boxed = Conn::Tls(Box::new(tls));
    boxed.flush().map_err(|e| format!("{}", e))?;
    Ok(boxed)
}

/// hex decode (equivalent to `bytes.fromhex`; invalid input yields an empty string).
fn hex_decode(s: &str) -> Vec<u8> {
    let s = s.trim();
    if s.len() % 2 != 0 {
        return Vec::new();
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect::<Option<Vec<u8>>>()
        .unwrap_or_default()
}

/// Standard base64 (equivalent to `base64.b64encode`, with `=` padding; caller rstrip("=")).
fn base64_std(data: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in data.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            T[((n >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            T[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

#[cfg(test)]
mod gateway_smtp_port_tests {
    use super::*;

    #[test]
    fn parses_reported_port() {
        let v: serde_json::Value =
            serde_json::from_str(r#"{"status":"ok","smtp_port":12526}"#).unwrap();
        assert_eq!(parse_smtp_port(&v), 12526);
    }

    #[test]
    fn missing_field_falls_back_to_25() {
        let v: serde_json::Value =
            serde_json::from_str(r#"{"status":"ok","version":"base"}"#).unwrap();
        assert_eq!(parse_smtp_port(&v), 25);
    }

    #[test]
    fn out_of_range_falls_back_to_25() {
        let v: serde_json::Value = serde_json::from_str(r#"{"smtp_port":70000}"#).unwrap();
        assert_eq!(parse_smtp_port(&v), 25);
    }
}
