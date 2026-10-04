//! P3b 离线验收：`ping` 的两块可离线部分 —— **SMTP 客户端**（对活桩 TCP，随机端口，不需要 root）
//! 与**三阶段事件判定引擎**（`core::ping::Watcher`）。真机路径（连网关 25 端口）留 L2 门禁。

use std::io::{Read, Write};
use std::net::TcpListener;
use std::thread;

use aimail::core::ping::{parse_ts, snapshot_line, Watcher, PING_PREFIX};
use aimail::core::smtp::{auth_from, smtp_cmd, smtp_host_from_url, Conn};

#[test]
fn smtp_host_parsing_strips_scheme_and_port() {
    // 2026-09-29 实测坑：带端口的 gateway_url 整串当 HOST ⇒ gaierror
    assert_eq!(smtp_host_from_url("http://127.0.0.1:34401"), "127.0.0.1");
    assert_eq!(smtp_host_from_url("gw.example.com:8080"), "gw.example.com");
    assert_eq!(
        smtp_host_from_url("https://gw.example.com"),
        "gw.example.com"
    );
    assert_eq!(
        smtp_host_from_url("http://[2001:db8::1]:9000"),
        "2001:db8::1"
    );
}

#[test]
fn auth_from_forms_match_python() {
    // advanced：base64(hex解码(key)) rstrip("=") + "=" + manager(@→=) + @auth.local
    // key = b"\x01\x02"、manager = m@example.com
    let got = auth_from("0102", "m@example.com", "advanced");
    // 精确值（base64(b"\x01\x02") = "AQI="，rstrip("=") ⇒ "AQI"）
    assert_eq!(got, "AQI=m=example.com@auth.local");
    // 精确值（base64(b"\x01\x02") = "AQI="，rstrip("=") ⇒ "AQI"）
    assert_eq!(auth_from("", "m@example.com", "base"), "m@example.com");
}

#[test]
fn smtp_cmd_reads_multiline_replies_until_terminator() {
    // 活桩：先回多行（250-… \n 250 …），再回单行
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let mut s = match stream {
                Ok(s) => s,
                Err(_) => continue,
            };
            let mut buf = [0u8; 1024];
            let _ = s.read(&mut buf); // EHLO
            let _ = s.write_all(b"250-gw.example.com\r\n250-STARTTLS\r\n250 8BITMIME\r\n");
            let _ = s.flush();
            let _ = s.read(&mut buf); // 第二条命令
            let _ = s.write_all(b"221 Bye\r\n");
            let _ = s.flush();
        }
    });
    let sock = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
    let mut c = Conn::Plain(sock);
    let r = smtp_cmd(&mut c, "EHLO aimail-ping-test");
    assert_eq!(r, "250-gw.example.com | 250-STARTTLS | 250 8BITMIME");
    assert!(r.to_uppercase().contains("STARTTLS"));
    let r2 = smtp_cmd(&mut c, "QUIT");
    assert_eq!(r2, "221 Bye");
}

#[test]
fn watcher_verdicts_cover_three_event_chain_and_pong_status() {
    let t0 = parse_ts("2026-10-04T00:00:00").unwrap();
    let mk = |dir: &str, ts: &str, extra: &str| {
        format!(
            r#"{{"dir":"{}","ts":"{}","id":"abc123"{} }}"#,
            dir, ts, extra
        )
    };

    // ① 三阶段齐 ⇒ 通过
    let mut w = Watcher::new("abc123", t0);
    w.observe(&format!(
        "{}\n{}\n{}\n",
        mk("ping_intercepted", "2026-10-04T00:00:01Z", ""),
        mk(
            "pong_sent",
            "2026-10-04T00:00:02Z",
            r#","pong_status":"ok""#
        ),
        mk("pong_returned", "2026-10-04T00:00:03Z", ""),
    ));
    assert!(w.done());
    let (lines, ok) = w.verdict(120, "/tmp/x.log");
    assert!(ok, "{lines:?}");
    let joined = lines.join("\n");
    assert!(joined.contains("Webhook Receive (ping)"), "{joined}");
    assert!(joined.contains("Pong Sent (send_mail)"), "{joined}");
    assert!(joined.contains("Webhook Return (pong)"), "{joined}");
    assert!(joined.contains("Total round-trip:"), "{joined}");
    assert!(joined.contains("Full pipeline verified"), "{joined}");
    assert!(joined.contains("pong_status 采样: ok"), "{joined}");

    // ② 只到 ping（pong 未回）⇒ 红
    let mut w2 = Watcher::new("abc123", t0);
    w2.observe(&mk("ping_intercepted", "2026-10-04T00:00:01Z", ""));
    let (lines2, ok2) = w2.verdict(7, "/tmp/x.log");
    assert!(!ok2);
    assert!(lines2.join("\n").contains("pong not returned within 7s"));

    // ③ 一条事件都没有 ⇒ 红（文案带日志路径与超时）
    let mut w3 = Watcher::new("abc123", t0);
    let (lines3, ok3) = w3.verdict(5, "/tmp/nope.log");
    assert!(!ok3);
    assert!(lines3
        .join("\n")
        .contains("No ping/pong events in /tmp/nope.log within 5s"));

    // ④ pong_status 采样无 ok ⇒ 事件齐也翻红（2026-09-30 前驱定因①）
    let mut w4 = Watcher::new("abc123", t0);
    w4.observe(&format!(
        "{}\n{}\n{}\n",
        mk("ping_intercepted", "2026-10-04T00:00:01Z", ""),
        mk(
            "pong_sent",
            "2026-10-04T00:00:02Z",
            r#","pong_status":"smtp-failed""#
        ),
        mk("pong_returned", "2026-10-04T00:00:03Z", ""),
    ));
    let (lines4, ok4) = w4.verdict(120, "/tmp/x.log");
    assert!(!ok4, "{lines4:?}");
    assert!(lines4.join("\n").contains("无一条 ok"), "{lines4:?}");

    // ⑤ pong_status 缺席 ⇒ 不判（仍通过）
    let mut w5 = Watcher::new("abc123", t0);
    w5.observe(&format!(
        "{}\n{}\n{}\n",
        mk("ping_intercepted", "2026-10-04T00:00:01Z", ""),
        mk("pong_sent", "2026-10-04T00:00:02Z", ""),
        mk("pong_returned", "2026-10-04T00:00:03Z", ""),
    ));
    let (lines5, ok5) = w5.verdict(120, "/tmp/x.log");
    assert!(ok5, "{lines5:?}");
    assert!(
        lines5.join("\n").contains("(pong_status 缺席, 不判)"),
        "{lines5:?}"
    );

    // 前缀常量（Subject 里带的标记）
    assert_eq!(PING_PREFIX, "__aimail_ping__:");
}

#[test]
fn snapshot_line_counts_recent_files() {
    let tmp = tempfile::tempdir().unwrap();
    let mail = tmp.path().join("mail/sub");
    std::fs::create_dir_all(&mail).unwrap();
    std::fs::write(mail.join("m1.eml"), "x").unwrap();
    let line = snapshot_line(&tmp.path().join("mail"), "s1", "a@b");
    assert!(line.contains("Snapshots: 1 new file(s)"), "{line}");
    assert!(line.contains("(total 1)"), "{line}");

    // 空目录 ⇒ 告警行（不是失败）
    let empty = tmp.path().join("empty");
    std::fs::create_dir_all(&empty).unwrap();
    let l2 = snapshot_line(&empty, "s1", "a@b");
    assert!(l2.contains("none from last 5min"), "{l2}");
}
