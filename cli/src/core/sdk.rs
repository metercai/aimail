//! CLI → SDK 委派执行器（**语言无关**）—— 2026-10-04 owner 裁决 A′。
//!
//! 契约（边界定稿 §1.5）：`python -m aimail.sdk_ops <op> [--args '<json>']`
//! · stdout **恰一行** JSON：`{"ok":true,"result":…}` / `{"ok":false,"kind":"usage|import|call","exc":…,"error":…}`
//! · exit `0` 成功 · `1` 运行期失败(import/call) · `2` 用法错误(usage)
//! · stderr = 人话/回溯/SDK 自身打印
//!
//! 为什么是"执行器"而不是"python 桥"：TS 侧的平台包早已是可执行门（`register-cli.js`，注册表
//! `register.kind=node_entry`）。本模块只认"可执行体 + argv + 单行 JSON"这条通则 —— 将来 Node 侧
//! 换 `program` 即可复用同一判读，不新造第三种形态。
//!
//! 定位（**同源优先**）：`{program_root}/aimail-src/pysdk/sdk_ops.py` 存在就直接跑它（调用方与被调方
//! 同一棵树；`install.py` 的混装教训）；否则回退 `-m aimail.sdk_ops`（pip 装的 aimailsdk ≥ 0.1.35）。
//! 解释器：`AIMAIL_PYTHON` > `python3`。

use serde_json::Value;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

/// 门的一次调用失败，按 kind 分档 —— 与 Python 侧 `sdk_ops()` 的三档**一一对应**。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AbiError {
    /// kind=usage：op 不存在 / --args 不是对象 / 缺必填键（调用方的 bug）
    Usage(String),
    /// kind=import：SDK 模块不可用（没装 / 版本过老）。**明确失败，不降级**
    Import(String),
    /// kind=call：SDK 内抛异常。`exc` = 异常类名（如 `ManagerRequiredError`），`msg` = 去前缀后的消息
    Call { exc: String, msg: String },
    /// 本地传输层失败（进程起不来 / 超时 / stdout 不是"恰一行 JSON"）
    Transport(String),
}

impl AbiError {
    /// 供调用方拼 `({exc}: {msg})` 文案用的异常类名（Transport/Usage/Import 也给一个稳定名）。
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

    /// 与 Python 的 `({type(e).__name__}: {e})` 逐字同形。
    pub fn display_like_python(&self) -> String {
        format!("{}: {}", self.exc_name(), self.message())
    }
}

/// 门（或任何"单行 JSON ABI"可执行体）的调用参数。
pub struct AbiCall<'a> {
    pub program: &'a Path,
    pub args: Vec<String>,
    pub timeout: Duration,
    /// 追加/覆盖的子进程环境（隔离夹具用；空 = 继承）
    pub env: Vec<(String, String)>,
}

/// 跑一条进程命令契约，返回信封里的 `result`。
///
/// 判读顺序：进程起不来 ⇒ Transport；stdout 非"恰一行" ⇒ Transport；信封 ok=true ⇒ result；
/// 否则按 kind(usage/import/call) 分档；kind 未知 ⇒ Transport（不猜）。
pub fn run_json_abi(call: &AbiCall<'_>) -> Result<Value, AbiError> {
    let output = run_capture(call.program, &call.args, call.timeout, &call.env)?;
    let stdout = String::from_utf8_lossy(&output.0).to_string();
    let lines: Vec<&str> = stdout.lines().filter(|l| !l.trim().is_empty()).collect();
    if lines.len() != 1 {
        let stderr = String::from_utf8_lossy(&output.1);
        return Err(AbiError::Transport(format!(
            "可执行门输出异常（rc={}, {} 行）: {}{}",
            output.2,
            lines.len(),
            lines.first().copied().unwrap_or(""),
            stderr.chars().take(200).collect::<String>()
        )));
    }
    let env: Value = serde_json::from_str(lines[0])
        .map_err(|e| AbiError::Transport(format!("可执行门输出不是 JSON: {e}")))?;
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
            // 门上报的 error 形如 "ExcName: msg" ⇒ 去掉前缀，调用方再拼一次前缀才是 Python 同形
            let prefix = format!("{exc}: ");
            let msg = err.strip_prefix(prefix.as_str()).unwrap_or(err).to_string();
            Err(AbiError::Call {
                exc: exc.to_string(),
                msg,
            })
        }
        other => Err(AbiError::Transport(format!(
            "可执行门返回未知 kind={other:?}: {err}"
        ))),
    }
}

/// 跑任意子程序并带超时（复用同一套"读取线程 + 轮询 + kill"机器）—— 供委派给 SDK 的
/// 其它入口（如 `install.py`）使用，输出不做 JSON 判读。
pub fn run_program(
    program: &Path,
    args: &[String],
    timeout: Duration,
    env: &[(String, String)],
) -> Result<(Vec<u8>, Vec<u8>, i32), AbiError> {
    run_capture(program, args, timeout, env)
}

