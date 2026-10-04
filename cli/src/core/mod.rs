//! 核心基础设施层（plan v3 S2 起逐模块填充）。
//!
//! | 模块 | 职责 | 现状码对照 |
//! |---|---|---|
//! | [`home`] | 主根 / 程序根 / 发布形态 | `cli/runtime_core.py:161-171`、`pysdk/aimail_base.aimail_home()` |
//! | [`perms`] | 凭据文件权限（跨平台助手） | Python 各写点的 `os.chmod(..., 0o600)` |
//! | [`contract`] | agent 侧契约常量（编译期内嵌单真源） | `contract/aimail-contract.json` |
//! | [`platforms`] | 平台注册表（内嵌 `cli/platforms.json`） | `cli/platforms.json` |
//! | [`config`] | 系统级环境文件读写（CLI 独占写权）+ 绑定文件只读入口 | `cli/setup_system.py:331-370`、`cli/aimail:2460-2467` |
//!
//! 未落的还有 `gateway` / `smtp`（HTTP/SMTP 客户端）与 `proc`：它们要靠 S3/S6 的
//! 真实消费方才有意义，先落会变成"零调用方的死代码 + 无谓依赖"（最小机制原则）。

pub mod bridge;
pub mod check;
pub mod checks;
pub mod config;
pub mod contract;
pub mod gateway;
pub mod home;
pub mod http;
pub mod mail;
pub mod payload;
pub mod perms;
pub mod platforms;
pub mod probe;
pub mod pyjson;
pub mod repair;
pub mod sdk;
pub mod sig;
pub mod style;
pub mod time;

#[cfg(test)]
pub(crate) mod testutil {
    //! 极小临时目录助手 —— 不引第三方 crate（tempfile），够用即止。
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU32, Ordering};

    static SEQ: AtomicU32 = AtomicU32::new(0);

    /// `<系统临时目录>/aimail-rs-test-<pid>-<seq>-<tag>`，Drop 时递归删除。
    pub struct TempDir {
        path: PathBuf,
    }

    impl TempDir {
        pub fn new(tag: &str) -> Self {
            let seq = SEQ.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "aimail-rs-test-{}-{}-{}",
                std::process::id(),
                seq,
                tag
            ));
            std::fs::create_dir_all(&path).expect("create temp dir");
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
}
