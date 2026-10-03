//! 集成测试共用件：跨语言 parity 的脚手架（`cli/tests/*.rs` 里 `mod common;` 引入）。
//!
//! 为什么需要它：S4 的引擎内部先落、命令面最后才接，这一阶段拿不到"CLI 对 CLI"的
//! parity 证据 ⇒ 用"同一夹具上 Rust 记录 vs Python `check_status.py --json` 记录"
//! 的真跨语言比对替代。夹具构造、Python 调用、记录取形都在这里单点。

#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

use serde_json::Value;

static SEQ: AtomicU32 = AtomicU32::new(0);

/// 一次性临时目录。
pub struct TempDir {
    path: PathBuf,
}

impl TempDir {
    pub fn new(tag: &str) -> Self {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "aimail-check-parity-{}-{}-{}",
            std::process::id(),
            seq,
            tag
        ));
        std::fs::create_dir_all(&path).expect("temp dir");
        Self { path }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// 仓库根（`cli/tests/...` → 上两级）。
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .to_path_buf()
}

/// 一条检查记录的取形（与 JSON 键序对齐）。
pub type Record = (String, String, bool, String, String);

pub fn tuple_of(rec: &Value) -> Record {
    (
        rec["level"].as_str().unwrap_or("").to_string(),
        rec["check"].as_str().unwrap_or("").to_string(),
        rec["pass"].as_bool().unwrap_or(false),
        rec["detail"].as_str().unwrap_or("").to_string(),
        rec["fix"].as_str().unwrap_or("").to_string(),
    )
}

/// 跑 Python 的 `check_status.py --json` 并取回 checks 列表。
/// 找不到 python3 或脚本时返回 None（调用方打印原因并跳过，不伪装通过）。
pub fn python_check_json(aimail_home: &Path, user_home: &Path, sid: &str) -> Option<Vec<Value>> {
    python_check_json_with_env(aimail_home, user_home, sid, &[])
}

/// 同上，但可追加环境变量（用于 `AIMAIL_PROG_DIR` 这类"程序根"用例）。
pub fn python_check_json_with_env(
    aimail_home: &Path,
    user_home: &Path,
    sid: &str,
    extra_env: &[(&str, &str)],
) -> Option<Vec<Value>> {
    let script = repo_root().join("cli").join("check_status.py");
    if !script.is_file() {
        println!("SKIP: 找不到 {}（非仓库检出？）", script.display());
        return None;
    }
    let mut cmd = Command::new("python3");
    cmd.arg(&script)
        .args(["--json", "--system-id", sid])
        .arg("--agent-home")
        .arg(user_home)
        .env("HOME", user_home)
        .env("AIMAIL_HOME", aimail_home);
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    let out = cmd.output().ok()?;
    if !out.status.success() {
        // check 的 rc 反映"有失败项"，夹具里本来就故意有失败项 ⇒ 不看 rc，只看输出
        println!(
            "note: python check_status rc={:?}（夹具含故意失败项，正常）",
            out.status.code()
        );
    }
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let start = stdout.find("{\n  \"all_pass\"")?;
    let parsed: Value = serde_json::from_str(&stdout[start..]).ok()?;
    Some(parsed["checks"].as_array()?.clone())
}

/// 记录过滤：只保留关心的 `(level, check)`。
pub fn filter_records(checks: &[Value], wanted: &[(&str, &str)]) -> Vec<Record> {
    checks
        .iter()
        .filter(|r| {
            let level = r["level"].as_str().unwrap_or("");
            let check = r["check"].as_str().unwrap_or("");
            wanted.iter().any(|(l, c)| *l == level && *c == check)
        })
        .map(tuple_of)
        .collect()
}
