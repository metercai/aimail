//! Thin entry: all implementation lives in the lib (`aimail::app::run`); this
//! only hands the exit code to the process.

use std::process::ExitCode;

fn main() -> ExitCode {
    let code = aimail::app::run();
    ExitCode::from(u8::try_from(code).unwrap_or(1))
}
