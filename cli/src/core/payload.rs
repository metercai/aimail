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
/// 一个 Runtime bundle 的规格（`runtime_bundle.py:59-98` `BUNDLES` 的 Rust 副本）。
pub struct Bundle {
    pub name: &'static str,
    pub default_dest: &'static str,
    /// `(源相对路径, 目标相对路径)`；目标一律铺平（无子目录，见 Python 的 files 映射）。
    pub files: &'static [(&'static str, &'static str)],
    /// 纯资源类 bundle 不写戳（Python `no_stamp: True`）。
    pub no_stamp: bool,
}

/// 核心运行时模块（`_CORE_FILES`）—— mcp / deer-flow 两个 bundle 共用。
pub const CORE_FILES: &[(&str, &str)] = &[
    ("aimail_base.py", "aimail_base.py"),
    ("aimail_tools.py", "aimail_tools.py"),
    ("aimail_board.py", "aimail_board.py"),
    ("aimail_contract.py", "aimail_contract.py"),
    ("gateway_api.py", "gateway_api.py"),
    ("_aimail_bootstrap.py", "_aimail_bootstrap.py"),
];

const DEERFLOW_FILES: &[(&str, &str)] = &[
    ("aimail_base.py", "aimail_base.py"),
    ("aimail_tools.py", "aimail_tools.py"),
    ("aimail_board.py", "aimail_board.py"),
    ("aimail_contract.py", "aimail_contract.py"),
    ("gateway_api.py", "gateway_api.py"),
    ("_aimail_bootstrap.py", "_aimail_bootstrap.py"),
    ("deer-flow/aimail_inbound.py", "aimail_inbound.py"),
    ("deer-flow/aimail_deerflow.py", "aimail_deerflow.py"),
];

const SKILL_FILES: &[(&str, &str)] = &[
    ("resources/skills/SKILL.md", "SKILL.md"),
    ("resources/skills/DESCRIPTION.md", "DESCRIPTION.md"),
];

const SKILL_FILES_DSH: &[(&str, &str)] = &[("resources/skills/SKILL.md", "SKILL.md")];

/// 六个 bundle（有**表一致性守门**测试：向 Python 要 `BUNDLES` 真值逐条比 default_dest）。
pub const BUNDLES: &[Bundle] = &[
    Bundle {
        name: "mcp",
        default_dest: "{program_root}/mcp",
        files: MCP_FILES,
        no_stamp: false,
    },
    Bundle {
        name: "deer-flow",
        default_dest: "~/deer-flow/backend/app/gateway/routers",
        files: DEERFLOW_FILES,
        no_stamp: false,
    },
    Bundle {
        name: "skill-hermes",
        default_dest: "__profile_skills__",
        files: SKILL_FILES,
        no_stamp: true,
    },
    Bundle {
        name: "skill-openclaw",
        default_dest: "~/.openclaw/skills/{skill}",
        files: SKILL_FILES,
        no_stamp: true,
    },
    Bundle {
        name: "skill-deerflow",
        // deer-flow 强制 name == 目录名 ⇒ 目录 = 契约 AGENT_SKILL_NAME（不是产品名）
        default_dest: "~/deer-flow/skills/public/{skill}",
        files: SKILL_FILES,
        no_stamp: true,
    },
    Bundle {
        name: "skill-dsh",
        default_dest: "~/.dsh/skills/{skill}",
        files: SKILL_FILES_DSH,
        no_stamp: true,
    },
];

/// CLI 声明的载荷最低版本（`MIN_PAYLOAD_VERSION`）。
pub const MIN_PAYLOAD_VERSION: &str = "0.1.0";

/// bundle 规格（未知 ⇒ None）。
pub fn bundle(name: &str) -> Option<&'static Bundle> {
    BUNDLES.iter().find(|b| b.name == name)
}

/// bundle 的默认落点（`BUNDLES[*].default_dest`），`{skill}` 展开为**契约**里的 agent 技能名
/// （不写死名，避免第二真源；与 `contract::agent_skill_name()` 同源）。
pub fn bundle_default_dest(name: &str) -> Option<String> {
    let tpl = bundle(name)?.default_dest;
    Some(tpl.replace("{skill}", crate::core::contract::agent_skill_name()))
}

