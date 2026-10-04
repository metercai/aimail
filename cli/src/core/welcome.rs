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

// ── 引擎余下：常量 · 归属 · 轮询回复 · 草案解析 · 两封信的正文 ──────────────────

/// 主题标记（SDK 用它识别"这是 welcome"）。
pub const WELCOME_SUBJECT_MARKER: &str = "welcome to aimail world";
/// 回复必须齐的三个标签（缺段 ⇒ 上层明确报错，不猜不编）。
pub const WELCOME_REPLY_LABELS: [&str; 3] = ["persona", "signature", "current_time"];

/// 三层收口：扫 `{home}/systems/*/{cleaned}/<绑定文件>` 得归属 sid（失败空串）。
pub fn sid_for(home: &std::path::Path, cleaned: &str) -> String {
    let root = home.join("systems");
    let mut names: Vec<_> = std::fs::read_dir(&root)
        .map(|it| {
            it.flatten()
                .map(|e| e.file_name().to_string_lossy().to_string())
                .collect()
        })
        .unwrap_or_default();
    names.sort();
    for name in names {
        let aj = root
            .join(&name)
            .join(cleaned)
            .join(crate::core::contract::binding_file());
        if aj.is_file() {
            return name;
        }
    }
    String::new()
}

/// `_agent_log_path`：`{home}/systems/{sid}/{addr}/agentmail.log`。
///
/// Python 会优先调 SDK 的 `aimail_base.aimail_log_path()`，失败回退本地拼；两侧的 home 解析
/// （`aimail_home()`）与三元组完全同构 ⇒ 本地拼即等价（sid 由三层收口扫出，找不到用 `_unassigned`）。
pub fn agent_log_path_for(agent_email: &str) -> std::path::PathBuf {
    let cleaned = crate::core::bridge_wire::addr_clean(agent_email);
    let home = crate::core::home::aimail_home();
    let sid = sid_for(&home, &cleaned);
    home.join("systems")
        .join(if sid.is_empty() {
            "_unassigned".to_string()
        } else {
            sid
        })
        .join(cleaned)
        .join("agentmail.log")
}

/// `_poll_reply`：轮询 agent 侧 agentmail.log，直到 welcome 之后出现新 outbound 记录。
/// 返回 `(ok, email_id, to)`。
pub fn poll_reply(agent_email: &str, timeout_secs: i64) -> (bool, String, String) {
    let log_path = agent_log_path_for(agent_email);
    let baseline = std::fs::read_to_string(&log_path)
        .map(|t| t.lines().count())
        .unwrap_or(0);
    println!(
        "  Polling reply from log: {} (baseline {} lines)",
        log_path.display(),
        baseline
    );
    let start = std::time::Instant::now();
    while (start.elapsed().as_secs() as i64) < timeout_secs {
        std::thread::sleep(std::time::Duration::from_secs(5));
        let Ok(text) = std::fs::read_to_string(&log_path) else {
            continue;
        };
        for ln in text.lines().skip(baseline) {
            let Ok(e) = serde_json::from_str::<serde_json::Value>(ln) else {
                continue;
            };
            if e.get("dir").and_then(|v| v.as_str()) == Some("outbound") {
                let eid = e
                    .get("email_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                println!(
                    "  ✓ Agent replied (outbound logged, email_id={})",
                    if eid.is_empty() { "?" } else { &eid }
                );
                let to = e
                    .get("to")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                return (true, eid, to);
            }
        }
    }
    println!(
        "  ⚠ Timeout — no outbound reply in log within {}s",
        timeout_secs
    );
    (false, String::new(), String::new())
}

