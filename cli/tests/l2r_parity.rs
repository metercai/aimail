//! L2r（平台运行时资源）的跨语言验收 —— 与 L0/L1/L2 同一思路。
//!
//! 两档夹具：
//! - **hermes 档**：注册表 `health_checks` 的 4 种 kind 全覆盖（file_contains 通过 /
//!   file_contains_alt 失败 / file_exists_any 的 glob 通过 / yaml_toolsets 通过）+
//!   board 资源失败 + platform-locatable 通过；
//! - **deerflow 档**：`payload_refs` 的"宿主引用的载荷文件"检查（1 缺 1 在），
//!   需要 `AIMAIL_PROG_DIR` 指向夹具程序根（Python 侧读同名环境变量）。

mod common;

use std::path::{Path, PathBuf};

use aimail::core::check::Check;
use aimail::core::checks::l0::Ctx;
use aimail::core::checks::l2r;
use aimail::core::{config, contract};
use common::{filter_records, python_check_json_with_env, Record, TempDir};

const HERMES_WANTED: &[(&str, &str)] = &[
    ("runtime", "platform-locatable"),
    ("runtime", "patch-webhook"),
    ("runtime", "patch-profiles"),
    ("runtime", "skills"),
    ("runtime", "toolsets"),
    ("runtime", "board-resources"),
];

const DEERFLOW_WANTED: &[(&str, &str)] = &[
    ("runtime", "platform-locatable"),
    ("runtime", "patch-app"),
    ("runtime", "board-resources"),
    ("runtime", "host-payload-refs"),
];

fn base_fixture(tmp: &TempDir) -> (PathBuf, PathBuf) {
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    std::fs::create_dir_all(aimail_home.join("systems").join("s1")).unwrap();
    std::fs::create_dir_all(&user_home).unwrap();
    (aimail_home, user_home)
}

fn write_gw(aimail_home: &Path, system_home: &Path) {
    std::fs::write(
        aimail_home
            .join("systems")
            .join("s1")
            .join(config::GATEWAY_CONFIG_NAME),
        format!(
            r#"{{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1",
                 "system_name":"s1","domain":"example.test","system_home":"{}"}}"#,
            system_home.display()
        ),
    )
    .unwrap();
}

/// `host-payload-refs` 的 fix 是"去哪跑安装"的提示：Python 给源码检出里的
/// `runtime_bundle.py` 路径，Rust 给已装程序根下的副本路径（部署形态truth）。
/// 只归一这一条的路径部分，其余字段/记录仍逐字比。
fn normalize_fix(rec: &mut Record) {
    if rec.1 == "host-payload-refs" {
        if let Some(last) = rec.4.rsplit('/').next() {
            if last.starts_with("runtime_bundle.py") {
                rec.4 = last.to_string();
            }
        }
    }
}

fn rust_records(ctx: &Ctx, wanted: &[(&str, &str)], prog_root: &Path) -> Vec<Record> {
    let mut c = Check::new();
    l2r::runtime(&mut c, "s1", ctx, prog_root);
    c.checks
        .iter()
        .filter(|r| wanted.iter().any(|(l, k)| *l == r.level && *k == r.check))
        .map(|r| {
            (
                r.level.clone(),
                r.check.clone(),
                r.pass,
                r.detail.clone(),
                r.fix.clone(),
            )
        })
        .collect()
}