/// bundle 名的升序列表（Python `sorted(BUNDLES)` 用于错误文案的"可选: …"）。
pub fn bundle_names() -> String {
    let mut v: Vec<&str> = BUNDLES.iter().map(|b| b.name).collect();
    v.sort_unstable();
    v.join("|")
}

/// `payload_dir(bundle)`：默认落点（展开 `{program_root}`，**不**展开 `~`）。
pub fn payload_dir_named(bundle: &str) -> Result<String, String> {
    let tpl = bundle_default_dest(bundle).ok_or_else(|| format!("ERROR: 未知 bundle {bundle}"))?;
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
    // 薄壳：布局知识单真源在 `core::sdkroot`（C：CLI 只认"SDK 根相对"的逻辑入口名）
    let r = if explicit.is_empty() {
        crate::core::sdkroot::resolve()?
    } else {
        crate::core::sdkroot::at(explicit)?
    };
    Ok((r.path.to_string_lossy().to_string(), r.kind.to_string()))
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

/// `install(bundle, dest, source_root, force)`（`runtime_bundle.py:239-301`）：幂等铺设 + 旧文件清理 + 戳。
///
/// 照抄点：① 源缺失 ⇒ 打印 `✗ {bundle}: 源缺失 [...]` 并 **rc=1**（不写戳）；② 逐文件按
/// `force || 不存在 || md5 不同` 决定是否复制；③ 清理上一版戳里、本次不再随包分发的旧文件
/// （只删**本目录内**的，防路径穿越）；④ 戳 = `{bundle,version,source,installed_at,min_version,files{rel:md5}}`
/// 经 `tmp → rename`（`installed_at` 每次不同 ⇒ 跨语言只能归一比）；⑤ 三行结果文案逐字。
pub fn install_bundle(bundle_name: &str, dest: &str, source_root: &str, force: bool) -> i32 {
    let Some(spec) = bundle(bundle_name) else {
        println!("ERROR: 未知 bundle {bundle_name}");
        return 2;
    };
    let (root, kind) = match resolve_source_root(source_root) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            return 1;
        }
    };
    let dest = if dest.is_empty() {
        spec.default_dest
    } else {
        dest
    };
    let dest = crate::core::home::abs_path(std::path::Path::new(&expand_user(&expand_dest(dest))));
    let version = source_version(&root, &kind);
    let dsts = spec.files.iter().map(|(_, d)| *d).collect::<Vec<_>>();
    let keep: std::collections::BTreeSet<&str> = dsts.iter().copied().collect();
    let mut changed: Vec<&str> = Vec::new();
    let mut missing_src: Vec<&str> = Vec::new();
    for (src_rel, dst_rel) in spec.files {
        let src = std::path::Path::new(&root).join(src_rel);
        let dst = dest.join(dst_rel);
        if !src.is_file() {
            missing_src.push(src_rel);
            continue;
        }
        let need = force || !dst.is_file() || md5_file(&src) != md5_file(&dst);
        if need {
            if let Some(parent) = dst.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if std::fs::copy(&src, &dst).is_ok() {
                changed.push(dst_rel);
            }
        }
    }
    if !missing_src.is_empty() {
        println!(
            "  ✗ {bundle_name}: 源缺失 {}(源根 {root})",
            py_list(&missing_src)
        );
        return 1;
    }
    // 清理旧戳里有、本次不再分发的旧文件（只删本目录内）
    let stamp_path = dest.join(STAMP_NAME);
    let mut pruned: Vec<String> = Vec::new();
    if stamp_path.is_file() {
        let old: std::collections::BTreeMap<String, Value> = std::fs::read_to_string(&stamp_path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.get("files").and_then(Value::as_object).cloned())
            .map(|m| m.into_iter().collect())
            .unwrap_or_default();
        let mut stale: Vec<String> = old
            .keys()
            .filter(|k| !keep.contains(k.as_str()))
            .cloned()
            .collect();
        stale.sort();
        for rel in stale {
            let p = dest.join(&rel);
            if p.parent() != Some(dest.as_path()) {
                continue; // 只删本目录内
            }
            if p.is_file() && std::fs::remove_file(&p).is_ok() {
                pruned.push(rel);
            }
        }
    }
    if !spec.no_stamp {
        let mut files = serde_json::Map::new();
        for rel in &dsts {
            let p = dest.join(rel);
            if p.is_file() {
                files.insert((*rel).to_string(), Value::String(md5_file(&p)));
            }
        }
        let stamp = serde_json::json!({
            "bundle": bundle_name,
            "version": version,
            "source": kind,
            "installed_at": format!("{}+00:00", crate::core::time::now_iso8601_z().trim_end_matches('Z')),
            "min_version": MIN_PAYLOAD_VERSION,
            "files": Value::Object(files),
        });
        let tmp = dest.join(format!("{STAMP_NAME}.tmp"));
        if std::fs::create_dir_all(&dest).is_ok() {
            let text = serde_json::to_string_pretty(&stamp).unwrap_or_default();
            if std::fs::write(&tmp, text).is_ok() {
                let _ = std::fs::rename(&tmp, &stamp_path);
            }
        }
    }
    if !pruned.is_empty() {
        println!(
            "  ✓ {bundle_name}: 清理旧文件 {} → {}",
            py_list_owned(&pruned),
            dest.display()
        );
    }
    if !changed.is_empty() {
        println!(
            "  ✓ {bundle_name}: 更新 {} 文件 → {} (v{version}, {kind})",
            changed.len(),
            dest.display()
        );
    } else {
        println!(
            "  ✓ {bundle_name}: 已一致(跳过)→ {} (v{version}, {kind})",
            dest.display()
        );
    }
    0
}

