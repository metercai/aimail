//! `aimail install` —— 集成入口。本切片（S6 切片1）只落**机器面分派 + `--payload` 通道**：
//!
//! · `--system-only` / `--payload` 的**互斥与拒参**（`cli/aimail:784-827` 逐字复刻：文案、流向、
//!   rc=2；其中 payload 通道的"未知动作/缺 bundle"文案走 **stdout**，拒参走 stderr —— 与 Python 一致）；
//! · `--payload dir|source|resource` 三个**只读**动作（路径解析，可跨语言逐字比对）；
//! · `--payload install`（写载荷，含 stamp/清理）与 `--system-only`（L1 激活/复用，单行 JSON ABI）
//!   以及人路径**一律未移植**：明确的非零退出 + 说明，绝不静默当成功、绝不降级成人路径。
//!
//! 未移植清单在 `cli/tests/cli_surface.rs::unported_faces_are_honest` 里钉住。

use crate::cmd::stub::not_yet_ported;
use crate::core::payload;

/// `install` 的全部参数（人面 + 隐藏机器面；未定义的值一律空串/`false`）。
#[derive(Default, Clone)]
pub struct Args {
    pub home: String,
    pub system_id: String,
    pub product_code: String,
    pub admin_key: String,
    pub gateway_url: String,
    pub system_name: String,
    pub manager: String,
    pub domain: String,
    pub all_agents: bool,
    pub system_only: bool,
    pub payload: String,
    pub payload_name: String,
    pub dest: String,
    pub source_root: String,
    pub force: bool,
}

pub fn run(a: Args) -> i32 {
    // ── 机器面分派（互斥；并拒绝另一侧的参数 —— 绝不静默吞掉）─────────────────
    if a.system_only && !a.payload.is_empty() {
        eprintln!("ERROR: --system-only 与 --payload 互斥");
        return 2;
    }
    if !a.payload.is_empty() {
        // payload 通道只认自身参数；激活/接线类参数一律拒（exit 2）
        let rejects: [(&str, bool); 9] = [
            ("-H", !a.home.is_empty()),
            ("-s", !a.system_id.is_empty()),
            ("-c", !a.product_code.is_empty()),
            ("-k", !a.admin_key.is_empty()),
            ("-g", !a.gateway_url.is_empty()),
            ("-n", !a.system_name.is_empty()),
            ("-m", !a.manager.is_empty()),
            ("-d", !a.domain.is_empty()),
            ("--all-agents", a.all_agents),
        ];
        for (flag, present) in rejects {
            if present {
                eprintln!("ERROR: --payload 通道不接受 {flag}");
                return 2;
            }
        }
        return run_payload(&a.payload, &a.payload_name, &a.dest, &a.source_root);
    }
    if a.system_only {
        // system-only 是 L1-only 铁律入口：接线/捆绑两侧的参数都拒
        let rejects: [(&str, bool); 5] = [
            ("--all-agents", a.all_agents),
            ("--dest", !a.dest.is_empty()),
            ("--source-root", !a.source_root.is_empty()),
            ("--force", a.force),
            ("payload operand", !a.payload_name.is_empty()),
        ];
        for (flag, present) in rejects {
            if present {
                eprintln!("ERROR: --system-only 通道不接受 {flag}");
                return 2;
            }
        }
        return not_yet_ported("install --system-only (L1 activation/reuse, one-line JSON ABI)");
    }
    // 无机器面标志却带了机器面参数/操作数 → 显式拒绝（不静默吞拼写错误）
    let stray: [(&str, bool); 4] = [
        ("payload operand", !a.payload_name.is_empty()),
        ("--dest", !a.dest.is_empty()),
        ("--source-root", !a.source_root.is_empty()),
        ("--force", a.force),
    ];
    for (flag, present) in stray {
        if present {
            eprintln!("ERROR: {flag} 只在 --payload 通道有效");
            return 2;
        }
    }
    not_yet_ported("install (human path: activation / platform wiring / runtime resources)")
}

/// `cmd_payload`（`cli/aimail:304-338`）。
fn run_payload(action: &str, name: &str, dest: &str, source_root: &str) -> i32 {
    let name = name.trim();
    match action {
        "install" => {
            if name.is_empty() {
                // Python 此处走 stdout（`print`），不是 stderr —— 照抄流向
                println!(
                    "ERROR: payload install 需要 <bundle>(可选: {})",
                    payload::bundle_names()
                );
                return 2;
            }
            let _ = (dest, source_root);
            not_yet_ported("install --payload install (runtime bundle install)")
        }
        "dir" => {
            let bundle = if name.is_empty() { "mcp" } else { name };
            match payload::payload_dir_named(bundle) {
                Ok(p) => {
                    println!("{p}");
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "resource" => {
            if name.is_empty() {
                println!(
                    "ERROR: payload resource 需要 <name>\
                     (skills|board-role|board-role-zh|board-soul|board-soul-zh)"
                );
                return 2;
            }
            match payload::source_path(name, source_root) {
                Ok(p) => {
                    println!("{p}");
                    0
                }
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            }
        }
        "source" => match payload::resolve_source_root(source_root) {
            Ok((root, kind)) => {
                println!("{kind}\t{root}");
                0
            }
            Err(e) => {
                eprintln!("{e}");
                1
            }
        },
        _ => {
            println!("ERROR: 未知 payload 动作");
            2
        }
    }
}
