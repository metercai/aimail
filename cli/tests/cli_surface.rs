//! Rust CLI 表面契约（plan v3 S1 验收）。
//!
//! 锁四件事（与 Python 侧 `tests/test_install_machine_surface.py` 同精神）：
//! 1. `version` 输出形态 == `aimail <ver> (<dev|bootstrapped>)`；
//! 2. 顶层 15 子命令 + `prompt` 5 嵌套全部注册（命令面冻结）；
//! 3. 机器面（`--system-only` / `--payload` / 旧名 `ensure-system`）**不出现在人面 help**；
//! 4. 未移植子命令 = 明确非零退出（绝不静默成功）；缺子命令 = rc 2。

use std::process::Command;

const TOP_LEVEL: &[&str] = &[
    "install",
    "uninstall",
    "reset",
    "stats",
    "renew",
    "version",
    "check",
    "repair",
    "ping",
    "welcome",
    "persona",
    "domain",
    "address",
    "prompt",
    "bridge",
];

const PROMPT_SUBCOMMANDS: &[&str] = &["add", "list", "rm", "test", "create-file"];

/// 机器面字样（人面 help 三者皆不得出现；Python 侧同名断言）。
const MACHINE_LITERALS: &[&str] = &["ensure-system", "payload", "system-only"];

fn run(args: &[&str]) -> (i32, String, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_aimail"))
        .args(args)
        .output()
        .expect("spawn aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

#[test]
fn version_line_shape_matches_python_contract() {
    let (rc, out, err) = run(&["version"]);
    assert_eq!(rc, 0, "rc={rc} stderr={err}");
    let line = out.trim_end();
    let parts: Vec<&str> = line.split(' ').collect();
    assert_eq!(parts.len(), 3, "want `aimail <ver> (<mode>)`, got {line:?}");
    assert_eq!(parts[0], "aimail", "prefix: {line:?}");
    assert_eq!(parts[1].split('.').count(), 3, "semver: {line:?}");
    assert!(
        parts[1]
            .split('.')
            .all(|p| p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty()),
        "numeric semver: {line:?}"
    );
    assert!(
        matches!(parts[2], "(dev)" | "(bootstrapped)"),
        "mode: {line:?}"
    );
}

#[test]
fn top_level_subcommands_registered() {
    let (rc, out, _) = run(&["--help"]);
    assert_eq!(rc, 0);
    for name in TOP_LEVEL {
        assert!(out.contains(name), "missing subcommand in help: {name}");
    }
}

#[test]
fn prompt_nested_subcommands_registered() {
    let (rc, out, err) = run(&["prompt", "--help"]);
    assert_eq!(rc, 0, "rc={rc} err={err}");
    for name in PROMPT_SUBCOMMANDS {
        assert!(out.contains(name), "missing prompt subcommand: {name}");
    }
}

#[test]
fn machine_surface_is_hidden_from_human_help() {
    for args in [vec!["--help"], vec!["install", "--help"]] {
        let (rc, out, err) = run(&args);
        assert_eq!(rc, 0, "rc={rc} err={err}");
        for lit in MACHINE_LITERALS {
            assert!(
                !out.contains(lit),
                "{args:?}: leaked machine literal {lit:?}"
            );
        }
    }
}

#[test]
fn unimplemented_subcommand_exits_nonzero_and_says_so() {
    let (rc, out, err) = run(&["stats"]);
    assert_ne!(rc, 0, "unported command must not exit 0 (stdout={out:?})");
    assert!(
        err.contains("not yet ported"),
        "stderr must say so, got {err:?}"
    );
}

#[test]
fn missing_subcommand_is_rc2_like_argparse() {
    let (rc, _, _) = run(&[]);
    assert_eq!(rc, 2, "no subcommand should mirror argparse rc 2");
}
