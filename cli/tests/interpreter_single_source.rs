//! 静态棘轮（契约 v1.0 §4.1(2)）：宿主解释器定位链**单真源** ——
//! 只有 `src/core/sdkroot.rs` 可以读写 `AIMAIL_PYTHON`；其余模块一律经
//! `sdkroot::python_bin()` / `sdkroot::bind_domain*()`。
//!
//! 2026-10-12 owner 定案：deerflow 恢复面(`reset`/`uninstall`)拿 CLI 自己的 `python3` 去找 SDK、
//! 报"解释器 python3 下未定位到 aimail 包"（而 SDK 就装在宿主 venv 里）= "自检与目标系统不匹配"。
//! 病灶 = 第二条退化链（`setup::python_bin` = env→python3）+ 三处裸读 env。本棘轮防复发。

use std::path::{Path, PathBuf};

fn rust_sources(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    let mut subs: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
    subs.sort();
    for p in subs {
        if p.is_dir() {
            rust_sources(&p, out);
        } else if p.extension().and_then(|s| s.to_str()) == Some("rs") {
            out.push(p);
        }
    }
}

#[test]
fn interpreter_chain_has_exactly_one_implementation() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    rust_sources(&src, &mut files);
    assert!(files.len() > 50, "源码树扫描异常({} 个 .rs)", files.len());

    let needles = [
        "env::var(\"AIMAIL_PYTHON\")",
        "env::set_var(\"AIMAIL_PYTHON\"",
    ];
    let mut offenders = Vec::new();
    for f in files {
        if f.ends_with("core/sdkroot.rs") {
            continue; // 单真源本体
        }
        let Ok(text) = std::fs::read_to_string(&f) else {
            continue;
        };
        for (i, line) in text.lines().enumerate() {
            if needles.iter().any(|n| line.contains(n)) {
                offenders.push(format!("{}:{}", f.display(), i + 1));
            }
        }
    }
    assert!(
        offenders.is_empty(),
        "解释器定位链出现第二实现(应改走 sdkroot::python_bin()/bind_domain*): {offenders:?}"
    );
}
