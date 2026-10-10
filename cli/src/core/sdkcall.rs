//! CLI 侧的**通用 SDK 按名调用**（owner 2026-10-04 口径：SDK 冻结，glue 归 CLI）。
//!
//! 与 `core/sdk.rs` 的关系（两者都是"进程命令契约"，不是两套协议）：
//! · `sdk.rs`  = 调**已发布的 SDK 可执行门** `python -m aimail.sdk_ops <op>`（op 清单在 SDK 侧，
//!               已随 v0.1.35 发布并登记在边界稿 §1.5；**不再扩 op**）。
//! · 本模块    = 按名调用 SDK **已发布的公共函数**（`<module>.<function>(**kwargs)`），
//!               shim 随本二进制内嵌 ⇒ CLI 侧能力扩展**零 SDK 改动、零发版**。
//!
//! 边界（硬）：shim 只做"取参数 → 调函数 → 回信封"；**任何算法都不许在这里复刻**，
//! 名字清单仍以边界稿 B-3 为准。

use serde_json::Value;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::core::sdk::AbiError;

/// 内嵌 shim 源（CLI 域文件，随二进制发布；不依赖部署机上另有一份脚本）。
const SHIM_SRC: &str = include_str!("sdk_call_shim.py");

/// shim 的调用形状：`python3 -c <shim> <core_dir> <module> <function> <kwargs-json>`。
pub fn shim_argv(core_dir: &Path, module: &str, function: &str, kwargs: &Value) -> Vec<String> {
    shim_argv_full(core_dir, module, function, kwargs, &[])
}

/// 带**位置参数**的形态（有些 SDK 函数按位置传：hermes 适配层 `(name, profile_dir, config)`）。
pub fn shim_argv_full(
    core_dir: &Path,
    module: &str,
    function: &str,
    kwargs: &Value,
    positional: &[Value],
) -> Vec<String> {
    vec![
        "-c".to_string(),
        SHIM_SRC.to_string(),
        core_dir.to_string_lossy().to_string(),
        module.to_string(),
        function.to_string(),
        serde_json::to_string(kwargs).unwrap_or_else(|_| "{}".into()),
        serde_json::to_string(positional).unwrap_or_else(|_| "[]".into()),
    ]
}

fn python_bin() -> String {
    // 定位链单真源 = `core::sdkroot::python_bin`（契约 §4.1(2)）
    crate::core::sdkroot::python_bin()
}

/// 调 `module.function(**kwargs)`：成功返回 SDK 的返回值；失败按 `usage|import|call` 分档。
pub fn call(
    module: &str,
    function: &str,
    kwargs: &Value,
    core_dir: &Path,
    timeout: Duration,
    env: &[(String, String)],
) -> Result<Value, AbiError> {
    let argv = shim_argv(core_dir, module, function, kwargs);
    let (stdout, _stderr, code) =
        crate::core::sdk::run_program(Path::new(&python_bin()), &argv, timeout, env)?;
    let text = String::from_utf8_lossy(&stdout);
    let line = text
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim();
    if line.is_empty() {
        return Err(AbiError::Transport(format!(
            "no JSON on stdout (exit {code})"
        )));
    }
    let v: Value = serde_json::from_str(line)
        .map_err(|e| AbiError::Transport(format!("stdout is not one-line JSON: {e}")))?;
    let ok = v.get("ok").and_then(Value::as_bool).unwrap_or(false);
    if ok {
        return Ok(v.get("result").cloned().unwrap_or(Value::Null));
    }
    let kind = v.get("kind").and_then(Value::as_str).unwrap_or("");
    let exc = v
        .get("exc")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let err = v
        .get("error")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    match kind {
        "usage" => Err(AbiError::Usage(err)),
        "import" => Err(AbiError::Import(err)),
        _ => Err(AbiError::Call { exc, msg: err }),
    }
}

/// 带位置参数的调用（其余同 `call`）。
pub fn call_positional(
    module: &str,
    function: &str,
    positional: &[Value],
    kwargs: &Value,
    core_dir: &Path,
    timeout: Duration,
    env: &[(String, String)],
) -> Result<Value, AbiError> {
    let argv = shim_argv_full(core_dir, module, function, kwargs, positional);
    let (stdout, _stderr, _code) =
        crate::core::sdk::run_program(Path::new(&python_bin()), &argv, timeout, env)?;
    parse_envelope(&stdout)
}

fn parse_envelope(stdout: &[u8]) -> Result<Value, AbiError> {
    let text = String::from_utf8_lossy(stdout);
    let line = text
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("")
        .trim();
    if line.is_empty() {
        return Err(AbiError::Transport("no JSON on stdout".into()));
    }
    let v: Value = serde_json::from_str(line)
        .map_err(|e| AbiError::Transport(format!("stdout is not one-line JSON: {e}")))?;
    if v.get("ok").and_then(Value::as_bool).unwrap_or(false) {
        return Ok(v.get("result").cloned().unwrap_or(Value::Null));
    }
    let kind = v.get("kind").and_then(Value::as_str).unwrap_or("");
    let exc = v
        .get("exc")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let err = v
        .get("error")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    match kind {
        "usage" => Err(AbiError::Usage(err)),
        "import" => Err(AbiError::Import(err)),
        _ => Err(AbiError::Call { exc, msg: err }),
    }
}

/// **测试/自证用**：把 shim 源落成一个临时脚本（部署形态下由 CLI 内嵌，不需要落盘）。
pub fn materialize_shim(dir: &Path) -> std::io::Result<PathBuf> {
    let p = dir.join("sdk_call_shim.py");
    std::fs::write(&p, SHIM_SRC)?;
    Ok(p)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn core_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("repo root")
            .join("pysdk")
    }

    #[test]
    fn calls_a_published_sdk_function_by_name() {
        // 真调（纯算法、离线）：定名规则在 SDK 内，CLI 只取计划
        let plan = call(
            "aimail_base",
            "plan_address_name",
            &json!({
                "requested_name": "billing",
                "agent_id": "agent",
                "domain": "example.test",
                "register_argv": ["x", "--name", "{name}"]
            }),
            &core_dir(),
            Duration::from_secs(60),
            &[],
        )
        .expect("plan_address_name 应可用（SDK 已发布按名可调）");
        assert_eq!(plan["target_name"], json!("billing"));
        assert_eq!(plan["needs_rename"], json!(false));
        assert_eq!(plan["email"], json!("billing@example.test"));
    }

    #[test]
    fn sdk_algorithm_errors_are_reported_not_swallowed() {
        // 非法名 ⇒ SDK 抛 ValueError ⇒ 分档 call + 异常名原样上报（不臆断成成功）
        let err = call(
            "aimail_base",
            "plan_address_name",
            &json!({"requested_name": "has.dot", "agent_id": "agent", "domain": "example.test"}),
            &core_dir(),
            Duration::from_secs(60),
            &[],
        )
        .expect_err("非法名必须报错");
        match err {
            AbiError::Call { exc, .. } => assert_eq!(exc, "ValueError"),
            other => panic!("应为 call 档: {other:?}"),
        }
    }

    #[test]
    fn unknown_function_is_an_import_error() {
        let err = call(
            "aimail_base",
            "no_such_function_xyz",
            &json!({}),
            &core_dir(),
            Duration::from_secs(60),
            &[],
        )
        .expect_err("未知函数必须报错");
        assert!(
            matches!(err, AbiError::Import(_)),
            "应为 import 档: {err:?}"
        );
    }
}
