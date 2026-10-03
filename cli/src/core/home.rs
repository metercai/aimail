//! 程序根 / 主根解析 —— 与 Python 侧同语义的单点。
//!
//! Python 真源：`cli/runtime_core.py:161-171`（`program_root()` / `toolkit_dir()`）
//! 与 `pysdk/aimail_base.aimail_home()`。Rust 侧不得在别处再拼 `~/.aimail/bin`。
//!
//! 分层：
//! - 主根（aimail_home）= `AIMAIL_HOME` 环境变量 > `~/.aimail`
//! - 程序根（program_root）= `AIMAIL_PROG_DIR` 环境变量 > `<主根>/bin`

use std::path::PathBuf;

fn env_nonempty(key: &str) -> Option<String> {
    match std::env::var(key) {
        Ok(v) if !v.trim().is_empty() => Some(v.trim().to_string()),
        _ => None,
    }
}

fn expand(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = env_nonempty("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    PathBuf::from(path)
}

/// 本机 aimail 主根：`AIMAIL_HOME` > `~/.aimail`。
pub fn aimail_home() -> PathBuf {
    match env_nonempty("AIMAIL_HOME") {
        Some(v) => expand(&v),
        None => expand("~/.aimail"),
    }
}

/// 本机程序根：`AIMAIL_PROG_DIR` > `<主根>/bin`（bootstrap 的安装落点）。
pub fn program_root() -> PathBuf {
    match env_nonempty("AIMAIL_PROG_DIR") {
        Some(v) => expand(&v),
        None => aimail_home().join("bin"),
    }
}

/// 发布形态：`dev`（仓内运行）| `bootstrapped`（已装进程序根）。
///
/// Python 侧判据是「脚本目录是否在 toolkit_dir 之下」（`cli/aimail:772`）；
/// Rust 是单文件二进制，等价判据 = 当前可执行文件是否落在程序根下。
pub fn release_mode() -> &'static str {
    let Ok(exe) = std::env::current_exe() else {
        return "dev";
    };
    if exe.starts_with(program_root()) {
        "bootstrapped"
    } else {
        "dev"
    }
}
