//! 运行时载荷状态 —— `cli/runtime_bundle.py:196-236`（`payload_dir` / `payload_state`）
//! 与 `cli/check_status.py:1647-1671`（`_check_payload`）的复刻。
//!
//! 语义要点（照抄不"优化"）：
//! - 载荷**未装**（无版本戳）⇒ 不出行（不虚报缺失）；
//! - `missing` = 戳里声明但文件已不在；`stale` = 与本机自用运行时核心 md5 不一致，
//!   或"规格里有、戳里没声明"（新增未装）；
//! - 核心目录（python 的 `runtime_core.resolve_core_dir()`：仓库 pysdk/ 优先）在 Rust 侧
//!   **显式传入** —— 单二进制没有"自身所在源码目录"，生产形态对应已装程序根下的快照副本
//!   （`{program_root}/aimail-src/pysdk`），由命令层解析后传进来。

use crate::core::check::Check;
use std::path::{Path, PathBuf};

/// 载荷默认落点相对程序根的子目录（`BUNDLES["mcp"].default_dest` = `{program_root}/mcp`）。
pub const MCP_SUBDIR: &str = "mcp";

/// 版本戳文件名（`runtime_bundle.STAMP_NAME`）。
pub const STAMP_NAME: &str = ".aimail-runtime.json";

/// `mcp` 载荷的文件映射：源相对路径 → 落地相对路径
/// （`runtime_bundle.BUNDLES["mcp"]["files"]`；与 Python 表的一致性由
/// `cli/tests/payload_parity.rs` 的跨语言断言守住，避免两边各自漂移）。
pub const MCP_FILES: &[(&str, &str)] = &[
    ("aimail_base.py", "aimail_base.py"),
    ("aimail_tools.py", "aimail_tools.py"),
    ("aimail_board.py", "aimail_board.py"),
    ("aimail_contract.py", "aimail_contract.py"),
    ("gateway_api.py", "gateway_api.py"),
    ("_aimail_bootstrap.py", "_aimail_bootstrap.py"),
    ("aimail_mcp_server.py", "aimail_mcp_server.py"),
];

/// 载荷状态（`payload_state` 的返回结构）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PayloadState {
    pub dest: PathBuf,
    pub present: bool,
    pub version: String,
    pub files: Vec<String>,
    pub missing: Vec<String>,
    pub stale: Vec<String>,
}

/// 载荷默认落点。
pub fn payload_dir(prog_root: &Path) -> PathBuf {
    prog_root.join(MCP_SUBDIR)
}

fn md5_hex(path: &Path) -> Option<String> {
    use md5::{Digest, Md5};
    let bytes = std::fs::read(path).ok()?;
    let mut h = Md5::new();
    h.update(&bytes);
    Some(format!("{:x}", h.finalize()))
}

/// `payload_state(bundle="mcp", dest=...)`。`core_dir=None` 等价 Python 侧
/// `resolve_core_dir()` 抛 SystemExit 后 `src=""` 的情形（跳过 md5 分支）。
pub fn payload_state(files: &[(&str, &str)], dest: &Path, core_dir: Option<&Path>) -> PayloadState {
    let mut state = PayloadState {
        dest: dest.to_path_buf(),
        present: false,
        version: String::new(),
        files: Vec::new(),
        missing: Vec::new(),
        stale: Vec::new(),
    };
    let stamp_file = dest.join(STAMP_NAME);
    if !stamp_file.is_file() {
        return state;
    }
    let Ok(text) = std::fs::read_to_string(&stamp_file) else {
        return state;
    };
    let Ok(stamp) = serde_json::from_str::<serde_json::Value>(&text) else {
        return state;
    };
    let mut declared: Vec<String> = stamp
        .get("files")
        .and_then(serde_json::Value::as_object)
        .map(|o| o.keys().cloned().collect())
        .unwrap_or_default();
    declared.sort();
    state.present = true;
    state.version = stamp
        .get("version")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    state.files = declared.clone();
    state.missing = declared
        .iter()
        .filter(|rel| !dest.join(rel.as_str()).is_file())
        .cloned()
        .collect();

    let mut targets: Vec<String> = files.iter().map(|(_, dst)| dst.to_string()).collect();
    targets.sort();
    targets.dedup();
    for rel in targets {
        if state.missing.contains(&rel) {
            continue;
        }
        let src_file = core_dir.map(|d| d.join(&rel));
        let src_ok = src_file.as_ref().map(|p| p.is_file()).unwrap_or(false);
        let differs = if src_ok {
            let s = src_file.as_ref().expect("checked");
            match (md5_hex(s), md5_hex(&dest.join(&rel))) {
                (Some(a), Some(b)) => a != b,
                _ => false,
            }
        } else {
            false
        };
        if !declared.contains(&rel) || differs {
            state.stale.push(rel);
        }
    }
    state
}

