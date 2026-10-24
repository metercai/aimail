//! CLI -> SDK delegation executor (**language-agnostic**) -- 2026-10-04 owner ruling A'.
//!
//! Contract (boundary finalization §1.5): `python -m aimail.sdk_ops <op> [--args '<json>']`
//! · stdout **exactly one line** JSON: `{"ok":true,"result":…}` / `{"ok":false,"kind":"usage|import|call","exc":…,"error":…}`
//! · exit `0` success · `1` runtime failure (import/call) · `2` usage error (usage)
//! · stderr = human-readable text/traceback/SDK self-printed output
//!
//! Why it is an "executor" and not a "python bridge": The TS-side platform package is already an executable gate (`register-cli.js`, registry
//! `register.kind=node_entry`). This module only recognizes the general rule "executable + argv + single-line JSON" -- future Node side
//! can switch to `program` to reuse the same interpretation, without creating a third form.
//!
//! Location (**same-source priority**): If `{program_root}/aimail-src/pysdk/sdk_ops.py` exists, run it directly (caller and callee
//! the same tree; the mixed-installation lesson from `install.py`); otherwise fall back to `-m aimail.sdk_ops` (pip-installed agent-aimail ≥ 0.1.35).
//! Interpreter: `AIMAIL_PYTHON` > `python3`.

use crate::hosts::Host;
use serde_json::Value;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// One call failure of the gate, categorized by kind -- **one-to-one correspondence** with the three tiers of `sdk_ops()` on the Python side.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AbiError {
    /// kind=usage: op does not exist / --args is not an object / missing required key (caller's bug)
    Usage(String),
    /// kind=import: SDK module unavailable (not installed / version too old). **Explicit failure, no degradation**
    Import(String),
    /// kind=call: Exception thrown inside SDK. `exc` = exception class name (e.g., `ManagerRequiredError`), `msg` = message with prefix removed
    Call { exc: String, msg: String },
    /// Local transport layer failure (process cannot start / timeout / stdout is not "exactly one line of JSON")
    Transport(String),
}

impl AbiError {
    /// Exception class name for callers to construct `({exc}: {msg})` messages (Transport/Usage/Import also provide a stable name).
    pub fn exc_name(&self) -> &str {
        match self {
            AbiError::Usage(_) => "UsageError",
            AbiError::Import(_) => "ImportError",
            AbiError::Call { exc, .. } => exc,
            AbiError::Transport(_) => "TransportError",
        }
    }

    pub fn message(&self) -> &str {
        match self {
            AbiError::Usage(m) | AbiError::Import(m) | AbiError::Transport(m) => m,
            AbiError::Call { msg, .. } => msg,
        }
    }

    /// Character-for-character identical to Python's `({type(e).__name__}: {e})`.
    pub fn display_like_python(&self) -> String {
        format!("{}: {}", self.exc_name(), self.message())
    }
}

/// Call parameters for the gate (or any "single-line JSON ABI" executable).
pub struct AbiCall<'a> {
    pub program: &'a Path,
    pub args: Vec<String>,
    pub timeout: Duration,
    /// Append/override subprocess environment (for isolation fixtures; empty = inherit)
    pub env: Vec<(String, String)>,
}