/// `_parse_draft_from_reply`：从 agent 最近两个月的 outbound 快照里解析 persona/signature 草案。
/// 双条件（主题含标记 **或** 三标签齐）任一命中即视为草案回复；三标签齐且 persona/signature 非空才可用。
pub fn parse_draft_from_reply(agent_email: &str) -> serde_json::Value {
    let cleaned = crate::core::bridge_wire::addr_clean(agent_email);
    let home = crate::core::home::aimail_home();
    let sid = sid_for(&home, &cleaned);
    let base = home
        .join("systems")
        .join(if sid.is_empty() {
            "_unassigned".to_string()
        } else {
            sid
        })
        .join(&cleaned)
        .join("mail");
    if !base.is_dir() {
        return serde_json::json!({});
    }
    let mut month_dirs: Vec<_> = std::fs::read_dir(&base)
        .map(|it| it.flatten().map(|e| e.path()).collect())
        .unwrap_or_default();
    month_dirs.retain(|p| {
        p.file_name()
            .map(|n| {
                let n = n.to_string_lossy();
                n.len() == 6 && n.chars().all(|c| c.is_ascii_digit())
            })
            .unwrap_or(false)
    });
    month_dirs.sort();
    month_dirs.reverse();
    month_dirs.truncate(2);

    let mut cands: Vec<(std::time::SystemTime, std::path::PathBuf)> = Vec::new();
    for d in month_dirs {
        let Ok(it) = std::fs::read_dir(&d) else {
            continue;
        };
        for e in it.flatten() {
            let p = e.path();
            let name = p
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_string();
            if name.starts_with("out-") && name.ends_with(".json") {
                if let Ok(md) = p.metadata() {
                    if let Ok(m) = md.modified() {
                        cands.push((m, p));
                    }
                }
            }
        }
    }
    cands.sort_by_key(|c| std::cmp::Reverse(c.0));
    for (_, path) in cands {
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(snap) = serde_json::from_str::<serde_json::Value>(&text) else {
            continue;
        };
        if snap.get("direction").and_then(|v| v.as_str()) != Some("outbound") {
            continue;
        }
        let subject = snap
            .get("subject")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_lowercase();
        let body = snap
            .get("body")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let lines: Vec<String> = body.lines().map(|l| l.trim().to_string()).collect();
        let marker = subject.contains(WELCOME_SUBJECT_MARKER);
        let mut vals: std::collections::BTreeMap<String, String> =
            std::collections::BTreeMap::new();
        for key in WELCOME_REPLY_LABELS {
            for ln in &lines {
                if ln.to_lowercase().starts_with(&format!("{}:", key)) {
                    vals.insert(key.to_string(), ln[key.len() + 1..].trim().to_string());
                    break;
                }
            }
        }
        let labels_ok = vals.contains_key("persona")
            && vals.contains_key("signature")
            && vals.contains_key("current_time");
        if !(marker || labels_ok) {
            continue;
        }
        let per = vals.get("persona").cloned().unwrap_or_default();
        let sig = vals.get("signature").cloned().unwrap_or_default();
        if labels_ok && !per.is_empty() && !sig.is_empty() {
            return serde_json::json!({
                "persona": per,
                "signature": sig,
                "current_time": vals.get("current_time").cloned().unwrap_or_default(),
                "source": path.to_string_lossy(),
            });
        }
    }
    serde_json::json!({})
}

/// `_approve_identity` 用的审批信 body（正文首行 `approve persona` 是网关触发词）。
pub fn approve_message(manager: &str, recipient: &str, persona: &str, signature: &str) -> String {
    rfc5322_message(
        manager,
        recipient,
        "approve persona",
        &format!(
            "approve persona\npersona: {}\nsignature: {}\n",
            persona, signature
        ),
        "approve",
    )
}

/// SMTP 模式的 welcome 信（主题/正文与 canonical 系统 welcome 同形：主题含 SDK 识别标记、
/// 正文含三标签指令块——缺任一，agent 就收不到"回三标签"的指令，第 3 段永远取不到草案）。
pub fn welcome_message(manager: &str, recipient: &str) -> String {
    let agent_name = recipient
        .split('@')
        .next()
        .filter(|s| !s.is_empty())
        .unwrap_or("Agent")
        .to_string();
    let (date, stamp) = utc_date_and_stamp();
    let subject = format!("Welcome to AIMail World, {}, since {}!", agent_name, date);
    // 正文用**原样多行串**（转义会漂移：曾因 `\n` 转义被写成字面反斜杠）——占位符替换，避免 format! 转义。
    let template = r#"Welcome to the AIMail world!

Your AIMail address has been activated. This is the first welcome email
automatically sent by the system, to confirm that your address is now active.

To verify that the full delivery path works end to end -- and to have your
outbound identity approved -- please **reply-all** to this email with the
following three lines, exactly as labelled:

  persona: <one to three sentences introducing who you are and what you do>
  signature: <your outbound email signature>
  current_time: <the current time when you reply, in a human-readable form,
                 e.g. 2026-09-28 12:05 UTC>

Your manager will review and apply the approved persona and signature to your
account. Nothing else changes.

Best regards,
__MANAGER__
Sent __STAMP__
"#;
    let body = template
        .replace("__MANAGER__", manager)
        .replace("__STAMP__", &stamp);
    rfc5322_message(manager, recipient, &subject, &body, "welcome")
}

/// UTC 日期（YYYY-MM-DD）与时间戳（YYYY-MM-DD HH:MM UTC）。
fn utc_date_and_stamp() -> (String, String) {
    let secs = now_secs();
    let (y, mo, d, hh, mi, _) = crate::core::ping::epoch_to_utc_parts(secs);
    (
        format!("{:04}-{:02}-{:02}", y, mo, d),
        format!("{:04}-{:02}-{:02} {:02}:{:02} UTC", y, mo, d, hh, mi),
    )
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
