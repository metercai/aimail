//! ② 运行时面：解析器 / SDK 根。
//!
//! 这是**唯一会起子进程**的事实面，故由调用方按需触发（`aimail address` / `stats`
//! 这类只读命令不得经过这里）。探测引擎仍在 [`crate::core::sdkroot`]：
//! 按 `--home` 的实际布局（env 覆盖 → 进程绑定 → 容器内 /proc 实测 → venv 布局 →
//! hermes PM `facts.json` → 嵌套 venv → 官方工具链）定位解释器，命中不存在就返回空、
//! 不猜。本模块只做门面与宿主语言分派。
//!
//! 包管理器没有 Rust 侧探测：uv → venv pip → ensurepip 的自适应写在宿主的安装步骤
//! shell 里（`hosts/*.rs` 的 `install_plan`），由目标环境自己判定。

use std::path::{Path, PathBuf};

use crate::core::sdkroot;
use crate::hosts::Host;

/// 域内解释器：python 宿主必要，node 宿主不需要（`{python}` 不出现在其步骤里）。
/// 顺带把结果绑定到进程本地态（`sdkroot::bind_domain`），python 门与子进程共用同一解释器。
pub fn python(h: &dyn Host, home: &Path, container: &str) -> String {
    if !h.uses_python() {
        return String::new();
    }
    sdkroot::bind_domain(container, &home.to_string_lossy())
}

/// 已装 SDK 的 core 目录（`install` 之后复核"包真的落在探测出的解释器里"）。
pub fn sdk_root(python: &str) -> Option<PathBuf> {
    if python.is_empty() {
        return None;
    }
    sdkroot::resolve_with(python).ok().map(|r| r.path)
}
