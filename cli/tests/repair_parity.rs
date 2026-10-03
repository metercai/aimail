//! repair 引擎（S5a 框架）的跨语言验收：
//! 1. **REPAIRABILITY 表一致性守门** —— 与 `cli/repair.py` 的真值逐条比（表漂移即红）；
//! 2. **dry-run 计划逐字等价** —— 同一夹具上，Rust 的表头 + 计划行与 Python
//!    `repair.py --dry-run` 的输出逐字相同（含 `--deep` 追加的 drain 步）。

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;

use aimail::core::repair::{dry_run_plan_lines, header_line, ladder, repairability, Repairability};
use common::{repo_root, TempDir};
use serde_json::Value;

/// 向 Python 要 `REPAIRABILITY` 真值（`{level}/{check: {kind, step?, why?}}`）。
fn python_reparability() -> Option<Value> {
    let cli_dir = repo_root().join("cli");
    let code = "import json,sys; sys.path.insert(0,sys.argv[1]); import repair; \
                print(json.dumps({f'{l}/{c}': v for (l, c), v in repair.REPAIRABILITY.items()}))";
    let out = Command::new("python3")
        .arg("-c")
        .arg(code)
        .arg(&cli_dir)
        .output()
        .ok()?;
    if !out.status.success() {
        println!(
            "SKIP: python 探针失败：{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return None;
    }
    serde_json::from_slice(&out.stdout).ok()
}

#[test]
fn reparability_table_matches_python() {
    let Some(py) = python_reparability() else {
        println!("SKIP: python3 或 cli/repair.py 不可用 —— 未做表比对");
        return;
    };
    let py_map = py.as_object().expect("REPAIRABILITY 应是映射");
    assert!(
        py_map.len() >= 30,
        "Python 侧表太小（{} 条），夹具或探针有问题",
        py_map.len()
    );
    for ((level, name), kind) in aimail::core::repair::REPAIRABILITY {
        let key = format!("{level}/{name}");
        let want = py_map
            .get(&key)
            .unwrap_or_else(|| panic!("Rust 表里有、Python 表里没有：{key}"));
        match kind {
            Repairability::Auto { step } => {
                assert_eq!(
                    want.get("kind").and_then(Value::as_str),
                    Some("auto"),
                    "{key}"
                );
                assert_eq!(
                    want.get("step").and_then(Value::as_u64),
                    Some(*step as u64),
                    "{key} 的 step 不一致"
                );
            }
            Repairability::Hint { why } => {
                assert_eq!(
                    want.get("kind").and_then(Value::as_str),
                    Some("hint"),
                    "{key}"
                );
                assert_eq!(
                    want.get("why").and_then(Value::as_str),
                    *why,
                    "{key} 的 why 文案不一致"
                );
            }
        }
    }
    assert_eq!(
        py_map.len(),
        aimail::core::repair::REPAIRABILITY.len(),
        "条数不同：rust={} python={}",
        aimail::core::repair::REPAIRABILITY.len(),
        py_map.len()
    );
    // 未登记维度：两侧都当 HINT，且带显式 why
    assert!(!repairability("no-such", "no-such").is_auto());
}

/// 夹具：一个 hermes 系统（含一个故意失败项，让 repair 走到"列失败项 + 计划"）。
fn build_fixture(tmp: &TempDir) -> (PathBuf, PathBuf, PathBuf) {
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    let hermes = user_home.join(".hermes");
    let systems = aimail_home.join("systems");
    std::fs::create_dir_all(hermes.join("hermes-agent")).unwrap();
    std::fs::create_dir_all(hermes.join("profiles")).unwrap();
    std::fs::create_dir_all(&user_home).unwrap();
    std::fs::create_dir_all(systems.join("s1")).unwrap();
    std::fs::write(
        systems.join("s1").join(aimail::core::config::GATEWAY_CONFIG_NAME),
        format!(
            r#"{{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1","system_name":"s1","domain":"example.test","system_home":"{}"}}"#,
            hermes.display()
        ),
    )
    .unwrap();
    (aimail_home, user_home, hermes)
}

fn python_repair_dry_run(
    aimail_home: &Path,
    user_home: &Path,
    hermes: &Path,
    deep: bool,
) -> Option<Vec<String>> {
    let script = repo_root().join("cli").join("repair.py");
    if !script.is_file() {
        return None;
    }
    let mut cmd = Command::new("python3");
    cmd.arg(&script)
        .args(["--dry-run", "--system-id", "s1"])
        .arg("--home")
        .arg(hermes);
    if deep {
        cmd.arg("--deep");
    }
    cmd.env("HOME", user_home)
        .env("AIMAIL_HOME", aimail_home)
        .env("AGENT_HOME", hermes);
    let out = cmd.output().ok()?;
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    // 只取"表头 + 计划"两段（失败项告警行由 check 引擎决定，已在 check 的 parity 里覆盖）
    let mut lines: Vec<String> = Vec::new();
    for line in stdout.lines() {
        if line.starts_with("  repair system=") || line.trim_start().starts_with("- ") {
            lines.push(line.to_string());
        }
    }
    Some(lines)
}

#[test]
fn dry_run_plan_matches_python() {
    for deep in [false, true] {
        let tmp = TempDir::new(if deep { "repair-deep" } else { "repair" });
        let (aimail_home, user_home, hermes) = build_fixture(&tmp);
        let Some(py_lines) = python_repair_dry_run(&aimail_home, &user_home, &hermes, deep) else {
            println!("SKIP: python3 或 cli/repair.py 不可用");
            return;
        };
        let mut rust_lines = vec![header_line("s1", deep, true)];
        rust_lines.extend(dry_run_plan_lines(deep));
        assert_eq!(rust_lines, py_lines, "dry-run（deep={deep}）输出不一致");
        assert_eq!(
            ladder(deep).len(),
            dry_run_plan_lines(deep).len(),
            "计划行数应与阶梯一致"
        );
    }
}
