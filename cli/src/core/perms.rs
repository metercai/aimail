//! 凭据文件权限 —— 跨平台助手。
//!
//! 现状码对照：Python 各写点用 `os.chmod(path, 0o600)`（例如
//! `cli/setup_system.py:370`、`cli/aimail:2464`），且普遍包在 `try/except OSError` 里
//! ⇒ **尽力而为**：失败不中断主流程。
//!
//! 平台差异（有意，不是遗漏）：POSIX 权限位在 Windows 上不存在（`os.chmod` 只切换
//! 只读位）⇒ 非 unix 平台本模块是 no-op，把"用户私有"交给安装目录的继承 ACL；
//! 这与 Python 侧在 Windows 上的实际效果一致，不是功能缺失。

use std::io;
use std::path::Path;

/// 把文件设为**仅属主可读写**（unix: 0600；非 unix: no-op）。
///
/// 调用方按 Python 现状同样**忽略失败**（凭据文件的权限收紧不是主流程的前置条件）。
#[cfg(unix)]
pub fn set_user_only(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
}

/// 见 unix 版本的说明：Windows 无 POSIX 权限位，本调用是 no-op。
#[cfg(not(unix))]
pub fn set_user_only(_path: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn set_user_only_tightens_permissions() {
        let tmp = TempDir::new("perms");
        let f = tmp.path().join("cred.json");
        std::fs::write(&f, b"{}").unwrap();
        set_user_only(&f).expect("chmod");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&f).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "want 0600, got {mode:o}");
        }
        #[cfg(not(unix))]
        {
            assert!(
                f.is_file(),
                "no-op on non-unix must still leave the file intact"
            );
        }
    }
}
