//! `aimail persona` —— 已并入 `welcome`（2026-09-22），此处只做**明确指引**。
//!
//! 逐字复刻 Python 侧 `cli/aimail:403-412`：三行 stderr 文案 + **rc 2**。
//! `2` 不是"跑错了"，而是"这个命令名保留只为指路，别再走这里"——契约面里
//! `persona` 仍是 15 个顶层子命令之一（指路壳），所以必须原样存在。

pub fn run() -> i32 {
    eprintln!("✗ `aimail persona` 已合并到 `aimail welcome`(2026-09-22)");
    eprintln!("  改用: aimail welcome -s <system-id> [--persona '<草案>' --signature '<签名>']");
    eprintln!("  (省略 --persona/--signature 时, welcome 会尝试从 agent 回复解析)");
    2
}

#[cfg(test)]
mod tests {
    #[test]
    fn pointer_shell_returns_two() {
        // 退出码是契约（Python `cmd_persona` 末行 `return 2`）
        assert_eq!(super::run(), 2);
    }
}
