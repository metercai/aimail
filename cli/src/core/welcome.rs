//! **engine piece** of `cli/send_welcome.py` (P3c slice 1/2): message construction, two transports.
//!
//! ⚠ `rfc5322_message` is the **only** message construction point for this command sent to the gateway (shared by welcome and approval messages)——
//! Do not write another set of headers (2026-09-28 defect #12 was exactly this shape).
//!
//! Why it must be a **complete message** (measured contract, not a style issue; gateway side `core/smtp/receiver.rs:729-741`):
//! · at DATA end the gateway takes the **intersection** of the MIME `To:`/`Cc:` headers with the RCPT envelope set; empty intersection ⇒
//!   `550 No recipients match between envelope and email headers` ⇒ bare body is **never ingested**;
//! · headers complete but **missing blank line** are accepted (250), but the body lines get parsed as **headers** by MIME ⇒ `record.body` empty ⇒
//!   manager instructions are not consumed (`webhook.rs:686-697`).
//! ⇒ To determine "whether the channel is fixed", one must look at **status code + downstream side effects** simultaneously (three-state measurement: bare body 550 / missing blank line 250 not persisted /
//!   Headers + blank line 250 and persisted to DB). Transport is byte-by-byte ⇒ the `subject` parameter of `_smtp_send` is actually **not used**,
//!   the true source of the subject is the `Subject:` header written by this function.

use serde_json::json;

/// Complete RFC-5322 message (headers + blank line + body).
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

/// `_smtp_send`: EHLO name `aimail-welcome`; returns DATA end response (`250` prefix means success).
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
        port: crate::core::smtp::gateway_smtp_port(gateway_url),
        ehlo_name: "aimail-welcome",
        mail_from: &mail_from,
        rcpt: agent_email,
        raw_message,
        ensure_trailing_newline: true, // Existing difference: append a line if it does not end with \n
        timeout_secs: 15,
    })
}

/// `_api_send`: `POST /api/v1/system/welcome` (only parameter `to`; cc/subject/body are fixed by the server).
/// Returns `(ok, email_id, message_id, err)`.
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

// ── Remaining engine: constants · ownership · polling reply · draft parsing · body of the two letters ──────────────────

/// Subject marker (SDK uses it to identify "this is welcome").
pub const WELCOME_SUBJECT_MARKER: &str = "welcome to aimail world";
/// The three labels that must be present in the reply (missing section ⇒ upper layer reports a clear error, no guessing or fabricating).
pub const WELCOME_REPLY_LABELS: [&str; 3] = ["persona", "signature", "current_time"];

/// Three-layer convergence: scan `{home}/systems/*/{cleaned}/<binding file>` to get ownership sid (empty string on failure).
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
/// Python will preferentially call the SDK's `aimail_base.aimail_log_path()`, falling back to local concatenation on failure; the home resolution on both sides
/// (`aimail_home()`) is completely isomorphic to the triple ⇒ local concatenation is equivalent (sid is scanned by three-layer convergence, use `_unassigned` if not found).
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

/// `_poll_reply`: Poll the agent-side agentmail.log until a new outbound record appears after welcome.
/// Returns `(ok, email_id, to)`.
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

/// `_parse_draft_from_reply`: Parse persona/signature draft from the agent's outbound snapshots of the last two months.
/// Dual condition (subject contains marker **or** three labels complete) any hit is considered a draft reply; three labels complete and persona/signature non-empty to be usable.
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

/// Approval letter body used by `_approve_identity` (first line of body `approve persona` is the gateway trigger word).
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

/// SMTP mode welcome message (subject/body isomorphic to the canonical system welcome: subject contains the SDK identification marker,
/// body contains three-label instruction block——missing any, the agent will not receive the "reply with three labels" instruction, and the 3rd segment will never get the draft).
pub fn welcome_message(manager: &str, recipient: &str) -> String {
    let agent_name = recipient
        .split('@')
        .next()
        .filter(|s| !s.is_empty())
        .unwrap_or("Agent")
        .to_string();
    let (date, stamp) = utc_date_and_stamp();
    let subject = format!("Welcome to AIMail World, {}, since {}!", agent_name, date);
    // Body uses **raw multi-line string** (escaping causes drift: once `\n` escaping was written as literal backslash)——placeholder replacement, avoiding format! escaping.
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

/// UTC date (YYYY-MM-DD) and timestamp (YYYY-MM-DD HH:MM UTC).
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
        // msg_id is in the form <welcome-<epoch>-<4hex>@aimail>
        let id_line = m.lines().find(|l| l.starts_with("Message-ID: ")).unwrap();
        assert!(id_line.ends_with("@aimail>"), "{id_line}");
        assert_eq!(m.matches("\n\n").count(), 1, "Must have exactly one blank line separating headers and body");
    }
}
