//! Rust CLI 表面契约（plan v3 S1 验收，S3 起扩展）。
//!
//! 锁五件事：
//! 1. `version` 输出形态 == `aimail <ver> (<dev|bootstrapped>)`；
//! 2. 顶层 15 子命令 + `prompt` 5 嵌套全部注册（命令面冻结）；
//! 3. 机器面（`--system-only` / `--payload` / 旧名 `ensure-system`）**不出现在人面 help**；
//! 4. **未移植命令必须诚实**：未列进 `IMPLEMENTED` 的子命令一律 rc≠0 且 stderr 含
//!    "not yet ported"（绝不静默成功）；反过来，已实现的命令不许留在未移植清单里
//!    （强制维护，防止"移了代码忘了清单"）；
//! 5. 所有实跑都在**hermetic HOME** 里（临时目录），绝不读开发机的 `~/.aimail`。
//!
//! 第 5 条是 S3 加 `stats` 时暴露出来的真问题：旧版 `run()` 直接用进程环境，`stats`
//! 一实现就把开发机的真实系统打印出来（那既是测试污染源，也是误判来源）。

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

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

/// 本步已移植的子命令（每移植一个就加进来 —— 与 `unported_commands_are_honest` 互为棘轮）。
/// 注意粒度 = **子命令**：已移植命令的未移植**面**（如 `address set-name`、`domain --add`）
/// 由各自模块显式 `not_yet_ported`，不改变这里的清单。
// 已实现=命令面已接线且行为已验（`reset` 的桥路由对账仍明确告警："未移植(bridge)"，
// 但命令本体、激活 worker、注册链都是真实现 ⇒ 计入已实现面）
const IMPLEMENTED: &[&str] = &[
    "version",
    "stats",
    "persona",
    "address",
    "domain",
    "check",
    "reset",
    // install：机器面（--system-only / --payload）与人路径（激活/复用 + 平台接线）都已接线；
    // 唯二未移植的**桥相关面**（远端 bridge 部署 / 路由对账）在命令里显式告警，不静默跳过。
    "install",
    "uninstall",
    // bridge：status/按系统重刷已接线；--restart/--upgrade（部署面）在 P2 切片3 前**响亮未移植**。
    "bridge",
    // renew：只读视图 + 续期（rust 原生 HTTP）。**已登记分歧**：Python 解析失败后仍会拿空 key
    // 打默认网关（生产）⇒ 本实现打印同样文案后 rc=1 且不发网络。
    "renew",
    // ping：身份链 + SMTP（rustls STARTTLS）+ 三阶段日志判定。真机路径见 CLI L2 门禁。
    "ping",
    // welcome：API 模式（默认，admin key → /api/v1/system/welcome → 轮询回复 → 身份审批）+
    // SMTP 模式（--smtp）。出口语义 0/1/2（拿不到草案 ⇒ 2，不假装成功）。真机路径见 CLI L2。
    "welcome",
    // prompt：list/rm/create-file 已接线；add 走 SDK 落盘（绑定文件只由 SDK 写）；test 响亮未移植。
    "prompt",
    // repair：十步阶梯 + `-D` deep 扩展（引擎早已在 core::repair）+ 命令面接线。
    // 写面行为（真跑修复）由 CLI L2 覆盖；此处只验 --dry-run 与面。
    "repair",
];

static SEQ: AtomicU32 = AtomicU32::new(0);

/// 一次性临时目录（本文件专用；lib 的 testutil 是 `cfg(test)` 私有，集成测试看不到）。
struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(tag: &str) -> Self {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "aimail-rs-it-{}-{}-{}",
            std::process::id(),
            seq,
            tag
        ));
        std::fs::create_dir_all(&path).expect("temp dir");
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// 在 hermetic HOME/AIMAIL_HOME 下跑一次二进制。
fn run(args: &[&str]) -> (i32, String, String) {
    run_in(args, false)
}

