//! `install` 机器面（S6 切片1）的闸门：分派/拒参/只读动作/未移植诚实性。
//!
//! 为什么单列：机器面（`--system-only` / `--payload`）是**SDK 反向调用 CLI 的 ABI**，
//! 判据是"文案 + 流向 + rc"三者逐字；其中拒参必须 exit 2 且**不静默忽略**（Python 侧
//! `cli/aimail:784-827` 的裁决）。这里钉住 Rust 侧行为；与 Python 的逐字对照由
//! `/tmp/install-machine-parity.sh` 式的人工复核补充（解析器级错文差异除外，见下）。

use std::path::PathBuf;
use std::process::Command;

fn bin() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("target");
    p.push(if cfg!(debug_assertions) {
        "debug"
    } else {
        "release"
    });
    p.push("aimail");
    p
}

fn run(prog_dir: &str, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(bin())
        .args(args)
        .env("AIMAIL_PROG_DIR", prog_dir)
        .env("AIMAIL_HOME", format!("{prog_dir}/home"))
        .output()
        .expect("rust binary 可执行");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn machine_channels_reject_the_other_side_and_exit_2() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    let cases: &[(&[&str], &str)] = &[
        (
            &["install", "--system-only", "--payload", "dir"],
            "ERROR: --system-only 与 --payload 互斥",
        ),
        (
            &["install", "--payload", "dir", "-s", "s1"],
            "ERROR: --payload 通道不接受 -s",
        ),
        (
            &["install", "--payload", "dir", "-H", "/tmp"],
            "ERROR: --payload 通道不接受 -H",
        ),
        (
            &["install", "--payload", "dir", "--all-agents"],
            "ERROR: --payload 通道不接受 --all-agents",
        ),
        (
            &["install", "--system-only", "--dest", "/tmp/x"],
            "ERROR: --system-only 通道不接受 --dest",
        ),
        (
            &["install", "--system-only", "mcp"],
            "ERROR: --system-only 通道不接受 payload operand",
        ),
        (
            &["install", "mcp"],
            "ERROR: payload operand 只在 --payload 通道有效",
        ),
        (
            &["install", "--force"],
            "ERROR: --force 只在 --payload 通道有效",
        ),
    ];
    for (args, msg) in cases {
        let (rc, out, err) = run(&prog, args);
        assert_eq!(rc, 2, "{args:?} 必须 exit 2（拒参不静默）");
        assert!(
            err.contains(msg),
            "{args:?} stderr 应含 {msg:?}，实到 {err:?}"
        );
        assert!(out.is_empty(), "{args:?} 拒参时 stdout 应为空");
    }
}

#[test]
fn payload_readonly_actions_print_paths_and_rc0() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    // dir：默认 mcp；显式 bundle 也要认
    let (rc, out, _) = run(&prog, &["install", "--payload", "dir"]);
    assert_eq!(rc, 0);
    assert_eq!(out.trim(), format!("{prog}/mcp"));
    let (rc, out, _) = run(&prog, &["install", "--payload", "dir", "skill-dsh"]);
    assert_eq!(rc, 0);
    assert_eq!(
        out.trim(),
        &format!(
            "~/.dsh/skills/{}",
            aimail::core::contract::agent_skill_name()
        ),
        "~ 不展开（与 Python 同）"
    );
    // resource 缺 name ⇒ stdout 报错 + rc2（Python 的流向也是 stdout）
    let (rc, out, err) = run(&prog, &["install", "--payload", "resource"]);
    assert_eq!(rc, 2);
    assert!(out.contains("ERROR: payload resource 需要 <name>"));
    assert!(err.is_empty());
    // 未知 payload 动作：clap 的选择集先拦（rc2）
    let (rc, _, _) = run(&prog, &["install", "--payload", "nope"]);
    assert_eq!(rc, 2);
}

#[test]
fn unported_install_surfaces_fail_loudly() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    {
        let args = vec!["install"];
        let (rc, out, err) = run(&prog, &args);
        assert_ne!(rc, 0, "{args:?} 未移植必须非零");
        assert!(
            err.contains("not yet ported") || out.contains("not"),
            "{args:?} 应说明未移植: out={out:?} err={err:?}"
        );
    }
    // `--system-only` 无凭据 ⇒ **ABI 错误信封**（单行 JSON，rc=1），不是"未移植"
    let (rc, out, _e) = run(&prog, &["install", "--system-only"]);
    assert_eq!(rc, 1, "--system-only 缺 home/sid 应 rc=1");
    assert!(
        out.trim().starts_with(r#"{"success": false, "error": "#),
        "应输出 Python 风格单行 JSON 信封: {out:?}"
    );
    assert_eq!(out.trim_end().lines().count(), 1, "stdout 必须恰一行");
    // 到达激活 worker ⇒ 已实现：网关不可达时按 Python 口径**继续并成功**（只 warn），
    // 但必须用 `-g` 指向死地址以免触网（默认会落到生产网关）。
    let home2 = d.path().join("h2");
    std::fs::create_dir_all(&home2).unwrap();
    let (rc2, out2, err2) = run(
        &prog,
        &[
            "install",
            "--system-only",
            "-H",
            &home2.to_string_lossy(),
            "-s",
            "s1",
            "-k",
            "k",
            "-g",
            "http://127.0.0.1:9",
        ],
    );
    assert_eq!(
        rc2, 0,
        "网关不可达 ⇒ 沿用系统级 key 并成功（Python 同）: {err2}"
    );
    assert!(
        out2.trim()
            .starts_with(r#"{"success": true, "system_id": "s1""#),
        "单行 JSON 成功信封: {out2:?}"
    );
    assert_eq!(out2.trim_end().lines().count(), 1, "恰一行");

    // `--payload install` 已实现：源根不可解析时必须**明确报错非零**（与 Python 同一失败文案），
    // 绝不静默成功。
    let (rc, out, err) = run(&prog, &["install", "--payload", "install", "mcp"]);
    assert_ne!(rc, 0, "源根不可解析必须非零");
    assert!(
        err.contains("运行时源未找到"),
        "应报源缺失: out={out:?} err={err:?}"
    );
}