/// `_expand_dest`：只替换 `{program_root}`（`~` 留给 `expand_user`，与 Python 同序）。
fn expand_dest(template: &str) -> String {
    template.replace(
        "{program_root}",
        &crate::core::home::program_root().to_string_lossy(),
    )
}

/// `_source_version`：repo ⇒ `git describe --always --dirty`（拿不到再退 pyproject 版本）；
/// pip ⇒ 包 `__version__`；都没有 ⇒ `dev`。
fn source_version(root: &str, kind: &str) -> String {
    if kind == "pip" {
        let py = std::env::var("AIMAIL_PYTHON").unwrap_or_else(|_| "python3".to_string());
        if let Ok(o) = std::process::Command::new(py)
            .args([
                "-c",
                "import aimail;print(getattr(aimail,'__version__','0.0.0'))",
            ])
            .output()
        {
            if o.status.success() {
                let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if !v.is_empty() {
                    return v;
                }
            }
        }
        return "0.0.0".to_string();
    }
    if let Ok(o) = std::process::Command::new("git")
        .args(["-C", root, "describe", "--always", "--dirty"])
        .output()
    {
        if o.status.success() {
            let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if !v.is_empty() && v != "dev" {
                return v;
            }
        }
    }
    pyproject_version(root).unwrap_or_else(|| "dev".to_string())
}

/// `<root>/../pyproject.toml` 的 `[project].version`（快照形态的版本来源）。
fn pyproject_version(root: &str) -> Option<String> {
    let p = std::path::Path::new(root).parent()?.join("pyproject.toml");
    let text = std::fs::read_to_string(p).ok()?;
    let mut in_project = false;
    for line in text.lines() {
        let t = line.trim();
        if t.starts_with('[') {
            in_project = t == "[project]";
            continue;
        }
        if in_project {
            if let Some(v) = t.strip_prefix("version") {
                let v = v.trim_start_matches([' ', '=']).trim().trim_matches('"');
                if !v.is_empty() {
                    return Some(v.to_string());
                }
            }
        }
    }
    None
}

/// Python `list` 的 repr（错误/清理文案逐字用）：`['a', 'b']`。
fn py_list(items: &[&str]) -> String {
    let quoted: Vec<String> = items.iter().map(|s| format!("'{s}'")).collect();
    format!("[{}]", quoted.join(", "))
}

fn py_list_owned(items: &[String]) -> String {
    let refs: Vec<&str> = items.iter().map(|s| s.as_str()).collect();
    py_list(&refs)
}

/// 文件 md5（分块读，等价 Python `_md5`）。
fn md5_file(path: &std::path::Path) -> String {
    use md5::{Digest, Md5};
    let mut h = Md5::new();
    if let Ok(mut f) = std::fs::File::open(path) {
        let mut buf = [0u8; 65536];
        use std::io::Read;
        while let Ok(n) = f.read(&mut buf) {
            if n == 0 {
                break;
            }
            h.update(&buf[..n]);
        }
    }
    format!("{:x}", h.finalize())
}

use serde_json::Value;

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
