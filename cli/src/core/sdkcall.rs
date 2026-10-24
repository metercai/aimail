//! **generic SDK call-by-name** on the CLI side (owner 2026-10-04 ruling: SDK frozen, glue belongs to CLI).
//!
//! Relationship with `core/sdk.rs` (both are "process command contracts", not two separate protocols):
//! · `sdk.rs`  = invokes the **published SDK executable gate** `python -m aimail.sdk_ops <op>` (op list is on the SDK side,
//!               already published with v0.1.35 and registered in the boundary draft §1.5; **no more ops added**).
//! · This module = invokes SDK **published public functions** by name (`<module>.<function>(**kwargs)`),
//!               shim is embedded in this binary ⇒ CLI-side capability expansion requires **zero SDK changes, zero releases**.
//!
//! Boundary (hard): shim only does "get params → call function → return envelope"; **no algorithm may be replicated here**,
//! the name list still follows boundary draft B-3.

use serde_json::Value;
use std::path::Path;
use std::time::Duration;

use crate::core::sdk::AbiError;

/// Embedded shim source (CLI domain file, shipped with the binary; does not depend on a separate script on the deployment machine).
const SHIM_SRC: &str = include_str!("sdk_call_shim.py");

/// Shim invocation shape: `python3 -c <shim> <core_dir> <module> <function> <kwargs-json>`.
pub fn shim_argv(core_dir: &Path, module: &str, function: &str, kwargs: &Value) -> Vec<String> {
    shim_argv_full(core_dir, module, function, kwargs, &[])
}

/// Shape with **positional arguments** (some SDK functions take positional args: hermes adapter layer `(name, profile_dir, config)`).
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
    // Single source of truth for location chain = `core::sdkroot::python_bin` (contract §4.1(2))
    crate::core::sdkroot::python_bin()
}

/// Call `module.function(**kwargs)`: returns the SDK's return value on success; failure is categorized as `usage|import|call`.
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

/// Call with positional arguments (rest same as `call`).
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;

    fn core_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("repo root")
            .join("pysdk")
    }

    #[test]
    fn calls_a_published_sdk_function_by_name() {
        // Real call (pure algorithm, offline): naming rules are in the SDK, CLI only fetches the plan
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
        .expect("plan_address_name should be available (SDK published callable by name)");
        assert_eq!(plan["target_name"], json!("billing"));
        assert_eq!(plan["needs_rename"], json!(false));
        assert_eq!(plan["email"], json!("billing@example.test"));
    }

    #[test]
    fn sdk_algorithm_errors_are_reported_not_swallowed() {
        // Invalid name ⇒ SDK raises ValueError ⇒ tiered call + exception name reported as-is (do not assume success)
        let err = call(
            "aimail_base",
            "plan_address_name",
            &json!({"requested_name": "has.dot", "agent_id": "agent", "domain": "example.test"}),
            &core_dir(),
            Duration::from_secs(60),
            &[],
        )
        .expect_err("Invalid name must raise an error");
        match err {
            AbiError::Call { exc, .. } => assert_eq!(exc, "ValueError"),
            other => panic!("Should be call tier: {other:?}"),
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
        .expect_err("Unknown function must raise an error");
        assert!(
            matches!(err, AbiError::Import(_)),
            "Should be import tier: {err:?}"
        );
    }
}
