//! `prompt` 切片1 验收：`list`/`rm`/`create-file` 与命令面（`add` 的 SDK 落盘路径留 L2；`test` 未移植）。
//!
//! 判据：①裸 `prompt` ⇒ rc=2（`paction` required）；②`list` 渲染三态（地址级 role / common.md 兜底 /
//! MISSING）+ 按名排序 + 字段 `|` 连接 + `[disabled]` + 无字段行告警；③`rm` 未命中逐字；
//! ④`create-file` 校验与落盘；⑤`test` 响亮未移植。

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use aimail::core::{bridge_wire as bw, contract};

fn bin() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("target/debug/aimail");
    p
}

fn run(home: &Path, argv: &[&str]) -> (i32, String, String) {
    // SDK 根：`sdkroot` 语义 = 快照优先 ⇒ 夹具里做 `prog/sdk-staging-removed -> 仓库` 软链，
    // 让 CLI 用**仓库 pysdk**（否则会命中已装工具包里的旧快照，缺新函数）
    let prog = home.join("prog");
    let link = prog.join("sdk-staging-removed");
    if !link.exists() {
        std::fs::create_dir_all(&prog).unwrap();
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf();
        std::os::unix::fs::symlink(&repo, &link).unwrap();
    }
    let out = Command::new(bin())
        .args(argv)
        .env("AIMAIL_PROG_DIR", &prog)
        .env("AIMAIL_HOME", home)
        .env("HOME", home)
        .env_remove("AIMAIL_PYTHON")
        .stdin(Stdio::null())
        .output()
        .expect("spawn");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

/// 夹具：两个规则（一个带字段+禁用、一个无字段），三个 role 文件（地址级 / 系统级 common 兜底 / 无）。
fn fixture() -> (tempfile::TempDir, PathBuf) {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    let sys = home.join("systems/s1");
    let leaf = sys.join(bw::addr_clean("agent@example.test"));
    std::fs::create_dir_all(leaf.join("role_prompt")).unwrap();
    std::fs::write(
        sys.join("aimail_gateway.json"),
        r#"{"gateway_url":"http://127.0.0.1:1","admin_key":"AK","system_id":"s1","domain":"example.test","default_agent_name":"agent"}"#,
    )
    .unwrap();
    let rules = r#"[
      {"name":"30_later","file":"later","subject":["审批","approve"],"body":["x"]},
      {"name":"10_first","file":"first","sender":["m@example.test"],"enabled":false},
      {"name":"40_nofield","file":"nofield"},
      {"name":"50_common","file":"commononly"}
    ]"#;
    std::fs::write(
        leaf.join(contract::binding_file()),
        format!(
            r#"{{"email":"agent@example.test","api_key":"K","webhook_url":"","prompt_rules":{rules}}}"#
        ),
    )
    .unwrap();
    // 地址级 role（10_first）；系统级 common.md 兜底（50_common 自身文件不存在 ⇒ 走 common.md）
    std::fs::write(leaf.join("role_prompt/first.md"), "# first\n").unwrap();
    std::fs::create_dir_all(sys.join("board/role_prompt")).unwrap();
    std::fs::write(sys.join("board/role_prompt/common.md"), "# common\n").unwrap();
    (tmp, home)
}

