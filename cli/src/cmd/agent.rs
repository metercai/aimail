//! `aimail agent` —— address-level bootstrap (control plane only).
//!
//! `aimail agent doctor --questions`：打印"一次提问、一张答案表"的 1-shot 提问块，
//!   由 prompt/文档原样交给 agent（不在 CLI 里绕圈问）。
//! `aimail agent doctor --answers '<json>'`：逐条**验证** agent 的自述（路径存在/可写、
//!   命令可执行、示例插件可读），产出 host profile + SDK 选型 + verdict。
//!
//! 设计约束：
//! · 不信自述：每项都要机器证据，标 verified / claimed；`claimed` 不得单独构成 ready。
//! · SDK 选型在 doctor 内定：命中已适配包优先，否则按 runtime 选通用 SDK，other ⇒ unsupported。
//! · 本命令**不写任何文件**（安装/适配包由 setup 负责），只读探测 + 输出 JSON。

use serde_json::{json, Value};
use std::path::Path;

/// 1-shot 提问块：表格 + 反例 + 答题前自检 + JSON 骨架。
const QUESTIONS: &str = r#"请按下面这张表回答，一次答完，只输出表格和 JSON，不要解释。

【示例】
| # | 问题 | 我的答案 | 我怎么验证的 |
|---|---|---|---|
| Q1 | 宿主程序名/版本/runtime | acme-agent@2.3.1 · node | `command -v acme-agent` → /usr/local/bin/acme-agent；`acme-agent version` → 2.3.1 |
| Q2 | 插件目录绝对路径 | /home/me/.acme/plugins | `test -d` 通过、`test -w` 通过、目录内有 existing-hello.js |
| Q3 | 生效方式（一条命令 / none） | `acme-agent reload` | `command -v acme-agent` 存在；`acme-agent reload --help` 正常返回 |
| Q4 | skills 目录（可写） | /home/me/.acme/skills | `test -d` + `test -w` 通过 |
| Q5 | 是否常驻（yes/no） | yes | `ps -o etimes= -p $PPID` → 86400 |
| Q6 | 注册工具的最小写法（文件路径/片段） | /home/me/.acme/plugins/existing-hello.js | 已读该文件：`api.registerTool({name, description, parameters, handler})` |

【反例（这样答会被拒）】
- 相对路径 `./plugins` → 必须绝对路径
- Q1 只写 `node` → 必须有宿主程序名与版本
- Q3 写"重启一下就行" → 必须给一条可执行命令，或 `none`
- Q6 写"标准写法" → 必须给文件路径或 3 行真实代码

【答题前自检（先跑再答）】
`test -d <dir>` / `test -w <dir>` / `command -v <cmd>` / `sed -n '1,40p' <plugin-file>` / `ps -o etimes= -p $PPID`

【输出】
表格之外再给一段 JSON：
{"host":{"name":"","version":"","runtime":"node|python|other"},"plugin":{"dir":""},"reload":{"mode":"command|http|signal|none","cmd":""},"skills":{"dir":""},"process":{"long_running":true},"sample_file":""}
"#;

pub struct Args {
    pub questions: bool,
    pub answers: Option<String>,
    pub answers_file: Option<String>,
    pub json: bool,
}

fn sh(cmd: &str) -> Option<String> {
    let out = std::process::Command::new("sh")
        .arg("-c")
        .arg(cmd)
        .output()
        .ok()?;
    if out.status.success() {
        Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        None
    }
}

fn writable(dir: &str) -> bool {
    let probe = Path::new(dir).join(".aimail-write-probe");
    match std::fs::write(&probe, b"probe") {
        Ok(_) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

fn dir_entries(dir: &str) -> Vec<String> {
    std::fs::read_dir(dir)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().to_string())
                .take(5)
                .collect()
        })
        .unwrap_or_default()
}

/// 从示例插件里抽出"注册工具"的调用形态（模板参数化的依据）。
fn api_shape(sample: &str) -> String {
    let text = std::fs::read_to_string(sample).unwrap_or_default();
    let head: String = text.lines().take(80).collect::<Vec<_>>().join("\n");
    for (needle, shape) in [
        (
            "registerTool(",
            "registerTool(name, description, parameters, handler)",
        ),
        (
            "register_tool(",
            "register_tool(name, description, parameters, handler)",
        ),
        ("api.tools.add(", "api.tools.add(name, schema, fn)"),
        ("addTool(", "addTool(name, schema, fn)"),
        ("register(", "register(name, schema, fn)"),
    ] {
        if head.contains(needle) {
            return shape.to_string();
        }
    }
    String::new()
}