/// 跑子进程并带超时（无外部依赖：读取线程 + try_wait 轮询 + 超时 kill）。
fn run_capture(
    program: &Path,
    args: &[String],
    timeout: Duration,
    env: &[(String, String)],
) -> Result<(Vec<u8>, Vec<u8>, i32), AbiError> {
    // ETXTBSY(26) 重试：刚写完的可执行文件在多线程 fork/exec 争用下会瞬间"忙"
    // （测试里现场生成 stub 脚本时实测踩到；生产侧同类场景 = 刚解包出来的门）。
    let mut child = {
        let mut attempt = 0;
        loop {
            match Command::new(program)
                .args(args)
                .envs(env.iter().cloned())
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
            {
                Ok(c) => break c,
                Err(e) if e.raw_os_error() == Some(26) && attempt < 5 => {
                    attempt += 1;
                    std::thread::sleep(Duration::from_millis(20));
                }
                Err(e) => {
                    return Err(AbiError::Transport(format!(
                        "门不可用({}): {e}",
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
                        "门超时（{}s）",
                        timeout.as_secs()
                    )));
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(e) => return Err(AbiError::Transport(format!("门等待失败: {e}"))),
        }
    };
    let out = out_h.join().unwrap_or_default();
    let err = err_h.join().unwrap_or_default();
    Ok((out, err, code))
}

/// 门的 argv（**同源优先**；解释器 `AIMAIL_PYTHON` > `python3`）。
pub fn door_command(op: &str, args: &Value) -> Vec<String> {
    door_command_in(&crate::core::home::program_root(), op, args)
}

/// 同上，但程序根显式传入（测试用夹具根可走"同源"分支，不必改进程环境）。
pub fn door_command_in(prog_root: &Path, op: &str, args: &Value) -> Vec<String> {
    let py = std::env::var("AIMAIL_PYTHON").unwrap_or_else(|_| "python3".to_string());
    let mut argv = vec![py];
    let same_tree = prog_root
        .join("aimail-src")
        .join("pysdk")
        .join("sdk_ops.py");
    if same_tree.is_file() {
        argv.push(same_tree.to_string_lossy().to_string());
    } else {
        argv.push("-m".to_string());
        argv.push("aimail.sdk_ops".to_string());
    }
    argv.push(op.to_string());
    argv.push("--args".to_string());
    argv.push(args.to_string());
    argv
}

/// 调一个 SDK 门 op（默认 120s 超时，与 Python 侧一致）。
pub fn sdk_ops(op: &str, args: &Value) -> Result<Value, AbiError> {
    sdk_ops_call(
        op,
        args,
        &crate::core::home::program_root(),
        Duration::from_secs(120),
        &[],
    )
}

pub fn sdk_ops_with_timeout(op: &str, args: &Value, timeout: Duration) -> Result<Value, AbiError> {
    sdk_ops_call(op, args, &crate::core::home::program_root(), timeout, &[])
}

/// 可测核心：程序根 + 超时 + 子进程环境都可注入（夹具隔离无需改进程 env ⇒ 并行安全）。
pub fn sdk_ops_call(
    op: &str,
    args: &Value,
    prog_root: &Path,
    timeout: Duration,
    env: &[(String, String)],
) -> Result<Value, AbiError> {
    let argv = door_command_in(prog_root, op, args);
    let program = PathBuf::from(&argv[0]);
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
            "#!/bin/sh\nprintf '%s\\n' '{\"ok\":false,\"kind\":\"call\",\"exc\":\"ManagerRequiredError\",\"error\":\"ManagerRequiredError: 缺 manager\"}'\nexit 1\n",
        );
        let e = call(&call_e, &[]).unwrap_err();
        assert_eq!(
            e,
            AbiError::Call {
                exc: "ManagerRequiredError".into(),
                msg: "缺 manager".into()
            }
        );
        // 调用方文案 = Python 的 ({type(e).__name__}: {e}) 同形
        assert_eq!(e.display_like_python(), "ManagerRequiredError: 缺 manager");
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
        // SDK 自身打印若混进 stdout 也会被"恰一行"判掉（门内已把 stdout 改道 stderr）
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
        assert!(e.message().contains("超时"), "{}", e.message());
    }

    #[test]
    fn door_command_prefers_same_tree_then_pip_module() {
        // 同源优先：把 program_root 指到夹具（AIMAIL_PROG_DIR）⇒ 走 <prog>/aimail-src/pysdk/sdk_ops.py
        let d = tempfile::tempdir().unwrap();
        let door = d.path().join("aimail-src").join("pysdk");
        fs::create_dir_all(&door).unwrap();
        fs::write(door.join("sdk_ops.py"), "# stub\n").unwrap();
        let argv = door_command_in(d.path(), "iter_bindings", &json!({"system_id": "s1"}));
        assert!(argv[1].ends_with("sdk_ops.py"), "{argv:?}");
        assert_eq!(argv[2], "iter_bindings");
        assert_eq!(argv[3], "--args");
        assert_eq!(argv[4], "{\"system_id\":\"s1\"}");
    }

    #[test]
    fn real_door_is_callable_when_python_and_sdk_are_present() {
        // 真门联调（本机有 python3 + 仓库 pysdk 时必跑；否则打印原因跳过，不伪装通过）
        let prog_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
        let door = prog_root
            .join("aimail-src")
            .join("pysdk")
            .join("sdk_ops.py");
        if !door.is_file() {
            // 仓库形态的程序根不含 aimail-src ⇒ 回退 pip 门（≥0.1.35）；两者都不可用则跳过
            let fallback_ok = sdk_ops_call(
                "iter_bindings",
                &json!({"system_id": ""}),
                &prog_root,
                Duration::from_secs(60),
                &[],
            );
            if let Err(e) = fallback_ok {
                println!("SKIP: 既无同源门也无 pip 门: {}", e.display_like_python());
                return;
            }
        }
        let home = tempfile::tempdir().unwrap();
        let r = sdk_ops_call(
            "iter_bindings",
            &json!({"system_id": ""}),
            &prog_root,
            Duration::from_secs(60),
            &[(
                "AIMAIL_HOME".to_string(),
                home.path().to_string_lossy().to_string(),
            )],
        );
        match r {
            Ok(Value::Array(items)) => assert!(items.is_empty(), "夹具 home 下不应有绑定"),
            Ok(other) => panic!("iter_bindings 应返回数组, 得到 {other:?}"),
            Err(e) => panic!("真门调用失败: {}", e.display_like_python()),
        }
    }
}