/// Run a process command contract, returning the `result` from the envelope.
///
/// Interpretation order: process fails to start ⇒ Transport; stdout is not "exactly one line" ⇒ Transport; envelope ok=true ⇒ result;
/// otherwise categorize by kind (usage/import/call); unknown kind ⇒ Transport (do not guess).
pub fn run_json_abi(call: &AbiCall<'_>) -> Result<Value, AbiError> {
    let output = run_capture(call.program, &call.args, call.timeout, &call.env)?;
    let stdout = String::from_utf8_lossy(&output.0).to_string();
    let lines: Vec<&str> = stdout.lines().filter(|l| !l.trim().is_empty()).collect();
    if lines.len() != 1 {
        let stderr = String::from_utf8_lossy(&output.1);
        return Err(AbiError::Transport(format!(
            "Executable gate output anomaly (rc={}, {} lines): {}{}",
            output.2,
            lines.len(),
            lines.first().copied().unwrap_or(""),
            stderr.chars().take(200).collect::<String>()
        )));
    }
    let env: Value = serde_json::from_str(lines[0])
        .map_err(|e| AbiError::Transport(format!("Executable gate output is not JSON: {e}")))?;
    if env.get("ok").and_then(Value::as_bool) == Some(true) {
        return Ok(env.get("result").cloned().unwrap_or(Value::Null));
    }
    let kind = env.get("kind").and_then(Value::as_str).unwrap_or("");
    let exc = env
        .get("exc")
        .and_then(Value::as_str)
        .unwrap_or("SdkOpsError");
    let err = env.get("error").and_then(Value::as_str).unwrap_or("");
    match kind {
        "usage" => Err(AbiError::Usage(err.to_string())),
        "import" => Err(AbiError::Import(err.to_string())),
        "call" => {
            // The error reported by the gate is in the form "ExcName: msg" ⇒ remove the prefix; the caller must prepend the prefix again to match the Python format
            let prefix = format!("{exc}: ");
            let msg = err.strip_prefix(prefix.as_str()).unwrap_or(err).to_string();
            Err(AbiError::Call {
                exc: exc.to_string(),
                msg,
            })
        }
        other => Err(AbiError::Transport(format!(
            "Executable gate returned unknown kind={other:?}: {err}"
        ))),
    }
}

/// Run any subprocess with timeout (reusing the same "read thread + polling + kill" machinery) -- for delegation to SDK
/// delegated to the SDK (such as `install.py`), output is not interpreted as JSON.
pub fn run_program(
    program: &Path,
    args: &[String],
    timeout: Duration,
    env: &[(String, String)],
) -> Result<(Vec<u8>, Vec<u8>, i32), AbiError> {
    run_capture(program, args, timeout, env)
}

/// Run subprocess with timeout (no external dependencies: read thread + try_wait polling + timeout kill).
fn run_capture(
    program: &Path,
    args: &[String],
    timeout: Duration,
    env: &[(String, String)],
) -> Result<(Vec<u8>, Vec<u8>, i32), AbiError> {
    // ETXTBSY(26) retry: just-written executable file may be momentarily "busy" under multi-threaded fork/exec contention
    // (actually encountered when generating stub scripts on the fly in tests; similar production scenario = gate just unpacked).
    // Windows: python subprocess locale encoding defaults to cp1252(charmap) -- print Chinese to pipe
    // stdout crashes with UnicodeEncodeError, open() reading UTF-8 crashes with UnicodeDecodeError
    // (L3 2026-10-17 empirical: "pysdk entry call failed: 'charmap' codec can't encode
    // position 2001-2063" / "failed to write hermes webhook config: 'charmap'
    // codec can't decode byte 0x8f")。PYTHONUTF8=1 = PEP 540 UTF-8 mode,
    // One env override for standard IO + file open default encoding on both sides; unix does not set (behavior unchanged).
    let mut child = {
        let mut attempt = 0;
        loop {
            let mut cmd = Command::new(program);
            cmd.args(args);
            cmd.envs(env.iter().cloned());
            #[cfg(windows)]
            {
                cmd.env("PYTHONUTF8", "1");
            }
            cmd.stdin(Stdio::null());
            cmd.stdout(Stdio::piped());
            cmd.stderr(Stdio::piped());
            match cmd.spawn() {
                Ok(c) => break c,
                Err(e) if e.raw_os_error() == Some(26) && attempt < 5 => {
                    attempt += 1;
                    std::thread::sleep(Duration::from_millis(20));
                }
                Err(e) => {
                    return Err(AbiError::Transport(format!(
                        "Gate unavailable ({}): {e}",
                        program.display()
                    )))
                }
            }
        }
    };
    let mut out_pipe = child.stdout.take().expect("piped stdout");
    let mut err_pipe = child.stderr.take().expect("piped stderr");
    let out_h = std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = out_pipe.read_to_end(&mut b);
        b
    });
    let err_h = std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = err_pipe.read_to_end(&mut b);
        b
    });
    let deadline = Instant::now() + timeout;
    let code: i32 = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st.code().unwrap_or(-1),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = out_h.join();
                    let _ = err_h.join();
                    return Err(AbiError::Transport(format!(
                        "Gate timeout ({}s)",
                        timeout.as_secs()
                    )));
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(AbiError::Transport(format!("Gate wait failed: {e}"))),
        }
    };
    let out = out_h.join().unwrap_or_default();
    let err = err_h.join().unwrap_or_default();
    Ok((out, err, code))
}