/// `with_system=true` 时先造一个**合法的系统配置**（`systems/s1/aimail_gateway.json`），
/// 这样命令能通过校验、走到自己的分支（用于断言"未移植面"的诚实性）。
fn run_in(args: &[&str], with_system: bool) -> (i32, String, String) {
    let tmp = TempDir::new("surface");
    let home = tmp.path().join("home");
    let aimail_home = tmp.path().join("aimail-home");
    std::fs::create_dir_all(&home).unwrap();
    if with_system {
        let sid_dir = aimail_home.join("systems").join("s1");
        std::fs::create_dir_all(&sid_dir).unwrap();
        std::fs::write(
            sid_dir.join("aimail_gateway.json"),
            br#"{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1","system_name":"s1","save_raw_snapshots":true}"#,
        )
        .unwrap();
    } else {
        std::fs::create_dir_all(&aimail_home).unwrap();
    }
    let out = Command::new(env!("CARGO_BIN_EXE_aimail"))
        .args(args)
        .env("HOME", &home)
        .env("AIMAIL_HOME", &aimail_home)
        .env_remove("AIMAIL_PROG_DIR")
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
fn unported_commands_are_honest() {
    for name in TOP_LEVEL {
        if IMPLEMENTED.contains(name) {
            continue;
        }
        let (rc, out, err) = run(&[name]);
        assert_ne!(rc, 0, "'{name}' must not exit 0 while unported: {out:?}");
        assert!(
            err.contains("not yet ported"),
            "'{name}' stderr must say so, got {err:?}"
        );
    }
}

#[test]
fn implemented_list_does_not_claim_unported_commands() {
    // 反向棘轮：IMPLEMENTED 里的命令必须真的不再打印 stub 文案
    for name in IMPLEMENTED {
        let (_, _, err) = run(&[name]);
        assert!(
            !err.contains("not yet ported"),
            "'{name}' is listed IMPLEMENTED but still hits the stub: {err:?}"
        );
    }
}

#[test]
fn stats_on_empty_machine_is_the_single_line_and_rc0() {
    let (rc, out, err) = run(&["stats"]);
    assert_eq!(rc, 0, "rc={rc} err={err}");
    assert_eq!(
        out.trim_end(),
        "  no aimail systems configured on this machine"
    );
}

#[test]
fn persona_pointer_shell_keeps_rc2() {
    let (rc, out, err) = run(&["persona"]);
    assert_eq!(
        rc, 2,
        "persona is a pointer shell (contract rc 2), out={out:?}"
    );
    assert!(
        err.contains("welcome"),
        "stderr must point at welcome: {err:?}"
    );
}

#[test]
fn unported_faces_are_honest() {
    // 已移植**命令**上的未移植**面**：必须明确非零 + 说明，且绝不静默降级成查看面
    // （Python 侧 `cli/aimail:2045-2065` 记录过两起静默降级事故，这里用断言钉住）。
    let cases: &[&[&str]] = &[
        // `address -n`（set-name）已于 P4 切片4 接线 ⇒ 移出本清单。
        // `address -m`（set-manager）已于 P4 切片2 接线（走 SDK 按名调用）⇒ 移出本清单；
        //    校验路径验收：无 -a/-e 定位 ⇒ rc=1「该操作需要 -a <agent> 或 -e <email> 定位目标地址」。
        // `address -d/--default` 已于 P4 切片3 接线 ⇒ 移出（行为验收 tests/address_default.rs）。
        // `address --inbound-live/--inbound-down`（hidden 面）已于 P4 切片5 接线 ⇒ 本清单**已空**；
        // 验收见 tests/address_inbound_live.rs（8 组：互斥/定位/幂等两态/撤行+backlog）。
        // `domain --add` 已于 P4 切片1 真实现（创建面）⇒ 从"未移植面"清单移出，
        // 行为验收见 tests/domain_add.rs（stub 网关）。
    ];
    for args in cases {
        let (rc, out, err) = run_in(args, true);
        assert_ne!(rc, 0, "{args:?} must not exit 0 while unported: {out:?}");
        assert!(
            err.contains("not yet ported"),
            "{args:?} stderr must say so, got {err:?}"
        );
        assert!(
            !out.contains("webhook   状态") && !out.contains("Domains of system"),
            "{args:?} silently fell back to a view: {out:?}"
        );
    }
}

#[test]
fn missing_subcommand_is_rc2_like_argparse() {
    let (rc, _, _) = run(&[]);
    assert_eq!(rc, 2, "no subcommand should mirror argparse rc 2");
}
