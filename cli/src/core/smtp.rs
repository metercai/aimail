//! CLI 侧 SMTP 发送客户端（`cli/_common.smtp_cmd` + `ping_test._smtp_send_ping`）。
//!
//! 两处机制（照抄 Python，含它的历史坑）：
//! · **host 解析**：只剥 scheme 是不够的 —— 带端口的 `gateway_url`（`http://127.0.0.1:34401`）
//!   必须取**纯主机名**，否则 `connect(("127.0.0.1:34401", 25))` 直接 `gaierror`
//!   （2026-09-29 CLI L2 / J5-1 实测）。端口固定 **25**（SMTP 约定；网关 SMTP 不在 25 是已登记项）。
//! · **STARTTLS**：网关对 `auth.local` 发件人强制 TLS ⇒ 服务器通告 STARTTLS 就升级，升级后按
//!   RFC 3207 **重新 EHLO**；证书按系统根校验（网关证书由 ACME 管理）。
//!
//! TLS 用 `rustls` + `ring` + `webpki-roots`（都在 ureq 的依赖树里 ⇒ 零新增下载）。

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Arc;

/// 连接的两种形态（明文 / STARTTLS 之后），统一当作读写流用。
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

/// `urlparse(raw).hostname` 的等价物（raw 无 `//` 时补 `http://`；IPv6 去方括号）。
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

/// `_common.smtp_cmd`：发一行、读完多行响应，行间以 `" | "` 合并（行首 `NNN-` 表示续行）。
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
        // `NNN-` 续行 / `NNN ` 末行（第 4 字符非 '-' 即结束）
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

/// `detect_edition`：`GET {gw}/health` 的 version 含 `advanced-` ⇒ advanced，否则 base；失败取 default。
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

/// `_smtp_send_ping` 的 MAIL FROM 形态：advanced ⇒ `base64(hex解码(api_key))=<manager @→=>@auth.local`；
/// base ⇒ 直接用 manager（已自动加白）。
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

/// 通用发送（ping 与 welcome 同机制）：连 host:port → banner → EHLO → 可选 STARTTLS → 重新 EHLO
/// → MAIL FROM → RCPT TO → DATA → 报文（逐字节，`\n`→`\r\n`；welcome 侧另有"不以 \n 结尾补一行"的既有差异）
/// → `.` → 读 DATA end 响应。返回响应串（调用方判 `250` 前缀）。
pub struct SmtpJob<'a> {
    /// 用来取 HOST 的 URL（含端口会被正确剥掉）
    pub url_for_host: &'a str,
    pub port: u16,
    pub ehlo_name: &'a str,
    pub mail_from: &'a str,
    pub rcpt: &'a str,
    pub raw_message: &'a str,
    /// welcome 侧既有差异：报文不以 `\n` 结尾时补一行（ping 恒为 false，报文本身以 `\n` 结尾）
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

/// `_smtp_send_ping`：返回 DATA end 的响应串（`250` 开头即成功）。
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
    // ping 侧既有形态：报文以 \n 结尾 ⇒ ensure_trailing_newline 分支不触发（与 Python 同）
    send_raw(&SmtpJob {
        url_for_host: gw_url,
        port: 25,
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
    // 明文 socket 取出后套 TLS
    let sock = match &mut c {
        Conn::Plain(s) => s.try_clone().map_err(|e| format!("{}", e))?,
        Conn::Tls(_) => return Err("already tls".to_string()),
    };
    let tls = rustls::StreamOwned::new(conn, sock);
    // 握手（写一次空数据触发）
    let mut boxed = Conn::Tls(Box::new(tls));
    boxed.flush().map_err(|e| format!("{}", e))?;
    Ok(boxed)
}

/// hex 解码（`bytes.fromhex` 等价；非法即空串）。
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

/// 标准 base64（`base64.b64encode` 等价，带 `=` 填充；调用方 rstrip("=")）。
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
