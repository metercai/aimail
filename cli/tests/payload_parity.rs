//! 载荷状态（`runtime/mcp-payload`）的跨语言验收 + **表一致性守门**。
//!
//! 两件事：
//! 1. `MCP_FILES` 是 `cli/runtime_bundle.py` 的 `BUNDLES["mcp"]["files"]` 的 Rust 副本 ——
//!    这类"两边各写一份表"最容易漂移，所以这里直接向 Python 要真值做比对
//!    （表一变而 Rust 没跟，测试立刻红）；
//! 2. 同一夹具上 Rust 的 `runtime/mcp-payload` 记录与 Python 逐字比（fix 提示路径做窄归一，
//!    理由同 L2r：Python 给源码检出路径，Rust 给部署形态路径）。

mod common;

use aimail::core::check::Check;
use aimail::core::payload::{check_payload, MCP_FILES, MCP_SUBDIR, STAMP_NAME};
use common::{filter_records, python_check_json_with_env, python_table_probe, TempDir};
use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;

#[test]
fn rust_mcp_files_table_matches_python_runtime_bundle() {
    let Some(probed) = python_table_probe() else {
        println!("SKIP: python3 或 cli/runtime_bundle.py 不可用 —— 未做表比对");
        return;
    };
    let py_files = probed
        .get("files")
        .and_then(Value::as_object)
        .expect("python 侧 files 应存在");
    let rust_files: std::collections::BTreeMap<String, String> = MCP_FILES
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect();
    assert_eq!(
        rust_files.len(),
        py_files.len(),
        "条目数不同：rust={} python={}",
        rust_files.len(),
        py_files.len()
    );
    for (src_rel, dst_rel) in py_files {
        assert_eq!(
            rust_files.get(src_rel),
            Some(&dst_rel.as_str().unwrap_or("").to_string()),
            "映射不一致：{src_rel}"
        );
    }
    assert_eq!(
        probed.get("stamp_name").and_then(Value::as_str),
        Some(STAMP_NAME),
        "版本戳文件名不一致"
    );
    assert_eq!(
        probed.get("default_dest_suffix").and_then(Value::as_str),
        Some(MCP_SUBDIR),
        "载荷默认落点后缀不一致"
    );
}

#[test]
fn mcp_payload_record_matches_python() {
    let tmp = TempDir::new("payload-parity");
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    let prog = tmp.path().join("prog");
    std::fs::create_dir_all(&aimail_home).unwrap();
    std::fs::create_dir_all(aimail_home.join("systems").join("s1")).unwrap();
    std::fs::create_dir_all(&user_home).unwrap();

    // 载荷落点：戳只声明一个文件、且内容与核心目录不同 ⇒ missing 空 + stale 非空
    let dest = prog.join(MCP_SUBDIR);
    std::fs::create_dir_all(&dest).unwrap();
    std::fs::write(dest.join("aimail_base.py"), "payload copy body\n").unwrap();
    std::fs::write(
        dest.join(STAMP_NAME),
        serde_json::to_string(&serde_json::json!({
            "version": "1.2.3",
            "files": {"aimail_base.py": "aimail_base.py"},
        }))
        .unwrap(),
    )
    .unwrap();

    let prog_s = prog.to_string_lossy().into_owned();
    let Some(py_checks) = python_check_json_with_env(
        &aimail_home,
        &user_home,
        "s1",
        &[("AIMAIL_PROG_DIR", &prog_s)],
    ) else {
        println!("SKIP: python3 不可用");
        return;
    };
    let mut expected = filter_records(&py_checks, &[("runtime", "mcp-payload")]);
    assert_eq!(
        expected.len(),
        1,
        "Python 侧应产出 mcp-payload：{expected:#?}"
    );
    normalize_fix(&mut expected[0]);
    assert!(!expected[0].2, "夹具应判失败：{expected:#?}");
    assert!(expected[0].3.contains("(v1.2.3)"), "{expected:#?}");
    assert!(expected[0].3.contains("stale: "), "{expected:#?}");

    // Rust 侧：核心目录显式给仓库 pysdk（Python 的 resolve_core_dir 首选项）
    let core = common::repo_root().join("pysdk");
    let mut c = Check::new();
    check_payload(&mut c, &prog, Some(&core));
    assert_eq!(c.checks.len(), 1, "{:?}", c.checks);
    let got = (
        c.checks[0].level.clone(),
        c.checks[0].check.clone(),
        c.checks[0].pass,
        c.checks[0].detail.clone(),
        c.checks[0].fix.clone(),
    );
    let mut got_norm = got;
    normalize_fix(&mut got_norm);
    assert_eq!(got_norm, expected[0]);
}

/// 只归一 fix 里的脚本路径（其余逐字比）。
fn normalize_fix(rec: &mut (String, String, bool, String, String)) {
    if let Some(last) = rec.4.rsplit('/').next() {
        if last.starts_with("runtime_bundle.py") {
            rec.4 = last.to_string();
        }
    }
}

#[test]
fn bundle_default_dest_table_matches_python() {
    // 表一致性守门（与 MCP_FILES 同法）：直接向 Python 要 BUNDLES[*].default_dest 真值逐条比。
    // 源真值缺失 ⇒ 打印原因并跳过（不伪装通过）。
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .to_path_buf();
    let script = "import json,sys; sys.path.insert(0, sys.argv[1]); \
                  import runtime_bundle as rb; \
                  print(json.dumps({k: v['default_dest'] for k, v in rb.BUNDLES.items()}, ensure_ascii=False))";
    let out = Command::new("python3")
        .arg("-c")
        .arg(script)
        .arg(repo.join("cli"))
        .output();
    let Ok(out) = out else {
        eprintln!("跳过：python3 不可用");
        return;
    };
    if !out.status.success() {
        eprintln!(
            "跳过：python 侧探测失败 {}",
            String::from_utf8_lossy(&out.stderr)
        );
        return;
    }
    let py: serde_json::Value = serde_json::from_slice(&out.stdout).expect("python 真值 JSON");
    let py_map = py.as_object().expect("object");
    assert_eq!(
        py_map.len(),
        aimail::core::payload::BUNDLE_DEFAULT_DEST.len(),
        "bundle 数量漂移: python={} rust={}",
        py_map.len(),
        aimail::core::payload::BUNDLE_DEFAULT_DEST.len()
    );
    for (name, dest) in aimail::core::payload::BUNDLE_DEFAULT_DEST {
        assert_eq!(
            py_map.get(*name).and_then(|v| v.as_str()),
            Some(*dest),
            "bundle {name} 的 default_dest 与 Python 不一致"
        );
    }
    // sorted(BUNDLES) 的 "a|b|c" 文案也要一致
    let mut names: Vec<&str> = py_map.keys().map(|s| s.as_str()).collect();
    names.sort_unstable();
    assert_eq!(aimail::core::payload::bundle_names(), names.join("|"));
}