#[test]
fn hermes_runtime_records_match_python() {
    let tmp = TempDir::new("l2r-hermes");
    let (aimail_home, user_home) = base_fixture(&tmp);
    let plat = user_home.join(".hermes");
    // 平台特征（注册表 detect.markers = hermes-agent + profiles）
    std::fs::create_dir_all(plat.join("hermes-agent")).unwrap();
    std::fs::create_dir_all(plat.join("profiles/p1/skills/agentmail")).unwrap();
    write_gw(&aimail_home, &plat);

    // file_contains：marker 在 ⇒ 通过
    std::fs::create_dir_all(plat.join("hermes-agent/gateway/platforms")).unwrap();
    std::fs::write(
        plat.join("hermes-agent/gateway/platforms/webhook.py"),
        "# PREPROCESS_REGISTRY hook\n",
    )
    .unwrap();
    // file_contains_alt：两个候选都没有 marker ⇒ 失败（path 取第一个候选）
    std::fs::create_dir_all(plat.join("hermes-agent/hermes_cli")).unwrap();
    std::fs::write(plat.join("hermes-agent/hermes_cli/profiles.py"), "x = 1\n").unwrap();
    // file_exists_any：glob 命中 profiles/*/skills/agentmail/SKILL.md
    std::fs::write(
        plat.join("profiles/p1/skills/agentmail/SKILL.md"),
        "skill body\n",
    )
    .unwrap();
    // yaml_toolsets：platform_toolsets 含契约 skill 名
    std::fs::write(
        plat.join("config.yaml"),
        format!(
            "platform_toolsets:\n  default:\n    - {}\n",
            contract::agent_skill_name()
        ),
    )
    .unwrap();

    let Some(py_checks) = common::python_check_json(&aimail_home, &user_home, "s1") else {
        println!("SKIP: python3 不可用");
        return;
    };
    let expected = filter_records(&py_checks, HERMES_WANTED);
    assert!(
        expected.len() >= 6,
        "hermes 档记录太少（{} 条）：{expected:#?}",
        expected.len()
    );

    let ctx = Ctx {
        aimail_home,
        user_home,
    };
    let actual = rust_records(&ctx, HERMES_WANTED, &tmp.path().join("prog"));
    assert_eq!(
        actual.len(),
        expected.len(),
        "条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致");
    }
    // 关键分支确认（防退化成"全 fail 也通过"）
    let by = |n: &str| actual.iter().find(|r| r.1 == n).cloned().unwrap();
    assert!(by("patch-webhook").2, "{actual:#?}");
    assert!(!by("patch-profiles").2, "{actual:#?}");
    assert!(by("skills").2, "{actual:#?}");
    assert!(by("toolsets").2, "{actual:#?}");
    assert!(!by("board-resources").2, "{actual:#?}");
}

#[test]
fn deerflow_payload_refs_match_python() {
    let tmp = TempDir::new("l2r-deerflow");
    let (aimail_home, user_home) = base_fixture(&tmp);
    let plat = user_home.join(".deer-flow");
    // 平台特征（注册表 detect.markers = backend/app/gateway，无 dir_name）
    std::fs::create_dir_all(plat.join("backend/app/gateway")).unwrap();
    write_gw(&aimail_home, &plat);
    // patch-app：无 marker ⇒ 失败
    std::fs::write(plat.join("backend/app/gateway/app.py"), "x = 1\n").unwrap();

    // 程序根夹具（两侧都读 AIMAIL_PROG_DIR）
    let prog = tmp.path().join("prog");
    std::fs::create_dir_all(prog.join("mcp")).unwrap();
    std::fs::write(prog.join("runtime_bundle.py"), "# stub\n").unwrap();
    std::fs::write(prog.join("mcp").join("aimail_mcp_server.py"), "# present\n").unwrap();
    let dest = prog.join("mcp");
    // 宿主配置引用载荷：一个在、一个缺
    std::fs::write(
        plat.join("extensions_config.json"),
        format!(
            r#"{{"mcp":{{"command":"python3","args":["{}/aimail_mcp_server.py","{}/gone_missing.py"]}}}}"#,
            dest.display(),
            dest.display()
        ),
    )
    .unwrap();

    let prog_s = prog.to_string_lossy().into_owned();
    let Some(py_checks) = python_check_json_with_env(
        &aimail_home,
        &user_home,
        "s1",
        &[("AIMAIL_PROG_DIR", prog_s.as_str())],
    ) else {
        println!("SKIP: python3 不可用");
        return;
    };
    let mut expected = filter_records(&py_checks, DEERFLOW_WANTED);
    for r in expected.iter_mut() {
        normalize_fix(r);
    }
    let payload = expected
        .iter()
        .find(|r| r.1 == "host-payload-refs")
        .cloned();
    let Some(payload) = payload else {
        println!("SKIP: Python 侧未产出 host-payload-refs（夹具/环境不适用）");
        return;
    };
    assert!(!payload.2, "引用了不存在的载荷 ⇒ 应失败：{payload:?}");
    assert!(payload.3.contains("missing gone_missing.py"), "{payload:?}");

    let ctx = Ctx {
        aimail_home,
        user_home,
    };
    let mut actual = rust_records(&ctx, DEERFLOW_WANTED, &prog);
    for r in actual.iter_mut() {
        normalize_fix(r);
    }
    assert_eq!(
        actual.len(),
        expected.len(),
        "条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致");
    }
}