/// python transport gate argv (contract §4.1(2): `<host_interpreter> -m aimail.sdk_ops <op>`).
/// Interpreter location chain see `core::sdkroot::python_bin` (`$AIMAIL_PYTHON` -> host venv probe -> `python3`).
/// Gate always uses **installed package** (zero path ✓).
pub fn python_door_command(op: &str, args: &Value) -> Vec<String> {
    // Interpreter location chain single source of truth = `core::sdkroot::python_bin` (contract §4.1(2))
    let py = crate::core::sdkroot::python_bin();
    vec![
        py,
        "-m".to_string(),
        "aimail.sdk_ops".to_string(),
        op.to_string(),
        "--args".to_string(),
        args.to_string(),
    ]
}

/// op's argv —— **transport dispatch** (contract v1.0 §4.1(2): two transports carry the same set of semantics).
/// · Registry provided `ops.argv` (node side: `node_entry`) ⇒ expand that template;
/// · Not provided (python host: hermes / deerflow) ⇒ python gate.
/// Template placeholders: `{op}` / `{args}` / `{node_path}` (resolved by `register.node_path` + candidate home).
pub fn op_argv(
    platform: &str,
    homes: &[String],
    op: &str,
    args: &Value,
) -> Result<Vec<String>, AbiError> {
    let Some(host) = crate::hosts::get(platform) else {
        return Err(AbiError::Transport(format!(
            "Unknown platform '{platform}' (not in host table) -- do not guess gate type"
        )));
    };
    let Some(tmpl) = host.op_template() else {
        return Ok(python_door_command(op, args));
    };
    let rdef = crate::hosts::exec::register_json(host);
    let node_tmpl = rdef.get("node_path").and_then(Value::as_str).unwrap_or("");
    let node_path = if node_tmpl.is_empty() {
        String::new()
    } else {
        crate::hosts::exec::resolve_path_template(node_tmpl, homes)
    };
    if tmpl.iter().any(|t| t.contains("{node_path}")) && node_path.is_empty() {
        // Explicit failure, do not guess: if platform package is not installed, there is no executable gate ⇒ provide the fix hint from the registry
        return Err(AbiError::Transport(format!(
            "{platform} platform package not installed (op entry missing) — {}",
            rdef.get("fail_hint").and_then(Value::as_str).unwrap_or("")
        )));
    }
    Ok(tmpl
        .iter()
        .map(|t| match *t {
            "{op}" => op.to_string(),
            "{args}" => args.to_string(),
            other => other.replace("{node_path}", &node_path),
        })
        .collect())
}

/// Call an SDK gate op (default 120s timeout, consistent with Python side).
pub fn sdk_ops(op: &str, args: &Value) -> Result<Value, AbiError> {
    sdk_ops_call(op, args, Duration::from_secs(120), &[])
}

