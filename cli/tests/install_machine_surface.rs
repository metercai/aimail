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
fn install_surfaces_behave_like_python() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    {
        // 人路径**已实现**：无 --home/--system-id 时按 Python 口径响亮失败（文案进 stdout，rc=1）
        let args = vec!["install"];
        let (rc, out, _err) = run(&prog, &args);
        assert_eq!(rc, 1, "{args:?} 应 rc=1");
        assert!(
            out.contains("install 需要 --home"),
            "{args:?} 应报缺失参数: out={out:?}"
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

    // `--payload install` 已实现：**两形态皆不可用**时必须**明确报错非零**（与 Python 同一失败文案），
    // 绝不静默成功。
    // 注（契约 v1.0 §3 阶段三）：解析链 = repo→pip，pip 形态**是契约允许的正当来源**；
    // 故本用例须同时掐断两条路 —— 临时把 `python_bin()` 的单真源 `AIMAIL_PYTHON` 指向
    // 无法 import aimail 的解释器（prog 为临时目录 ⇒ 无 sdk-staging-removed/pysdk）。测试串行运行，
    // 该赋值只影响本用例。
    std::env::set_var("AIMAIL_PYTHON", "/nonexistent/python");
    let (rc, out, err) = run(&prog, &["install", "--payload", "install", "mcp"]);
    std::env::remove_var("AIMAIL_PYTHON");
    assert_ne!(rc, 0, "源根不可解析必须非零");
    assert!(
        err.contains("运行时源未找到"),
        "应报源缺失: out={out:?} err={err:?}"
    );
}

/// 子进程级 env 注入(不污染测试进程,规避并行用例的进程级 env 竞态)。
fn run_env(prog_dir: &str, args: &[&str], extra: &[(&str, &str)]) -> (i32, String, String) {
    let out = Command::new(bin())
        .args(args)
        .env("AIMAIL_PROG_DIR", prog_dir)
        .env("AIMAIL_HOME", format!("{prog_dir}/home"))
        .envs(extra.iter().copied())
        .output()
        .expect("rust binary 可执行");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn install_reads_agent_home_env_when_no_flag_or_sid() {
    let d = tempfile::tempdir().unwrap();
    let prog = d.path().to_string_lossy().to_string();
    let agent_home = d.path().join("agent-home");
    std::fs::create_dir_all(&agent_home).unwrap();
    // C-7:AGENT_HOME + 无 --home/--system-id ⇒ home 由 env 解析(与 welcome 身份链对齐)。
    // 空临时目录下游 resolve_platform 无法定平台 ⇒ 响亮失败(rc=1),证明已越过 C-7 关口。
    let (rc, out, _err) = run_env(
        &prog,
        &["install"],
        &[("AGENT_HOME", agent_home.to_str().unwrap())],
    );
    assert_eq!(
        rc, 1,
        "空目录无法定平台 ⇒ rc=1(证明 home 解析已通过): {out:?}"
    );
    assert!(
        out.contains("--home 由 AGENT_HOME env 解析"),
        "应展示 AGENT_HOME home 解析来源: out={out:?}"
    );
    assert!(
        !out.contains("install 需要 --home"),
        "C-7:不再报缺 --home(home 已由 AGENT_HOME 提供): out={out:?}"
    );
}