/// `_check_payload`：载荷未装 ⇒ 不出行；装了 ⇒ 按 missing/stale 出一行记录。
pub fn check_payload(c: &mut Check, prog_root: &Path, core_dir: Option<&Path>) {
    let dest = payload_dir(prog_root);
    let st = payload_state(MCP_FILES, &dest, core_dir);
    if !st.present {
        return;
    }
    let mut parts: Vec<String> = Vec::new();
    if !st.missing.is_empty() {
        parts.push(format!("missing: {}", st.missing.join(", ")));
    }
    if !st.stale.is_empty() {
        parts.push(format!("stale: {}", st.stale.join(", ")));
    }
    let mut detail = format!("{} (v{})", st.dest.display(), st.version);
    if !parts.is_empty() {
        detail.push_str(" — ");
        detail.push_str(&parts.join("; "));
    }
    c.add(
        "runtime",
        "mcp-payload",
        parts.is_empty(),
        &detail,
        &format!(
            "python3 {} install mcp",
            prog_root
                .join("aimail-src")
                .join("cli")
                .join("runtime_bundle.py")
                .display()
        ),
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    fn write_stamp(dest: &Path, version: &str, files: &[&str]) {
        std::fs::create_dir_all(dest).unwrap();
        let map: serde_json::Map<String, serde_json::Value> = files
            .iter()
            .map(|f| ((*f).to_string(), serde_json::json!(*f)))
            .collect();
        std::fs::write(
            dest.join(STAMP_NAME),
            serde_json::to_string(&serde_json::json!({
                "version": version, "files": map,
            }))
            .unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn absent_stamp_means_not_present_and_no_records() {
        let tmp = TempDir::new("payload-absent");
        let dest = payload_dir(tmp.path());
        let st = payload_state(MCP_FILES, &dest, None);
        assert!(!st.present);
        let mut c = Check::new();
        check_payload(&mut c, tmp.path(), None);
        assert!(c.checks.is_empty(), "未装载荷不该出行：{:?}", c.checks);
    }

    #[test]
    fn missing_and_stale_are_classified_like_python() {
        let tmp = TempDir::new("payload-state");
        let prog = tmp.path();
        let dest = payload_dir(prog);
        // 戳声明两个文件，只落地一个 ⇒ 另一个进 missing
        write_stamp(&dest, "9.9.9", &["aimail_base.py", "gateway_api.py"]);
        std::fs::write(dest.join("aimail_base.py"), "body\n").unwrap();
        // 核心目录有同名文件但与落地内容不同 ⇒ stale
        let core = tmp.path().join("core");
        std::fs::create_dir_all(&core).unwrap();
        std::fs::write(core.join("aimail_base.py"), "different\n").unwrap();

        let st = payload_state(MCP_FILES, &dest, Some(&core));
        assert!(st.present);
        assert_eq!(st.version, "9.9.9");
        assert_eq!(st.missing, vec!["gateway_api.py".to_string()]);
        // aimail_base.py：戳里声明 + md5 不同 ⇒ stale；其余未声明 ⇒ 也算 stale（新增未装）
        assert!(st.stale.contains(&"aimail_base.py".to_string()));
        assert!(st.stale.contains(&"aimail_tools.py".to_string()));

        let mut c = Check::new();
        check_payload(&mut c, prog, Some(&core));
        assert_eq!(c.checks.len(), 1);
        let rec = &c.checks[0];
        assert_eq!(rec.check, "mcp-payload");
        assert!(!rec.pass);
        assert!(
            rec.detail.contains("missing: gateway_api.py"),
            "{}",
            rec.detail
        );
        assert!(rec.detail.contains("stale: "), "{}", rec.detail);
    }

    #[test]
    fn identical_content_is_not_stale() {
        let tmp = TempDir::new("payload-fresh");
        let dest = payload_dir(tmp.path());
        write_stamp(&dest, "1.0.0", &["aimail_base.py"]);
        std::fs::write(dest.join("aimail_base.py"), "same\n").unwrap();
        let core = tmp.path().join("core");
        std::fs::create_dir_all(&core).unwrap();
        std::fs::write(core.join("aimail_base.py"), "same\n").unwrap();
        let st = payload_state(&[("aimail_base.py", "aimail_base.py")], &dest, Some(&core));
        assert!(st.stale.is_empty(), "{:?}", st.stale);
    }
}
