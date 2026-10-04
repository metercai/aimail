//! `cli/send_welcome.py` 的**引擎件**（P3c 切片 1/2）：报文构造、两条传输。
//!
//! ⚠ `rfc5322_message` 是本命令发给网关的**唯一**报文构造点（welcome 信与审批信共用）——
//! 不许再另写一套头（2026-09-28 第 12 缺陷就是这个形状）。
//!
//! 为什么必须是**完整报文**（实测契约，不是风格问题；网关侧 `core/smtp/receiver.rs:729-741`）：
//! · 网关在 DATA end 用 MIME `To:`/`Cc:` 头与 RCPT 信封集合**取交集**，交集空 ⇒
//!   `550 No recipients match between envelope and email headers` ⇒ 裸正文**从不摄入**；
//! · 头齐但**缺空行**会被收下（250），但正文行被 MIME 当**头**解析 ⇒ `record.body` 为空 ⇒
//!   manager 指令不被消费（`webhook.rs:686-697`）。
//! ⇒ 判"通道是否修好"必须同时看**状态码 + 下游副作用**（三态实测：裸正文 550 / 缺空行 250 不落库 /
//!   头+空行 250 且落库）。传输是逐字节的 ⇒ `_smtp_send` 的 `subject` 形参实际**未被使用**，
//!   主题真源就是本函数写出的 `Subject:` 头。

use serde_json::{json, Value};

/// 完整 RFC-5322 报文（头 + 空行 + 正文）。
pub fn rfc5322_message(
    sender: &str,
    recipient: &str,
    subject: &str,
    body: &str,
    msg_id_prefix: &str,
) -> String {
    let msg_id = format!("<{}-{}-{}@aimail>", msg_id_prefix, now_secs(), short_hex(4));
    format!(
        "From: {}\nTo: {}\nMessage-ID: {}\nSubject: {}\n\n{}",
        sender, recipient, msg_id, subject, body
    )
}

/// `_smtp_send`：EHLO 名 `aimail-welcome`；返回 DATA end 响应（`250` 前缀即成功）。
pub fn smtp_send(
    gateway_url: &str,
    api_key: &str,
    agent_email: &str,
    manager: &str,
    edition: &str,
    raw_message: &str,
) -> String {
    let mail_from = crate::core::smtp::auth_from(api_key, manager, edition);
    crate::core::smtp::send_raw(&crate::core::smtp::SmtpJob {
        url_for_host: gateway_url,
        port: 25,
        ehlo_name: "aimail-welcome",
        mail_from: &mail_from,
        rcpt: agent_email,
        raw_message,
        ensure_trailing_newline: true, // 既有差异：不以 \n 结尾时补一行
        timeout_secs: 15,
    })
}

/// `_api_send`：`POST /api/v1/system/welcome`（唯一参数 `to`；cc/subject/body 服务器固定）。
/// 返回 `(ok, email_id, message_id, err)`。
pub fn api_send(
    gw_url: &str,
    admin_key: &str,
    recipient: &str,
    identity: &str,
) -> (bool, String, String, String) {
    let path = "/api/v1/system/welcome";
    let client = crate::core::gateway::GatewayClient::new(gw_url, admin_key, identity, 30);
    let r = client.post(path, &json!({ "to": [recipient] }));
    let status = r.get("status").and_then(|v| v.as_i64()).unwrap_or(0);
    if (200..300).contains(&status) {
        let s = |k: &str| r.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
        (true, s("email_id"), s("message_id"), String::new())
    } else {
        let detail = r
            .get("error")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .or_else(|| {
                r.get("body")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or_default();
        (
            false,
            String::new(),
            String::new(),
            format!("HTTP {}: {}", status, detail),
        )
    }
}

/// `_agent_log_path` 的目录部分：`systems/<sid>/<addr>/agentmail.log`。
pub fn agent_log_path(email: &str, sid: &str) -> std::path::PathBuf {
    crate::core::config::systems_root_in(&crate::core::home::aimail_home())
        .join(sid)
        .join(crate::core::bridge_wire::addr_clean(email))
        .join("agentmail.log")
}

fn now_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn short_hex(n: usize) -> String {
    let seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
        ^ (std::process::id() as u128);
    format!("{:032x}", seed).chars().take(n).collect()
}

/// 兜底：把 `Value` 原样给调用方（供后续编排用）。
pub fn body_of(v: &Value) -> String {
    crate::core::pyjson::str_python(Some(v))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc5322_message_has_full_headers_and_blank_line() {
        let m = rfc5322_message(
            "m@example.com",
            "a@example.test",
            "Sub ject",
            "line1\nline2",
            "welcome",
        );
        assert!(
            m.starts_with("From: m@example.com\nTo: a@example.test\nMessage-ID: <welcome-"),
            "{m}"
        );
        assert!(m.contains("\nSubject: Sub ject\n\nline1\nline2"), "{m}");
        // msg_id 形如 <welcome-<epoch>-<4hex>@aimail>
        let id_line = m.lines().find(|l| l.starts_with("Message-ID: ")).unwrap();
        assert!(id_line.ends_with("@aimail>"), "{id_line}");
        assert_eq!(m.matches("\n\n").count(), 1, "必须恰有一个空行分隔头与正文");
    }
}
