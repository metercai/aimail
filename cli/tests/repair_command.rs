//! `repair` 命令面验收（只读面：`-n/--dry-run` 与 help），**不动真盘**（repair 是写面，真跑留给 CLI L2）。
//!
//! 判据：①help 四开关与 Python 逐字（`-s/-H/-D/-n`）；②`-n` ⇒ 打印计划后 rc=0（不执行任何写步骤）；
//! ③`repair` 已被识别为**已实现**（不再落 `not yet ported`）。

use std::path::PathBuf;
use std::process::Command;

fn exe() -> PathBuf {
    PathBuf::from(env!("CARGO_BIN_EXE_aimail"))
}

fn run(home: &std::path::Path, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(exe())
        .args(args)
        .env("AIMAIL_HOME", home)
        .env("AGENT_HOME", home)
        .env("AIMAIL_PROG_DIR", home)
        .output()
        .expect("spawn");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).into_owned(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

#[test]
fn repair_dry_run_prints_plan_and_help_matches_python() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("systems")).unwrap();

    // ① help：四开关
    let (rc, out, _) = run(&home, &["repair", "--help"]);
    assert_eq!(rc, 0, "help rc: {out}");
    for flag in ["--system-id", "--home", "--deep", "--dry-run"] {
        assert!(out.contains(flag), "缺少开关 {flag}: {out}");
    }

    // ② `-n`：打印计划、rc=0、**不落 not yet ported**
    let (rc, out, err) = run(&home, &["repair", "-n"]);
    assert!(
        !out.contains("not yet ported") && !err.contains("not yet ported"),
        "repair 仍被当作未移植: {out} / {err}"
    );
    assert_eq!(
        rc, 0,
        "dry-run 应 rc=0（不执行写步骤）: out={out} err={err}"
    );
    assert!(out.contains("[dry-run] plan:"), "dry-run 未打印计划: {out}");
    // 计划里必须出现阶梯步骤（用稳定标识，避免与文案耦合）
    for step in ["bridge", "route", "webhook"] {
        assert!(out.to_lowercase().contains(step), "计划缺少 {step}: {out}");
    }
}
