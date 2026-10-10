//! **SDK 根定位** —— CLI 里**唯一**知道"SDK 在哪、什么形态"的地方（C 的统一点）。
//!
//! 口径（owner 2026-10-04）：**rust CLI = 纯二进制命令行，自身零环境依赖**；一切"对环境有依赖"
//! 的代码归 SDK（pysdk→python / tssdk→node）。因此 CLI 只做两件事：
//! ①把**逻辑入口名**（注册表里 `hermes/register_profiles.py` 这类 SDK 根相对路径）落成实际调用；
//! ②起一个 SDK 执行进程。**布局知识不许散落在别处**——其余模块一律问本模块。
//!
//! **唯一形态**（owner 2026-10-06 定稿）：`pip` = 已安装的 `aimail` 包目录
//! （`python -c "import aimail;print(dirname(aimail.__file__))"`）—— **不存在** `aimail-src/`
//! 仓库快照态，也不存在"仓库态优先"的隐式或显式分支 ✗；资源一律取自**安装包** ✓。

use std::path::{Path, PathBuf};

/// 已定位的 SDK 根。`kind` ∈ `repo` | `pip`（与 Python 侧 `resolve_source_root` 同词表）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SdkRoot {
    pub path: PathBuf,
    pub kind: &'static str,
}

/// 判定一个目录是否为"SDK 根"（标志物：`aimail_base.py`，两种形态都直接放在根下）。
pub fn is_sdk_root(dir: &Path) -> bool {
    dir.join("aimail_base.py").is_file()
}

/// 显式给定的根（`--source-root` / 调用方指定）：必须真的含 `aimail_base.py`，否则报错。
pub fn at(explicit: &str) -> Result<SdkRoot, String> {
    let root = crate::core::home::abs_path(&crate::core::home::expand_user(explicit));
    if !is_sdk_root(&root) {
        return Err(format!(
            "ERROR: --source-root 无效(无 aimail_base.py): {}",
            root.to_string_lossy()
        ));
    }
    Ok(SdkRoot {
        path: root,
        // owner 2026-10-06：只有一种形态（pip 已装包）⇒ `kind` 恒为 "pip"
        kind: "pip",
    })
}

/// 探测本机正在运行的 agent 所用解释器（要求该 venv 内确有 aimail ⇒ 不误采无关 venv）
pub fn probe_agent_python() -> Option<String> {
    let rd = std::fs::read_dir("/proc").ok()?;
    for e in rd.flatten() {
        let Ok(s) = std::fs::read(e.path().join("cmdline")) else {
            continue;
        };
        for tok in s.split(|b| *b == 0) {
            let t = String::from_utf8_lossy(tok).to_string();
            if !t.contains("/bin/python") {
                continue;
            }
            let pb = std::path::Path::new(&t);
            if !pb.is_file() {
                continue;
            }
            if let Some(venv) = pb.parent().and_then(|b| b.parent()) {
                if let Ok(ls) = std::fs::read_dir(venv.join("lib")) {
                    if ls
                        .flatten()
                        .any(|d| d.path().join("site-packages/aimail").is_dir())
                    {
                        return Some(t);
                    }
                }
            }
        }
    }
    None
}

const PROBE_SH: &str = r#"for c in /proc/[0-9]*/cmdline; do tr "\0" "\n" <"$c" 2>/dev/null; done | grep -E "/bin/python" | sort -u | while read -r x; do "$x" -c "import aimail" 2>/dev/null && { echo "$x"; exit 0; }; done;"#;

/// 域内探测：容器 ⇒ docker exec；宿主 ⇒ 候选逐个试 "能 import aimail"
pub fn probe_in_domain(container: &str, home: &str) -> String {
    if !container.is_empty() {
        if let Ok(o) = std::process::Command::new("docker")
            .args(["exec", container, "sh", "-c", PROBE_SH])
            .output()
        {
            let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if !v.is_empty() {
                return v;
            }
        }
    }
    for rel in ["bin/python3", "venv/bin/python3", ".venv/bin/python3"] {
        let c = std::path::Path::new(home).join(rel);
        if c.is_file() {
            return c.to_string_lossy().to_string();
        }
    }
    if let Ok(o) = std::process::Command::new("sh")
        .env("AIMAIL_HOME", home)
        .current_dir(if home.is_empty() { "/" } else { home })
        .args(["-c", PROBE_SH])
        .output()
    {
        let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if !v.is_empty() {
            return v;
        }
    }
    probe_agent_python().unwrap_or_else(|| "python3".to_string())
}

