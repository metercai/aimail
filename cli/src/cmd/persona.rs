//! `aimail persona` — merged into `welcome` (2026-09-22); here it only does an **explicit signpost**.
//!
//! Verbatim replication of Python side `cli/aimail:403-412`: three lines of stderr text + **rc 2**.
//! `2` is not "ran it wrong" but "this command name is kept only as a signpost, don't go here" — in the contract surface
//! `persona` is still one of the 15 top-level subcommands (guidance shell), so it must exist as-is.

pub fn run() -> i32 {
    eprintln!("✗ `aimail persona` has been merged into `aimail welcome`(2026-09-22)");
    eprintln!("  Use instead: aimail welcome -s <system-id> [--persona '<draft>' --signature '<signature>']");
    eprintln!("  (when --persona/--signature are omitted, welcome will attempt to parse from agent reply)");
    2
}

#[cfg(test)]
mod tests {
    #[test]
    fn pointer_shell_returns_two() {
        // exit code is contract (Python `cmd_persona` last line `return 2`)
        assert_eq!(super::run(), 2);
    }
}