fn sdk_for(host_name: &str, runtime: &str) -> (String, String, bool) {
    let h = host_name.to_lowercase();
    for (needle, pkg) in [
        ("hermes", "aimailsdk"),
        ("deerflow", "aimailsdk"),
        ("dsh", "dsh-aimail"),
        ("openclaw", "openclaw-aimail"),
        ("pi", "pi-aimail"),
    ] {
        if h.contains(needle) {
            return (pkg.to_string(), "adapter".to_string(), true);
        }
    }
    match runtime {
        "node" | "ts" | "typescript" => {
            ("@aimail/mail-core".to_string(), "generic".to_string(), true)
        }
        "python" | "py" => ("aimailsdk".to_string(), "generic".to_string(), true),
        _ => (String::new(), "none".to_string(), false),
    }
}

pub fn run(args: &Args) -> i32 {
    if args.questions {
        println!("{QUESTIONS}");
        return 0;
    }

    let raw = match (&args.answers, &args.answers_file) {
        (Some(s), _) => s.clone(),
        (None, Some(f)) => match std::fs::read_to_string(f) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("doctor: cannot read {f}: {e}");
                return 2;
            }
        },
        (None, None) => {
            eprintln!("usage: aimail agent doctor --questions | --answers '<json>' [--json]");
            return 2;
        }
    };
    let v: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("doctor: answers is not valid JSON: {e}");
            return 2;
        }
    };

    let host_name = v["host"]["name"].as_str().unwrap_or("").to_string();
    let host_ver = v["host"]["version"].as_str().unwrap_or("").to_string();
    let runtime = v["host"]["runtime"].as_str().unwrap_or("").to_lowercase();
    let plugin_dir = v["plugin"]["dir"].as_str().unwrap_or("").to_string();
    let skills_dir = v["skills"]["dir"].as_str().unwrap_or("").to_string();
    let reload_mode = v["reload"]["mode"].as_str().unwrap_or("").to_string();
    let reload_cmd = v["reload"]["cmd"].as_str().unwrap_or("").to_string();
    let long_running = v["process"]["long_running"].as_bool().unwrap_or(false);
    let sample = v["sample_file"].as_str().unwrap_or("").to_string();

    let mut checks: Vec<Value> = Vec::new();

    // Q1 host
    let host_path = if host_name.is_empty() {
        None
    } else {
        sh(&format!("command -v {}", shell_escape(&host_name)))
    };
    let host_ver_out = if host_name.is_empty() {
        None
    } else {
        sh(&format!(
            "{} version 2>/dev/null || {} --version 2>/dev/null",
            shell_escape(&host_name),
            shell_escape(&host_name)
        ))
    };
    let host_ok = host_path.is_some();
    checks.push(json!({
        "id": "Q1", "name": "host",
        "status": if host_ok { "verified" } else { "rejected" },
        "evidence": format!("path={:?} version_out={:?} claimed={}@{}", host_path, host_ver_out, host_name, host_ver),
    }));

    // Q2 plugin dir
    let plugin_is_dir = !plugin_dir.is_empty() && Path::new(&plugin_dir).is_dir();
    let plugin_writable = plugin_is_dir && writable(&plugin_dir);
    let entries = dir_entries(&plugin_dir);
    checks.push(json!({
        "id": "Q2", "name": "plugin_dir",
        "status": if plugin_is_dir && plugin_writable { "verified" } else { "rejected" },
        "evidence": format!("is_dir={} writable={} entries={:?}", plugin_is_dir, plugin_writable, entries),
    }));

    // Q3 reload
    let (reload_status, reload_evidence) = match reload_mode.as_str() {
        "none" => ("verified", "hot reload (no action)".to_string()),
        "command" if !reload_cmd.is_empty() => {
            let first = reload_cmd.split_whitespace().next().unwrap_or("");
            match sh(&format!("command -v {}", shell_escape(first))) {
                Some(p) => ("verified", format!("command={p}")),
                None => ("rejected", format!("command not found: {first}")),
            }
        }
        "http" if !reload_cmd.is_empty() => {
            match sh(&format!(
                "curl -sS -o /dev/null -w '%{{http_code}}' -m 3 {}",
                shell_escape(&reload_cmd)
            )) {
                Some(code) => ("verified", format!("http={code}")),
                None => ("rejected", "http endpoint unreachable".to_string()),
            }
        }
        "signal" => (
            "claimed",
            "signal: cannot verify without the host pid".to_string(),
        ),
        other => ("rejected", format!("unsupported reload mode: {other:?}")),
    };
    checks.push(
        json!({"id": "Q3", "name": "reload", "status": reload_status, "evidence": reload_evidence}),
    );

    // Q4 skills dir
    let skills_is_dir = !skills_dir.is_empty() && Path::new(&skills_dir).is_dir();
    let skills_writable = skills_is_dir && writable(&skills_dir);
    checks.push(json!({
        "id": "Q4", "name": "skills_dir",
        "status": if skills_is_dir && skills_writable { "verified" } else { "rejected" },
        "evidence": format!("is_dir={} writable={}", skills_is_dir, skills_writable),
    }));

    // Q5 long-running: doctor can only record the claim; the pull heartbeat proves it later.
    checks.push(json!({
        "id": "Q5", "name": "long_running",
        "status": if long_running { "claimed" } else { "rejected" },
        "evidence": "proof is the pull heartbeat at setup time (~/.aimail/state/pull-<addr>.beat)",
    }));

    // Q6 api shape
    let shape = if sample.is_empty() {
        String::new()
    } else {
        api_shape(&sample)
    };
    checks.push(json!({
        "id": "Q6", "name": "api_shape",
        "status": if !shape.is_empty() { "verified" } else { "rejected" },
        "evidence": if shape.is_empty() { format!("no registration call found in {sample:?}") } else { format!("{shape}  (from {sample})") },
    }));

    let (sdk_pkg, sdk_source, sdk_ok) = sdk_for(&host_name, &runtime);
    let all_verified = checks
        .iter()
        .all(|c| c["status"] == "verified" || (c["id"] == "Q5" && c["status"] == "claimed"));
    let verdict = if !sdk_ok {
        "unsupported"
    } else if all_verified && long_running {
        "ready"
    } else if !long_running {
        "unsupported"
    } else {
        "blocked"
    };

    let profile = json!({
        "host": {"name": host_name, "version": host_ver, "runtime": runtime},
        "plugin": {"dir": plugin_dir, "entries": entries},
        "reload": {"mode": reload_mode, "cmd": reload_cmd, "requires_action": reload_mode != "none"},
        "skills": {"dir": skills_dir},
        "process": {"long_running": long_running},
        "api_shape": shape,
        "sdk": {"package": sdk_pkg, "source": sdk_source, "symbols_ok": null_by_design()},
        "checks": checks,
        "verdict": verdict,
    });

    if args.json {
        match serde_json::to_string_pretty(&profile) {
            Ok(s) => println!("{s}"),
            Err(e) => {
                eprintln!("doctor: json encode failed: {e}");
                return 1;
            }
        }
    } else {
        println!("verdict: {verdict}");
        if let Some(arr) = profile["checks"].as_array() {
            for c in arr {
                println!(
                    "  {:<3} {:<12} {:<9} {}",
                    c["id"].as_str().unwrap_or(""),
                    c["name"].as_str().unwrap_or(""),
                    c["status"].as_str().unwrap_or(""),
                    c["evidence"].as_str().unwrap_or("")
                );
            }
        }
        println!(
            "  sdk: {} ({})",
            profile["sdk"]["package"].as_str().unwrap_or("-"),
            sdk_source
        );
    }

    match verdict {
        "ready" => 0,
        "blocked" => 5,
        _ => 4,
    }
}

/// symbols_ok 由 setup 的符号探针填写；doctor 阶段显式 null（不假装已验证）。
fn null_by_design() -> Value {
    Value::Null
}

/// 只允许安全字符进 shell 单引号，防止自述里的奇怪字符变成命令。
fn shell_escape(s: &str) -> String {
    let ok = s.chars().all(|c| {
        c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '/' | ':' | '@' | '+' | '~')
    });
    if ok {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\\''"))
    }
}
