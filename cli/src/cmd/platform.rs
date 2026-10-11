//! `aimail platform` —— 检测面机器导出（契约 v1.0 §5）。
//!
//! `aimail platform spec [--home <dir>] [--json]`：
//!   · 无 `--home` ⇒ 打印平台静态知识表（常量三分里的"CLI 探测模块"部分）；
//!   · 带 `--home` ⇒ 附上对该 home 的探测结果（命中即平台/transport/agent 键/指针/入口；
//!     探测失败以 `{"error": "…"}` 呈现，禁用 `unknown` 冒充成功）。
//!
//! 为什么由 CLI 导出：门禁与夹具原先直读 `cli/platforms.json`（已退役）；单一真源仍是
//! `core::detect` 内的常量，本命令只是可断言的导出面（契约 §5）。

pub struct Args {
    pub home: Option<String>,
}

pub fn run(args: &Args) -> i32 {
    let home = args.home.as_deref().map(std::path::Path::new);
    let v = crate::core::detect::spec_json(home, None);
    match serde_json::to_string_pretty(&v) {
        Ok(s) => {
            println!("{s}");
            0
        }
        Err(e) => {
            eprintln!("platform spec: json encode failed: {e}");
            1
        }
    }
}