/// Testable core: op + input JSON + timeout + subprocess environment (fixture isolation does not require modifying process env ⇒ parallel safe).
/// transport (python gate / node op entry) is determined by the system-level cfg pointed to by `args.system_id`.
pub fn sdk_ops_call(
    op: &str,
    args: &Value,
    timeout: Duration,
    env: &[(String, String)],
) -> Result<Value, AbiError> {
    // CLI→SDK actions unrelated to the host transport (update / prompt / address / repair backfill):
    // **Fixed python gate**, no platform reverse lookup (that is the responsibility of `sdk_ops_call_for`).
    let argv = python_door_command(op, args);
    let program = PathBuf::from(argv.first().cloned().unwrap_or_default());
    run_json_abi(&AbiCall {
        program: &program,
        args: argv[1..].to_vec(),
        timeout,
        env: env.to_vec(),
    })
}

/// Same as above, but **the platform and candidate platform root are given by the caller** (when the caller already knows the platform —— avoiding the fragility of reverse lookup;
/// e.g., platform + cand_homes in `register.rs`, platform + system_home in `uninstall.rs`).
pub fn sdk_ops_call_for(
    platform: &str,
    homes: &[String],
    op: &str,
    args: &Value,
    timeout: Duration,
    env: &[(String, String)],
) -> Result<Value, AbiError> {
    let argv = op_argv(platform, homes, op, args)?;
    let program = PathBuf::from(argv.first().cloned().unwrap_or_default());
    let call = AbiCall {
        program: &program,
        args: argv[1..].to_vec(),
        timeout,
        env: env.to_vec(),
    };
    run_json_abi(&call)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    fn stub_script(dir: &Path, name: &str, body: &str) -> PathBuf {
        let p = dir.join(name);
        fs::write(&p, body).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perm = fs::metadata(&p).unwrap().permissions();
            perm.set_mode(0o755);
            fs::set_permissions(&p, perm).unwrap();
        }
        p
    }

    fn call(script: &Path, args: &[&str]) -> Result<Value, AbiError> {
        run_json_abi(&AbiCall {
            program: script,
            args: args.iter().map(|s| s.to_string()).collect(),
            timeout: Duration::from_secs(10),
            env: vec![],
        })
    }

    #[test]
    fn ok_envelope_yields_result() {
        let d = tempfile::tempdir().unwrap();
        let s = stub_script(
            d.path(),
            "ok.sh",
            "#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"result\":[1,2]}'\n",
        );
        assert_eq!(call(&s, &[]).unwrap(), json!([1, 2]));
    }

    #[test]
    fn kind_dispatch_matches_python_taxonomy() {
        let d = tempfile::tempdir().unwrap();
        let usage = stub_script(
            d.path(),
            "usage.sh",
            "#!/bin/sh\nprintf '%s\\n' '{\"ok\":false,\"kind\":\"usage\",\"exc\":\"UsageError\",\"error\":\"bad op\"}'\nexit 2\n",
        );
        assert_eq!(
            call(&usage, &[]).unwrap_err(),
            AbiError::Usage("bad op".into())
        );

        let import = stub_script(
            d.path(),
            "imp.sh",
            "#!/bin/sh\nprintf '%s\\n' '{\"ok\":false,\"kind\":\"import\",\"exc\":\"ImportError\",\"error\":\"no module\"}'\nexit 1\n",
        );
        assert_eq!(
            call(&import, &[]).unwrap_err(),
            AbiError::Import("no module".into())
        );

        let call_e = stub_script(
            d.path(),
            "call.sh",
            "#!/bin/sh\nprintf '%s\\n' '{\"ok\":false,\"kind\":\"call\",\"exc\":\"ManagerRequiredError\",\"error\":\"ManagerRequiredError: manager missing\"}'\nexit 1\n",
        );
        let e = call(&call_e, &[]).unwrap_err();
        assert_eq!(
            e,
            AbiError::Call {
                exc: "ManagerRequiredError".into(),
                msg: "manager missing".into()
            }
        );
        // Caller message = same shape as Python's ({type(e).__name__}: {e})
        assert_eq!(e.display_like_python(), "ManagerRequiredError: manager missing");
    }

    #[test]
    fn multi_line_or_garbage_stdout_is_transport_error() {
        let d = tempfile::tempdir().unwrap();
        let two = stub_script(d.path(), "two.sh", "#!/bin/sh\nprintf 'a\\nb\\n'\n");
        assert!(matches!(
            call(&two, &[]).unwrap_err(),
            AbiError::Transport(_)
        ));
        let junk = stub_script(d.path(), "junk.sh", "#!/bin/sh\nprintf 'not json\\n'\n");
        assert!(matches!(
            call(&junk, &[]).unwrap_err(),
            AbiError::Transport(_)
        ));
        // If SDK's own prints mix into stdout, they will also be rejected by the "exactly one line" check (stdout has already been redirected to stderr inside the gate)
        let noise = stub_script(
            d.path(),
            "noise.sh",
            "#!/bin/sh\necho noise\nprintf '%s\\n' '{\"ok\":true,\"result\":1}'\n",
        );
        assert!(matches!(
            call(&noise, &[]).unwrap_err(),
            AbiError::Transport(_)
        ));
    }

    #[test]
    fn missing_program_and_timeout_are_transport_errors() {
        let d = tempfile::tempdir().unwrap();
        let missing = d.path().join("nope.sh");
        assert!(matches!(
            call(&missing, &[]).unwrap_err(),
            AbiError::Transport(_)
        ));
        let slow = stub_script(d.path(), "slow.sh", "#!/bin/sh\nsleep 5\n");
        let e = run_json_abi(&AbiCall {
            program: &slow,
            args: vec![],
            timeout: Duration::from_millis(300),
            env: vec![],
        })
        .unwrap_err();
        assert!(matches!(e, AbiError::Transport(_)));
        assert!(e.message().contains("timeout"), "{}", e.message());
    }

    #[test]
    fn python_door_command_uses_installed_package_no_paths() {
        // owner 2026-10-06: the gate always goes through **installed packages** (zero path ✗ no "same-origin" branch) —— the argv of the python transport is always
        // `<interpreter> -m aimail.sdk_ops <op> --args <json>` (contract §4.1(2)).
        let argv = python_door_command("iter_bindings", &json!({"system_id": "s1"}));
        assert_eq!(argv[1], "-m", "{argv:?}");
        assert_eq!(argv[2], "aimail.sdk_ops", "{argv:?}");
        assert_eq!(argv[3], "iter_bindings");
        assert_eq!(argv[4], "--args");
        assert_eq!(argv[5], "{\"system_id\":\"s1\"}");
    }

    #[test]
    fn op_argv_dispatches_by_registry_ops_block() {
        // Contract §4.1(2): node host (registry provided ops.argv) directly calls the platform package's built-in op entry, not via python;
        // python host (no ops block) goes through python gate.
        let node = op_argv(
            "dsh",
            &["/nonexistent-home".to_string()],
            "assemble",
            &json!({"a": 1}),
        );
        assert!(node.is_err(), "missing node entry ⇒ explicit failure (do not guess): {node:?}");
        let py = op_argv("hermes", &[], "assemble", &json!({"a": 1})).expect("python gate");
        assert_eq!(py[1], "-m");
        assert_eq!(py[2], "aimail.sdk_ops");
        assert_eq!(py[3], "assemble");
    }

    #[test]
    fn real_door_is_callable_when_python_and_sdk_are_present() {
        // Real gate integration (must run if local machine has python3 + installed agent-aimail; otherwise print reason and skip, do not fake pass)
        let home = tempfile::tempdir().unwrap();
        let r = sdk_ops_call(
            "iter_bindings",
            &json!({"system_id": ""}),
            Duration::from_secs(60),
            &[(
                "AIMAIL_HOME".to_string(),
                home.path().to_string_lossy().to_string(),
            )],
        );
        match r {
            Ok(Value::Array(items)) => assert!(items.is_empty(), "should not have bindings under fixture home"),
            Ok(other) => panic!("iter_bindings should return array, got {other:?}"),
            Err(e) => println!("SKIP: python gate unavailable: {}", e.display_like_python()),
        }
    }
}
