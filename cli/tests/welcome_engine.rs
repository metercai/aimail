//! P3c 切片2 离线验收：`welcome` 引擎余下（归属 / 草案解析 / 两封信正文）。
//! 单测串行（同进程内设 `AIMAIL_HOME` 夹具，避免竞态）。

use std::path::Path;

use aimail::core::welcome::{
    agent_log_path_for, approve_message, parse_draft_from_reply, sid_for, welcome_message,
    WELCOME_SUBJECT_MARKER,
};

fn fx() -> tempfile::TempDir {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("AIMAIL_HOME", &home);
    std::env::set_var("HOME", home.join("uhome"));
    tmp
}

#[test]
fn welcome_engine_offline() {
    let tmp = fx();
    let home = tmp.path().join("ahome");
    let cleaned = "a_example.test"; // addr_clean("a@example.test")
    let leaf = home.join("systems/s1").join(cleaned);
    std::fs::create_dir_all(leaf.join("mail/202610")).unwrap();
    // 绑定文件名走契约常量（棘轮：新文件里不许出现字面量）
    std::fs::write(
        leaf.join(aimail::core::contract::binding_file()),
        r#"{"email":"a@example.test","api_key":"k"}"#,
    )
    .unwrap();

    // 1) 三层收口：能按绑定文件归属出 sid
    assert_eq!(sid_for(&home, cleaned), "s1");
    assert_eq!(sid_for(&home, "nobody"), "");
    // 2) 日志路径：sid 归属 + agentmail.log
    let lp = agent_log_path_for("a@example.test");
    assert!(
        lp.ends_with("systems/s1/a_example.test/agentmail.log"),
        "{}",
        lp.display()
    );
    assert!(agent_log_path_for("zz@elsewhere.test")
        .to_string_lossy()
        .contains("_unassigned"));

    // 3) 草案解析：三标签齐 + direction=outbound ⇒ 命中
    std::fs::write(
        leaf.join("mail/202610/out-1.json"),
        r#"{"direction":"outbound","subject":"Re: whatever","body":"persona: I operate the mail system.\nsignature: -- Agent A\ncurrent_time: 2026-10-04 09:00 UTC\n"}"#,
    )
    .unwrap();
    let got = parse_draft_from_reply("a@example.test");
    assert_eq!(got["persona"], "I operate the mail system.");
    assert_eq!(got["signature"], "-- Agent A");
    assert_eq!(got["current_time"], "2026-10-04 09:00 UTC");
    assert!(got["source"].as_str().unwrap().ends_with("out-1.json"));

    // 4a) 更新的快照"命中但缺段" ⇒ 继续翻更早的完整版（Python 语义：翻完仍缺才按缺段处理）
    std::fs::write(
        leaf.join("mail/202610/out-2.json"),
        format!(
            r#"{{"direction":"outbound","subject":"Re: {}","body":"persona: x\n"}}"#,
            WELCOME_SUBJECT_MARKER
        ),
    )
    .unwrap();
    let got2 = parse_draft_from_reply("a@example.test");
    assert_eq!(
        got2["persona"], "I operate the mail system.",
        "应继续翻到完整版: {got2}"
    );

    // 4b) 缺段且更早的也没有完整版 ⇒ 返回空（不猜不编）
    std::fs::remove_file(leaf.join("mail/202610/out-1.json")).unwrap();
    let got2b = parse_draft_from_reply("a@example.test");
    assert!(
        got2b.as_object().unwrap().is_empty(),
        "缺段翻完仍缺 ⇒ 必须返回空: {got2b}"
    );

    // 5) direction 非 outbound ⇒ 跳过（inbound 快照不算），翻完仍空
    std::fs::write(
        leaf.join("mail/202610/out-3.json"),
        r#"{"direction":"inbound","subject":"x","body":"persona: p\nsignature: s\ncurrent_time: t\n"}"#,
    )
    .unwrap();
    let got3 = parse_draft_from_reply("a@example.test");
    assert!(got3.as_object().unwrap().is_empty(), "{got3}");

    // 6) 两封信：头齐 + 恰一个空行 + 主题标记/触发词在位
    let w = welcome_message("m@example.com", "a@example.test");
    assert!(
        w.starts_with("From: m@example.com\nTo: a@example.test\nMessage-ID: <welcome-"),
        "{w}"
    );
    assert!(
        w.contains("\nSubject: Welcome to AIMail World, a, since "),
        "{w}"
    );
    assert!(w.contains("\n\nWelcome to the AIMail world!"), "{w}");
    assert!(w.contains("  persona: <"), "{w}");
    assert!(w.contains("  signature: <"), "{w}");
    assert!(w.contains("  current_time: <"), "{w}");
    assert!(w.contains("\nSent "), "{w}");
    // 头/正文分隔恰一个空行（welcome 正文内部本身有多处空行段落 ⇒ 不能整体计数）
    assert!(
        w.contains("\n\nWelcome to the AIMail world!"),
        "头后应恰接一个空行"
    );
    assert!(w.contains("active.\n\nTo verify"), "正文段落空行保留");

    let ap = approve_message("m@example.com", "a@example.test", "P", "S");
    assert!(
        ap.contains("\nSubject: approve persona\n\napprove persona\npersona: P\nsignature: S\n"),
        "{ap}"
    );
    assert!(
        ap.starts_with("From: m@example.com\nTo: a@example.test\nMessage-ID: <approve-"),
        "{ap}"
    );

    // 7) 常量
    assert_eq!(WELCOME_SUBJECT_MARKER, "welcome to aimail world");
    let _ = Path::new(".");
}
