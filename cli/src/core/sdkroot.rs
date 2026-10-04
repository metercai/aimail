//! **SDK 根定位** —— CLI 里**唯一**知道"SDK 在哪、什么形态"的地方（C 的统一点）。
//!
//! 口径（owner 2026-10-04）：**rust CLI = 纯二进制命令行，自身零环境依赖**；一切"对环境有依赖"
//! 的代码归 SDK（pysdk→python / tssdk→node）。因此 CLI 只做两件事：
//! ①把**逻辑入口名**（注册表里 `hermes/register_profiles.py` 这类 SDK 根相对路径）落成实际调用；
//! ②起一个 SDK 执行进程。**布局知识不许散落在别处**——其余模块一律问本模块。
//!
//! 两种形态（值相同、根不同、调用方式相同 —— 把 SDK 根塞进 `sys.path`，扁平模块名在两种布局都成立）：
//! · `repo`：工具包/仓库快照 `{program_root}/aimail-src/pysdk`（与 CLI 同一棵树时"同源"）
//! · `pip`：已安装的 `aimail` 包目录（`python -c "import aimail;print(dirname(aimail.__file__))"`）

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
        kind: "repo",
    })
}

/// 解释器（`AIMAIL_PYTHON` > `python3`）—— 起 SDK 执行进程时用。
pub fn python_bin() -> String {
    std::env::var("AIMAIL_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// 工具包/仓库快照形态的**候选**路径（唯一拼法；`resolve` 与"同源优先"判定共用）。
pub fn repo_candidate() -> PathBuf {
    crate::core::home::program_root()
        .join("aimail-src")
        .join("pysdk")
}

/// 无显式值时的定位：**工具包/仓库快照优先**（与 CLI 同源），否则问解释器要 pip 包目录。
pub fn resolve() -> Result<SdkRoot, String> {
    let repo = repo_candidate();
    if is_sdk_root(&repo) {
        return Ok(SdkRoot {
            path: repo,
            kind: "repo",
        });
    }
    let out = std::process::Command::new(python_bin())
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

/// 便利：`resolve()` 失败时退化为"工具包快照路径"（用于只播报/记录、不立即执行的场景，
/// 如 install 的 `{sdk}` 上下文）。**不要**用它去决定"能不能跑"。
pub fn resolve_or_repo_candidate() -> SdkRoot {
    match resolve() {
        Ok(r) => r,
        Err(_) => SdkRoot {
            path: repo_candidate(),
            kind: "repo",
        },
    }
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