/// 解释器（`AIMAIL_PYTHON` > `python3`）—— 起 SDK 执行进程时用。
pub fn python_bin() -> String {
    // 契约 v1.0 §4.1(2) 的**定位链**（单真源，三处消费：本模块解析、`sdkcall` 按名调用、`sdk` 门）：
    // `$AIMAIL_PYTHON` → 宿主 venv 探测（运行中且能 `import aimail` 的 agent 解释器）→ PATH `python3`。
    if let Ok(p) = std::env::var("AIMAIL_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Some(p) = probe_agent_python() {
        return p;
    }
    "python3".to_string()
}

/// SDK 根定位：**只有 pip 已装包一种形态**（owner 2026-10-06：坚决彻底 —— 不存在 `aimail-src/`、
/// 不存在"仓库态"✗；资源一律取自**安装包**）。隐式与显式都不例外。
pub fn resolve() -> Result<SdkRoot, String> {
    resolve_with(&python_bin())
}

pub fn resolve_with(python: &str) -> Result<SdkRoot, String> {
    let out = std::process::Command::new(python)
        .args([
            "-c",
            "import aimail,os;print(os.path.dirname(os.path.abspath(aimail.__file__)))",
        ])
        .output();
    if let Ok(o) = out {
        if o.status.success() {
            let dir = String::from_utf8_lossy(&o.stdout).trim().to_string();
            let p = PathBuf::from(&dir);
            if !dir.is_empty() && is_sdk_root(&p) {
                return Ok(SdkRoot {
                    path: p,
                    kind: "pip",
                });
            }
        }
    }
    Err("ERROR: 运行时源未找到(pip aimail 未安装且仓库 pysdk/ 缺失)".to_string())
}

/// 注册表"SDK 根相对入口" → 绝对路径。**唯一**把逻辑名落成路径的地方。
pub fn entry(root: &SdkRoot, rel: &str) -> PathBuf {
    root.path.join(rel)
}

/// 同上，`&Path` 形态（已拿到根路径的调用方复用，避免各处再拼）。
pub fn entry_path(root: &Path, rel: &str) -> PathBuf {
    root.join(rel)
}

/// 便利：解析失败时的**可播报**形态（只用于日志/`{sdk}` 上下文，**不要**用它决定"能不能跑"）。
/// owner 2026-10-06：不再退化到任何 `aimail-src/` 快照路径 ✗（该目录不存在也不应存在）。
pub fn resolve_or_placeholder() -> SdkRoot {
    match resolve() {
        Ok(r) => r,
        Err(e) => {
            // owner 2026-10-06：**不存在**仓库态/自包含载荷 ⇒ 解析不到 = "SDK 未安装" ⇒ 必须**响亮失败** ✗
            eprintln!("✗ SDK 未安装或不可用：{e}");
            eprintln!("  请先安装最新已发布版：pip install --index-url https://pypi.org/simple/ aimailsdk");
            std::process::exit(2);
        }
    }
}

/// 对探测所得解释器**实测**包管理器：uv 可用 ⇒ uv；否则该解释器的 pip 可用 ⇒ pip；否则空
pub fn probe_pkgmgr(container: &str, python: &str) -> String {
    let run = |cmd: &str| -> bool {
        let mut c = if container.is_empty() {
            std::process::Command::new("sh")
        } else {
            let mut d = std::process::Command::new("docker");
            d.args(["exec", container, "sh"]);
            d
        };
        c.args(["-c", cmd])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    };
    if run(&format!("command -v uv >/dev/null 2>&1 && uv pip --version >/dev/null 2>&1 || uv pip install --python {} --dry-run aimailsdk >/dev/null 2>&1", python)) {
        return "uv".to_string();
    }
    if run(&format!("{} -m pip --version >/dev/null 2>&1", python)) {
        return "pip".to_string();
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repo_layout_entry_resolution() {
        // 仓库布局（本仓）：SDK 根 = <prog>/aimail-src/pysdk ⇒ 两个 SDK 根相对入口直接可落
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("repo root")
            .join("pysdk");
        assert!(is_sdk_root(&repo), "仓库 pysdk 应含 aimail_base.py");
        for rel in [
            "hermes/register_profiles.py",
            "deer-flow/manage.py",
            "install.py",
        ] {
            let p = entry(
                &SdkRoot {
                    path: repo.clone(),
                    kind: "repo",
                },
                rel,
            );
            assert!(
                p.is_file(),
                "SDK 根相对入口应可直接落成文件: {}",
                p.display()
            );
            // 关键：条目**不含**仓库前缀 ⇒ 与 pip 形态（aimail/<rel>）同构
            assert!(
                !rel.starts_with("pysdk/"),
                "注册表条目不许带仓库前缀: {rel}"
            );
        }
    }

    #[test]
    fn explicit_root_must_be_a_sdk_root() {
        let tmp = tempfile::tempdir().unwrap();
        let err = at(&tmp.path().to_string_lossy()).unwrap_err();
        assert!(
            err.contains("--source-root 无效(无 aimail_base.py)"),
            "{err}"
        );
    }
}