/// 角色文件完全缺失（连 `common.md` 都没有）⇒ `list` 必须提示 MISSING（loader 匹配时会跳过）。
#[test]
fn role_missing_is_reported_when_no_common_md() {
    let (_t, home) = fixture();
    std::fs::remove_file(home.join("systems/s1/board/role_prompt/common.md")).unwrap();
    let (rc, out, err) = run(
        &home,
        &["prompt", "list", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    assert!(
        out.contains("MISSING (loader will skip on match)"),
        "缺 role 应提示 MISSING: {out}"
    );
}

#[test]
fn prompt_faces_list_rm_createfile_and_command_surface() {
    let (_t, home) = fixture();
    // ① 裸 prompt ⇒ rc=2（子命令必填）
    let (rc, _, err) = run(&home, &["prompt"]);
    assert_eq!(rc, 2, "裸 prompt 应 rc=2: {err}");

    // ② list：按名排序 + 字段渲染 + 禁用标记 + role 三态
    let (rc, out, err) = run(
        &home,
        &["prompt", "list", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    let i30 = out.find("30_later").unwrap();
    let i10 = out.find("10_first").unwrap();
    assert!(i10 < i30, "应按名排序（10 在 30 前）: {out}");
    assert!(
        out.contains("subject=[审批 | approve]; body=[x]"),
        "字段渲染: {out}"
    );
    assert!(out.contains("file=first  [disabled]"), "禁用标记: {out}");
    assert!(
        out.contains("(NO FIELD — loader skips this rule)"),
        "无字段行告警: {out}"
    );
    assert!(out.contains("role_prompt/first.md"), "地址级 role: {out}");
    assert!(
        out.contains("common.md (common.md fallback)"),
        "系统级 common 兜底: {out}"
    );

    // ③ rm 未命中
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "rm",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "-n",
            "90_missing",
        ],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(
        out.contains("rule 90_missing not found for this agent"),
        "{out}"
    );

    // ④ create-file：坏 stem ⇒ rc=1；好 stem ⇒ 落盘 + 内容头
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "create-file",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "-n",
            "20_new",
            "--file",
            "Bad.Stem",
        ],
    );
    assert_eq!(rc, 1, "{out}");
    assert!(out.contains("bad file stem"), "{out}");
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "create-file",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "-n",
            "20_new",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("created"), "{out}");
    let f = home
        .join("systems/s1")
        .join(bw::addr_clean("agent@example.test"))
        .join("role_prompt/new.md");
    let text = std::fs::read_to_string(&f).expect("角色文件应已创建");
    assert!(text.starts_with("# new"), "{text}");

    // ⑤ test 已接线（L1–L5 仿真）：裸跑（无输入）⇒ rc=0 + 兜底行
    //    （L1–L5 的逐层语义由 `prompt_test_walks_the_chain_and_matches_via_sdk` 覆盖）
    let (rc, out, err) = run(
        &home,
        &["prompt", "test", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    assert!(
        out.contains("(matched against: subject='' sender='' to='')"),
        "{out}"
    );
}

/// `prompt test`（L1–L5 单源匹配仿真）：内建两级 + L4 头 + L5 本机规则 + 兜底行。
#[test]
fn prompt_test_walks_the_chain_and_matches_via_sdk() {
    let (_t, home) = fixture();

    // L1 内建 [WHOAMI]（role 文件缺 ⇒ MISSING，但**已命中即返回**）
    let (rc, out, err) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "[whoami] x",
        ],
    );
    assert_eq!(rc, 0, "rc={rc}\nout={out}\nerr={err}");
    assert!(
        out.contains("L1 [WHOAMI] (built-in) → file=whoami"),
        "{out}"
    );
    // 夹具里系统级 `common.md` 存在 ⇒ whoami 走兜底（MISSING 只在连 common.md 都缺时出现，
    // 该态由 `role_missing_is_reported_when_no_common_md` 覆盖）
    assert!(out.contains("common.md (common.md fallback)"), "{out}");

    // L2 内建 welcome（标记 + 三标签齐）
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "Welcome to AIMail World, agent, since 2026-10-04!",
            "--body",
            "persona: p\nsignature: s\ncurrent_time: t",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("L2 welcome (built-in) → file=role_calibrator"),
        "{out}"
    );

    // 标记在、标签缺 ⇒ MISMATCH 提示（并继续往下走）
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "Welcome to AIMail World",
            "--body",
            "persona: p",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("marker=true labels=false"), "{out}");

    // L5：`30_later` 命中 —— 注意匹配是"字段间**且**"（规则同时带 subject 与 body 关键词）
    // ⇒ 样本必须**同时**满足两个字段，否则正确结果就是 miss。
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "please approve this",
            "--body",
            "contains x here",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("L5 HIT 30_later → file=later"), "{out}");
    assert!(out.contains("common.md (common.md fallback)"), "{out}");
    assert!(
        out.contains("(matched against: subject='please approve this' sender='' to='')"),
        "{out}"
    );

    // 反例：只给 subject（body 空）⇒ `body:["x"]` 不命中 ⇒ 正确落 miss（字段间且）
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--subject",
            "please approve this",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("L5 miss 30_later"), "字段间且: {out}");

    // L5：无命中 ⇒ 兜底行；且**禁用规则不参与**（`10_first` 已 enabled=false）
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--sender",
            "m@example.test",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("L5: no rule matched — chain falls through (default prompt)"),
        "{out}"
    );
    assert!(!out.contains("L5 HIT 10_first"), "禁用规则不得命中: {out}");
    assert!(
        !out.contains("L5 miss 10_first"),
        "禁用规则不进匹配循环: {out}"
    );

    // L4：头指定且 role 文件不存在 ⇒ 仍经 `common.md` 兜底命中（同 L1 的口径）；
    // 「连 common.md 都缺 ⇒ MISSING + falls through」由无 common.md 的夹具覆盖（见另一用例）。
    let (rc, out, _) = run(
        &home,
        &[
            "prompt",
            "test",
            "-s",
            "s1",
            "-e",
            "agent@example.test",
            "--header",
            "nope",
        ],
    );
    assert_eq!(rc, 0, "{out}");
    assert!(
        out.contains("L4 X-AIMail-Prompt (gateway header) → file=nope"),
        "{out}"
    );
    assert!(out.contains("common.md (common.md fallback)"), "{out}");
}
