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
    // SDK 根：`sdkroot` 语义 = 快照优先 ⇒ 夹具里做 `prog/aimail-src -> 仓库` 软链，
    // 让 CLI 用**仓库 pysdk**（否则会命中已装工具包里的旧快照，缺新函数）
    let prog = home.join("prog");
    let link = prog.join("aimail-src");
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

    // ⑤ test ⇒ 响亮未移植（不静默降级）
    let (rc, _, err) = run(
        &home,
        &["prompt", "test", "-s", "s1", "-e", "agent@example.test"],
    );
    assert_eq!(rc, 1, "{err}");
    assert!(err.contains("not yet ported"), "{err}");
}
