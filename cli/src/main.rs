//! 薄入口：全部实现在 lib（`aimail::app::run`），这里只把退出码交给进程。

use std::process::ExitCode;

fn main() -> ExitCode {
    let code = aimail::app::run();
    ExitCode::from(u8::try_from(code).unwrap_or(1))
}
