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

// ════════════════════════════════════════════════════════════════════════════
// S6 切片1：`install --payload` 机器面要用的 bundle/源根解析（`runtime_bundle.py:59-98/110-200/320-340`）
// ════════════════════════════════════════════════════════════════════════════

/// bundle → 默认落点模板（`BUNDLES[*].default_dest` 的 Rust 副本；有**表一致性守门**测试
/// 直接向 Python 要真值比对，漂移即红）。`{program_root}` 由 `payload_dir` 展开，`~` **不**展开
/// （Python 侧 `payload_dir()` 也同样不展开 —— 只有 `install()` 才 `expanduser`）。
pub const BUNDLE_DEFAULT_DEST: &[(&str, &str)] = &[
    ("mcp", "{program_root}/mcp"),
    ("deer-flow", "~/deer-flow/backend/app/gateway/routers"),
    ("skill-hermes", "__profile_skills__"),
    ("skill-openclaw", "~/.openclaw/skills/agentmail"),
    ("skill-deerflow", "~/deer-flow/skills/public/agentmail"),
    ("skill-dsh", "~/.dsh/skills/agentmail"),
];

/// bundle 名的升序列表（Python `sorted(BUNDLES)` 用于错误文案的"可选: …"）。
pub fn bundle_names() -> String {
    let mut v: Vec<&str> = BUNDLE_DEFAULT_DEST.iter().map(|(n, _)| *n).collect();
    v.sort_unstable();
    v.join("|")
}

/// `payload_dir(bundle)`：默认落点（展开 `{program_root}`，**不**展开 `~`）。
pub fn payload_dir_named(bundle: &str) -> Result<String, String> {
    let tpl = BUNDLE_DEFAULT_DEST
        .iter()
        .find(|(n, _)| *n == bundle)
        .map(|(_, d)| *d)
        .ok_or_else(|| format!("ERROR: 未知 bundle {bundle}"))?;
    Ok(tpl.replace(
        "{program_root}",
        &crate::core::home::program_root().to_string_lossy(),
    ))
}

/// `resolve_source_root(explicit)` → `(root, kind)`；kind ∈ `repo|pip`。
///
/// **repo > pip** 与判读侧（check 的 stale 判定）逐字同序：源与判读不同源 ⇒ 刷的是 pip 旧码、
/// 复核拿 repo 新码 ⇒ 永远 stale（`iso14 hermes J5-2` 的病灶）。Rust 侧的"repo"= 部署形态
/// `{program_root}/aimail-src/pysdk`（= Python 的 `<cli>/../pysdk` 同一目录）。
pub fn resolve_source_root(explicit: &str) -> Result<(String, String), String> {
    if !explicit.is_empty() {
        let root = expand_user(explicit);
        if std::path::Path::new(&root).join("aimail_base.py").is_file() {
            return Ok((root, "repo".to_string()));
        }
        return Err(format!(
            "ERROR: --source-root 无效(无 aimail_base.py): {root}"
        ));
    }
    let repo = crate::core::home::program_root()
        .join("aimail-src")
        .join("pysdk");
    if repo.join("aimail_base.py").is_file() {
        return Ok((repo.to_string_lossy().to_string(), "repo".to_string()));
    }
    // pip 兜底：问解释器要包目录
    let py = std::env::var("AIMAIL_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let out = std::process::Command::new(py)
        .args([
            "-c",
            "import aimail,os;print(os.path.dirname(os.path.abspath(aimail.__file__)))",
        ])
        .output();
    if let Ok(o) = out {
        if o.status.success() {
            let dir = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if !dir.is_empty() && std::path::Path::new(&dir).join("aimail_base.py").is_file() {
                return Ok((dir, "pip".to_string()));
            }
        }
    }
    Err("ERROR: 运行时源未找到(pip aimail 未安装且仓库 pysdk/ 缺失)".to_string())
}

/// `source_path(name, explicit_root)`：资源目录（`_resource_path` 的两种布局同一形状）。
pub fn source_path(name: &str, explicit_root: &str) -> Result<String, String> {
    let (root, _kind) = resolve_source_root(explicit_root)?;
    let base = std::path::Path::new(&root).join("resources");
    let p = match name {
        "skills" => base.join("skills"),
        // 2026-09-25：board 只保留角色提示（目录改名 role_prompt）
        "board-role" => base.join("board").join("role_prompt"),
        other => {
            return Err(format!("ERROR: 未知资源 {other}(可选: skills|board-role)"));
        }
    };
    if !p.is_dir() {
        return Err(format!("ERROR: 资源目录不存在: {}", p.display()));
    }
    Ok(p.to_string_lossy().to_string())
}

/// `~` / `~/x` 展开（Python `os.path.expanduser` 的最小等价；不做 `~user`）。
fn expand_user(path: &str) -> String {
    if path == "~" {
        return crate::core::home::user_home().to_string_lossy().to_string();
    }
    if let Some(rest) = path.strip_prefix("~/") {
        return crate::core::home::user_home()
            .join(rest)
            .to_string_lossy()
            .to_string();
    }
    path.to_string()
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
