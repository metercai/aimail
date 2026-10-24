//! `aimail agent` —— address-level bootstrap (control plane only).
//!
//! `doctor --questions`: prints the 1-shot question block of "one question, one answer table" (passed as-is to the agent by prompt).
//! `doctor --answers '<json>'`: **verify** self-report item by item (path exists/writable, command executable, sample plugin readable,
//!   inbound injection call visible), produce host profile + SDK selection + verdict; do not write any files.
//! `setup --answers '<json>' [--apply]`: renders the adapter package and skill per profile, records state (file + sha256),
//!   optionally call host's reload hook; default dry-run.
//! `uninstall --host <name>`: reverse restore per state (only delete files written this time and sha256 unchanged).
//!
//! Constraints: do not trust self-report (each item needs machine evidence); do not modify host existing files (only create new `aimail-<host>/` subdirectory);
//! no "inbound injection" means no in-process pull ⇒ directly judge unsupported, do not pretend to receive mail.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

const TPL_NODE: &str = include_str!("../../templates/adapter-node.mjs.tpl");
const TPL_PY: &str = include_str!("../../templates/adapter-python.py.tpl");
const SKILL_MD: &str = include_str!("../../../resources/skills/SKILL.md");

/// 1-shot question block: table + counter-examples + pre-answer self-check + JSON skeleton.
const QUESTIONS: &str = r#"Please answer according to the table below, answer all at once, only output table and JSON, do not explain.

[Example]
| # | Question | My Answer | How I Verified |
|---|---|---|---|
| Q1 | Host program name/version/runtime | acme-agent@2.3.1 · node | `command -v acme-agent` → /usr/local/bin/acme-agent; `acme-agent version` → 2.3.1 |
| Q2 | Plugin directory absolute path | /home/me/.acme/plugins | `test -d` passed, `test -w` passed, directory contains existing-hello.js |
|| Q3 | Activation method (one command / none) | `acme-agent reload` | `command -v acme-agent` exists; `acme-agent reload --help` returns normally |
|| Q4 | skills directory (writable) | /home/me/.acme/skills | `test -d` + `test -w` pass |
|| Q5 | Resident or not (yes/no) | yes | `ps -o etimes= -p $PPID` → 86400 |
|| Q6 | Minimal registration tool syntax (file path/snippet) | /home/me/.acme/plugins/existing-hello.js | Read that file: `api.registerTool({name, description, parameters, handler})` |
|| Q7 | How to pass new mail to the session (one call / none) | `api.sendMessage(sessionId, mail.subject + "\n\n" + mail.body)` | This function name appears in existing-hello.js |

【Counter-examples (answers like this will be rejected)】
- Relative path `./plugins` → Must be an absolute path
- Q1 only writes `node` → Must include host program name and version
- Q3 writes "just restart it" → Must provide one executable command, or `none`
- Q6 writes "standard syntax" → Must provide file path or 3 lines of real code
- Q7 writes "agent will see it itself" → Must provide specific call, or `none` (no injection capability)

【Self-check before answering (run first, then answer)】
`test -d <dir>` / `test -w <dir>` / `command -v <cmd>` / `sed -n '1,40p' <plugin-file>` / `ps -o etimes= -p $PPID`

【Output】
Provide a JSON block in addition to the table:
{"host":{"name":"","version":"","runtime":"node|python|other"},"plugin":{"dir":""},"reload":{"mode":"command|http|signal|none","cmd":""},"skills":{"dir":""},"process":{"long_running":true},"sample_file":"","inbound":{"mode":"call|none","call":""}}
"#;

pub struct Args {
    pub questions: bool,
    pub answers: Option<String>,
    pub answers_file: Option<String>,
    pub json: bool,
}

pub struct SetupArgs {
    pub answers: Option<String>,
    pub answers_file: Option<String>,
    pub apply: bool,
    pub reload: bool,
}

pub struct UninstallArgs {
    pub host: Option<String>,
}

// ───────────────────────────── helpers ─────────────────────────────

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

/// Pull the first real call symbol out of a snippet (`await api.sendMessage(...)` → `sendMessage`).
fn call_symbol(call: &str) -> String {
    let b = call.as_bytes();
    let mut i = 0usize;
    while i < b.len() {
        if b[i].is_ascii_alphabetic() || b[i] == b'_' {
            let start = i;
            while i < b.len() && (b[i].is_ascii_alphanumeric() || b[i] == b'_' || b[i] == b'.') {
                i += 1;
            }
            let ident = &call[start..i];
            let mut j = i;
            while j < b.len() && b[j] == b' ' {
                j += 1;
            }
            if j < b.len()
                && b[j] == b'('
                && !matches!(ident, "await" | "return" | "if" | "for" | "while" | "new")
            {
                return ident.rsplit('.').next().unwrap_or(ident).to_string();
            }
        } else {
            i += 1;
        }
    }
    String::new()
}

fn sdk_for(host_name: &str, runtime: &str) -> (String, String, bool) {
    let h = host_name.to_lowercase();
    for (needle, pkg) in [
        ("hermes", "agent-aimail"),
        ("deerflow", "agent-aimail"),
        ("dsh", "agent-aimail"),
        ("openclaw", "agent-aimail"),
        ("pi", "agent-aimail"),
    ] {
        if h.contains(needle) {
            return (pkg.to_string(), "adapter".to_string(), true);
        }
    }
    match runtime {
        "node" | "ts" | "typescript" => ("agent-aimail".to_string(), "generic".to_string(), true),
        "python" | "py" => ("agent-aimail".to_string(), "generic".to_string(), true),
        _ => (String::new(), "none".to_string(), false),
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    format!("{:x}", h.finalize())
}

fn slug(name: &str) -> String {
    let s: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    let s = s.trim_matches('-').to_lowercase();
    if s.is_empty() {
        "unknown-host".to_string()
    } else {
        s
    }
}

fn load_answers(args: &Args) -> Result<Value, i32> {
    let raw = match (&args.answers, &args.answers_file) {
        (Some(s), _) => s.clone(),
        (None, Some(f)) => match std::fs::read_to_string(f) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("doctor: cannot read {f}: {e}");
                return Err(2);
            }
        },
        (None, None) => {
            eprintln!("usage: aimail agent doctor --questions | --answers '<json>' [--json]");
            return Err(2);
        }
    };
    serde_json::from_str(&raw).map_err(|e| {
        eprintln!("doctor: answers is not valid JSON: {e}");
        2
    })
}

fn load_answers_setup(args: &SetupArgs) -> Result<Value, i32> {
    let raw = match (&args.answers, &args.answers_file) {
        (Some(s), _) => s.clone(),
        (None, Some(f)) => match std::fs::read_to_string(f) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("setup: cannot read {f}: {e}");
                return Err(2);
            }
        },
        (None, None) => {
            eprintln!("usage: aimail agent setup --answers '<json>' [--apply] [--no-reload]");
            return Err(2);
        }
    };
    serde_json::from_str(&raw).map_err(|e| {
        eprintln!("setup: answers is not valid JSON: {e}");
        2
    })
}

// ───────────────────────────── profile ─────────────────────────────

fn build_profile(v: &Value) -> Value {
    let host_name = v["host"]["name"].as_str().unwrap_or("").to_string();
    let host_ver = v["host"]["version"].as_str().unwrap_or("").to_string();
    let runtime = v["host"]["runtime"].as_str().unwrap_or("").to_lowercase();
    let plugin_dir = v["plugin"]["dir"].as_str().unwrap_or("").to_string();
    let skills_dir = v["skills"]["dir"].as_str().unwrap_or("").to_string();
    let reload_mode = v["reload"]["mode"].as_str().unwrap_or("").to_string();
    let reload_cmd = v["reload"]["cmd"].as_str().unwrap_or("").to_string();
    let long_running = v["process"]["long_running"].as_bool().unwrap_or(false);
    let sample = v["sample_file"].as_str().unwrap_or("").to_string();
    let inbound_mode = v["inbound"]["mode"].as_str().unwrap_or("").to_string();
    let inbound_call = v["inbound"]["call"].as_str().unwrap_or("").to_string();

    let mut checks: Vec<Value> = Vec::new();

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
    checks.push(json!({
        "id": "Q1", "name": "host",
        "status": if host_path.is_some() { "verified" } else { "rejected" },
        "evidence": format!("path={:?} version_out={:?} claimed={}@{}", host_path, host_ver_out, host_name, host_ver),
    }));

    let plugin_is_dir = !plugin_dir.is_empty() && Path::new(&plugin_dir).is_dir();
    let plugin_writable = plugin_is_dir && writable(&plugin_dir);
    let entries = dir_entries(&plugin_dir);
    checks.push(json!({
        "id": "Q2", "name": "plugin_dir",
        "status": if plugin_is_dir && plugin_writable { "verified" } else { "rejected" },
        "evidence": format!("is_dir={} writable={} entries={:?}", plugin_is_dir, plugin_writable, entries),
    }));

    let (reload_status, reload_evidence) = match reload_mode.as_str() {
        "none" => ("verified", "hot reload (no action)".to_string()),
        "command" if !reload_cmd.is_empty() => {
            let first = reload_cmd.split_whitespace().next().unwrap_or("");
            match sh(&format!("command -v {}", shell_escape(first))) {
                Some(p) => ("verified", format!("command={p}")),
                None => ("rejected", format!("command not found: {first}")),
            }
        }
        "http" if !reload_cmd.is_empty() => match sh(&format!(
            "curl -sS -o /dev/null -w '%{{http_code}}' -m 3 {}",
            shell_escape(&reload_cmd)
        )) {
            Some(code) => ("verified", format!("http={code}")),
            None => ("rejected", "http endpoint unreachable".to_string()),
        },
        "signal" => (
            "claimed",
            "signal: cannot verify without the host pid".to_string(),
        ),
        other => ("rejected", format!("unsupported reload mode: {other:?}")),
    };
    checks.push(
        json!({"id": "Q3", "name": "reload", "status": reload_status, "evidence": reload_evidence}),
    );

    let skills_is_dir = !skills_dir.is_empty() && Path::new(&skills_dir).is_dir();
    let skills_writable = skills_is_dir && writable(&skills_dir);
    checks.push(json!({
        "id": "Q4", "name": "skills_dir",
        "status": if skills_is_dir && skills_writable { "verified" } else { "rejected" },
        "evidence": format!("is_dir={} writable={}", skills_is_dir, skills_writable),
    }));

    checks.push(json!({
        "id": "Q5", "name": "long_running",
        "status": if long_running { "claimed" } else { "rejected" },
        "evidence": "proof is the pull heartbeat at setup time (~/.aimail/state/pull-<addr>.beat)",
    }));

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

    // Q7: without a way to hand a mail to a session there is no in-process pull delivery.
    let inbound_ok = inbound_mode == "call" && inbound_call.contains('(');
    let symbol = call_symbol(&inbound_call);
    let inbound_symbol_seen = if inbound_ok && !sample.is_empty() && !symbol.is_empty() {
        std::fs::read_to_string(&sample)
            .map(|t| t.contains(&symbol))
            .unwrap_or(false)
    } else {
        false
    };
    checks.push(json!({
        "id": "Q7", "name": "inbound_hook",
        "status": if !inbound_ok { "rejected" } else if inbound_symbol_seen { "verified" } else { "claimed" },
        "evidence": if inbound_ok { inbound_call.clone() } else { "no injection call (mode=none) — pull cannot create a turn".to_string() },
    }));

    let (sdk_pkg, sdk_source, sdk_ok) = sdk_for(&host_name, &runtime);
    let hard_fail = checks.iter().any(|c| {
        c["status"] == "rejected"
            && matches!(
                c["id"].as_str().unwrap_or(""),
                "Q1" | "Q2" | "Q4" | "Q6" | "Q7" | "Q5"
            )
    });
    let verdict = if !sdk_ok || !inbound_ok || !long_running {
        "unsupported"
    } else if hard_fail {
        "blocked"
    } else {
        "ready"
    };

    json!({
        "host": {"name": host_name, "version": host_ver, "runtime": runtime},
        "plugin": {"dir": plugin_dir, "entries": entries},
        "reload": {"mode": reload_mode, "cmd": reload_cmd, "requires_action": reload_mode != "none"},
        "skills": {"dir": skills_dir},
        "process": {"long_running": long_running},
        "api_shape": shape,
        "inbound": {"mode": inbound_mode, "call": inbound_call},
        "sdk": {"package": sdk_pkg, "source": sdk_source, "symbols_ok": Value::Null},
        "checks": checks,
        "verdict": verdict,
    })
}

// ───────────────────────────── doctor ─────────────────────────────

pub fn run(args: &Args) -> i32 {
    if args.questions {
        println!("{QUESTIONS}");
        return 0;
    }
    let v = match load_answers(args) {
        Ok(v) => v,
        Err(rc) => return rc,
    };
    let profile = build_profile(&v);
    let verdict = profile["verdict"].as_str().unwrap_or("blocked");

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
                    "  {:<3} {:<13} {:<9} {}",
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
            profile["sdk"]["source"].as_str().unwrap_or("-")
        );
    }

    match verdict {
        "ready" => 0,
        "blocked" => 5,
        _ => 4,
    }
}

// ───────────────────────────── setup ─────────────────────────────

fn tool_register_snippet(shape: &str, runtime: &str) -> Option<String> {
    let py = runtime == "python" || runtime == "py";
    let s = if py {
        match shape {
            "registerTool(name, description, parameters, handler)" | "register_tool(name, description, parameters, handler)" => {
                "api.register_tool(t[\"name\"], t[\"description\"], t[\"inputSchema\"], t[\"handler\"])"
            }
            "api.tools.add(name, schema, fn)" => "api.tools.add(t[\"name\"], t[\"inputSchema\"], t[\"handler\"])",
            "addTool(name, schema, fn)" => "api.add_tool(t[\"name\"], t[\"inputSchema\"], t[\"handler\"])",
            "register(name, schema, fn)" => "api.register(t[\"name\"], t[\"inputSchema\"], t[\"handler\"])",
            _ => return None,
        }
    } else {
        match shape {
            "registerTool(name, description, parameters, handler)" => "api.registerTool({ name: t.name, description: t.description, parameters: t.inputSchema, handler: (a) => t.handler(a) });",
            "register_tool(name, description, parameters, handler)" => "api.register_tool({ name: t.name, description: t.description, parameters: t.inputSchema, handler: (a) => t.handler(a) });",
            "api.tools.add(name, schema, fn)" => "api.tools.add(t.name, t.inputSchema, (a) => t.handler(a));",
            "addTool(name, schema, fn)" => "api.addTool(t.name, t.inputSchema, (a) => t.handler(a));",
            "register(name, schema, fn)" => "api.register(t.name, t.inputSchema, (a) => t.handler(a));",
            _ => return None,
        }
    };
    Some(s.to_string())
}

fn render(tpl: &str, host: &str, sdk: &str, register: &str, inbound: &str, py: bool) -> String {
    // The placeholder already sits at the right indent in the template; only the
    // continuation lines of a multi-line snippet need the same prefix.
    let pad = if py { "    " } else { "      " };
    let inbound_indented = inbound
        .lines()
        .enumerate()
        .map(|(i, l)| {
            if i == 0 {
                l.to_string()
            } else {
                format!("{pad}{l}")
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    tpl.replace("__HOST__", host)
        .replace("__SDK_PACKAGE__", sdk)
        .replace("__TOOL_REGISTER__", register)
        .replace("__INBOUND_CALL__", &inbound_indented)
}

pub fn setup(args: &SetupArgs) -> i32 {
    let v = match load_answers_setup(args) {
        Ok(v) => v,
        Err(rc) => return rc,
    };
    let profile = build_profile(&v);
    let verdict = profile["verdict"].as_str().unwrap_or("blocked");
    if verdict != "ready" {
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "applied": false,
                "verdict": verdict,
                "reason": "profile is not ready; run `aimail agent doctor --answers …` to see which check failed",
                "checks": profile["checks"],
            }))
            .unwrap_or_default()
        );
        return if verdict == "unsupported" { 4 } else { 5 };
    }

    let host = profile["host"]["name"].as_str().unwrap_or("host");
    let runtime = profile["host"]["runtime"]
        .as_str()
        .unwrap_or("node")
        .to_string();
    let py = runtime == "python" || runtime == "py";
    let plugin_dir = profile["plugin"]["dir"].as_str().unwrap_or("");
    let skills_dir = profile["skills"]["dir"].as_str().unwrap_or("");
    let shape = profile["api_shape"].as_str().unwrap_or("");
    let sdk_pkg = profile["sdk"]["package"].as_str().unwrap_or("");
    let inbound = profile["inbound"]["call"].as_str().unwrap_or("");

    let register = match tool_register_snippet(shape, &runtime) {
        Some(r) => r,
        None => {
            eprintln!("setup: cannot express {shape:?} for runtime {runtime}");
            return 4;
        }
    };

    let name = slug(host);
    let adapter_dir: PathBuf = Path::new(plugin_dir).join(format!("aimail-{name}"));
    let entry = adapter_dir.join(if py { "adapter.py" } else { "index.mjs" });
    let tpl = if py { TPL_PY } else { TPL_NODE };
    let body = render(tpl, host, sdk_pkg, &register, inbound, py);
    let skill_path = Path::new(skills_dir)
        .join(crate::core::contract::agent_skill_name())
        .join("SKILL.md");

    let files = vec![
        (entry.clone(), body.clone()),
        (skill_path.clone(), SKILL_MD.to_string()),
    ];

    if !args.apply {
        let plan: Vec<Value> = files
            .iter()
            .map(|(p, c)| json!({"path": p, "bytes": c.len(), "sha256": sha256_hex(c.as_bytes())}))
            .collect();
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "applied": false,
                "dry_run": true,
                "host": host,
                "adapter_dir": adapter_dir,
                "files": plan,
                "sdk": profile["sdk"],
                "reload": profile["reload"],
            }))
            .unwrap_or_default()
        );
        return 0;
    }

    let state_dir = dirs_state();
    if let Err(e) =
        std::fs::create_dir_all(&adapter_dir).and_then(|_| std::fs::create_dir_all(&state_dir))
    {
        eprintln!("setup: cannot create directories: {e}");
        return 1;
    }
    if let Some(parent) = skill_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    let mut written: Vec<Value> = Vec::new();
    for (path, content) in &files {
        if let Err(e) = std::fs::write(path, content) {
            eprintln!("setup: cannot write {}: {e}", path.display());
            return 1;
        }
        written.push(json!({"path": path, "sha256": sha256_hex(content.as_bytes())}));
    }

    // reload hook (opt-in via --reload, default on for --apply)
    let reload_mode = profile["reload"]["mode"].as_str().unwrap_or("none");
    let reload_cmd = profile["reload"]["cmd"].as_str().unwrap_or("");
    let mut reload_ok = Value::Null;
    let mut reload_out = String::new();
    if args.reload {
        match reload_mode {
            "command" if !reload_cmd.is_empty() => match sh(reload_cmd) {
                Some(o) => {
                    reload_ok = json!(true);
                    reload_out = o.chars().take(400).collect();
                }
                None => reload_ok = json!(false),
            },
            "none" => reload_ok = json!(true),
            _ => reload_ok = json!(false),
        }
    }

    let state = json!({
        "host": host,
        "slug": name,
        "adapter_dir": adapter_dir,
        "files": written,
        "sdk": profile["sdk"],
        "reload": {"mode": reload_mode, "cmd": reload_cmd, "ok": reload_ok, "output": reload_out},
        "created_at": chrono_now(),
    });
    let state_path = state_dir.join(format!("adapter-{name}.json"));
    if let Err(e) = std::fs::write(
        &state_path,
        serde_json::to_string_pretty(&state).unwrap_or_default(),
    ) {
        eprintln!("setup: cannot write state {}: {e}", state_path.display());
        return 1;
    }

    println!(
        "{}",
        serde_json::to_string_pretty(&json!({
            "applied": true,
            "host": host,
            "adapter_dir": adapter_dir,
            "files": state["files"],
            "sdk": state["sdk"],
            "reload": state["reload"],
            "state": state_path,
        }))
        .unwrap_or_default()
    );
    0
}

// ───────────────────────────── uninstall ─────────────────────────────

pub fn uninstall(args: &UninstallArgs) -> i32 {
    let Some(host) = args.host.as_deref().filter(|h| !h.is_empty()) else {
        eprintln!("usage: aimail agent uninstall --host <name>");
        return 2;
    };
    let name = slug(host);
    let state_path = dirs_state().join(format!("adapter-{name}.json"));
    let raw = match std::fs::read_to_string(&state_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!(
                "uninstall: no state for {host} ({}): {e}",
                state_path.display()
            );
            return 1;
        }
    };
    let state: Value = serde_json::from_str(&raw).unwrap_or(Value::Null);
    let mut removed = 0usize;
    let mut kept = 0usize;
    if let Some(files) = state["files"].as_array() {
        for f in files {
            let path = f["path"].as_str().unwrap_or("");
            let want = f["sha256"].as_str().unwrap_or("");
            if path.is_empty() {
                continue;
            }
            match std::fs::read(path) {
                Ok(bytes) if sha256_hex(&bytes) == want => {
                    if std::fs::remove_file(path).is_ok() {
                        removed += 1;
                    }
                }
                Ok(_) => {
                    kept += 1; // user edited it — do not delete
                }
                Err(_) => {}
            }
        }
    }
    if let Some(dir) = state["adapter_dir"].as_str() {
        let _ = std::fs::remove_dir(dir);
    }
    let _ = std::fs::remove_file(&state_path);
    println!(
        "{}",
        serde_json::to_string_pretty(
            &json!({"removed": removed, "kept_modified": kept, "state": state_path})
        )
        .unwrap_or_default()
    );
    0
}

fn dirs_state() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    Path::new(&home).join(".aimail").join("state")
}

fn chrono_now() -> String {
    // seconds since epoch is enough for an audit trail; no chrono dependency.
    match std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) {
        Ok(d) => format!("{}", d.as_secs()),
        Err(_) => "0".to_string(),
    }
}

/// Only allow safe characters into shell, preventing strange characters in self-description from becoming commands.
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

// ───────────────────── package-mechanism hosts (adapted five) ─────────────────────

pub struct PackageArgs {
    pub host: String,
    pub apply: bool,
}

/// Determination for package installation step: `kind == "spawn"` + **adaptation package actually appears in argv** + not running in a container.
///
/// "Not running in a container" is judged by **structure** (`when.runtime == "docker"`), not by serialized text: non-docker steps'
/// `when.runtime_not = "docker"` also contains the string "docker" after serialization, excluding by text would filter out the host **local** package installation
/// steps together —— hermes therefore hits "no package installation step in platform table" on a clean machine, dry-run directly exits 5.
fn is_local_package_step(st: &Value, pkg: &str) -> bool {
    if st.get("kind").and_then(Value::as_str) != Some("spawn") {
        return false;
    }
    let names_pkg = st
        .get("argv")
        .and_then(Value::as_array)
        .map(|argv| {
            argv.iter()
                .filter_map(Value::as_str)
                .any(|a| a.contains(pkg))
        })
        .unwrap_or(false);
    let container = st
        .get("when")
        .and_then(|w| w.get("runtime"))
        .and_then(Value::as_str)
        == Some("docker");
    names_pkg && !container
}

/// Adapter package each adapted host installs through its own package manager.
fn adapter_package(host: &str) -> &'static str {
    match host {
        "openclaw" => "agent-aimail",
        "pi" => "agent-aimail",
        "dsh" => "agent-aimail",
        _ => "agent-aimail", // hermes / deerflow
    }
}

fn pkg_present(pkg: &str, home: &Path, python: &str) -> String {
    if pkg == "agent-aimail" && !python.is_empty() {
        if let Some(v) = sh(&format!(
            "{} -c \"import importlib.metadata as m;print(m.version('{}'))\" 2>/dev/null",
            shell_escape(python),
            pkg
        ))
        .filter(|v| !v.is_empty())
        {
            return format!("import ok, version {v}");
        }
    }
    if let Some(hit) = sh(&format!(
        "find {} -maxdepth 6 -type d -name {} 2>/dev/null | head -1",
        shell_escape(&home.to_string_lossy()),
        shell_escape(pkg)
    )) {
        if !hit.is_empty() {
            return hit;
        }
    }
    if let Some(root) = sh("npm root -g 2>/dev/null") {
        if !root.is_empty() && Path::new(&root).join(pkg).is_dir() {
            return format!("{root}/{pkg}");
        }
    }
    String::new()
}

/// `aimail agent setup --host <name>`: adapted hosts install an adapter PACKAGE
/// through their own package manager — the platform table already encodes the exact
/// command. We run only the `spawn` steps that name the adapter package (never the
/// system-level `sdk_install` / register steps), then verify the package is in place.
pub fn package_setup(args: &PackageArgs) -> i32 {
    let Some(host_def) = crate::hosts::get(&args.host) else {
        eprintln!(
            "setup: unknown host {:?}; known hosts: {}",
            args.host,
            crate::hosts::order().join(", ")
        );
        return 2;
    };
    let pkg = adapter_package(&args.host);
    let home_dir = crate::core::detect::spec_json(None, Some(&args.host))
        .get("platforms")
        .and_then(|p| p.get(&args.host))
        .and_then(|p| p.get("home_dir"))
        .and_then(|h| h.as_str())
        .unwrap_or("")
        .to_string();
    let user_home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let host_home = if home_dir.is_empty() {
        PathBuf::from(&user_home)
    } else {
        Path::new(&user_home).join(home_dir)
    };

    let python = crate::hosts::runtime::python(host_def as &dyn crate::hosts::Host, &host_home, "");
    let steps: Vec<Value> =
        crate::hosts::exec::install_steps_json(host_def as &dyn crate::hosts::Host)
            .into_iter()
            .filter(|st| is_local_package_step(st, pkg))
            .collect();

    let already = pkg_present(pkg, &host_home, &python);

    let mut ctx = serde_json::Map::new();
    ctx.insert(
        "home".into(),
        json!(host_home.to_string_lossy().to_string()),
    );
    ctx.insert("python".into(), json!(python));
    ctx.insert("sid".into(), json!(""));
    ctx.insert("sdk".into(), json!(""));
    ctx.insert("scripts".into(), json!(""));
    ctx.insert("cfg".into(), json!({}));
    ctx.insert("runtime".into(), json!("local"));
    ctx.insert("container".into(), json!(""));
    ctx.insert("all_agents".into(), json!(false));
    ctx.insert("agents".into(), json!([]));

    let restart = all_restart_hints(&args.host, &ctx);

    if steps.is_empty() {
        let out = json!({
            "applied": false,
            "host": args.host,
            "mechanism": "package",
            "package": pkg,
            "installed": if already.is_empty() { Value::Null } else { json!(already) },
            "reason": "no package step in the platform table for this host",
            "restart": restart,
        });
        println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
        return if already.is_empty() { 5 } else { 0 };
    }

    if !args.apply {
        let cmds: Vec<String> = steps
            .iter()
            .map(|st| {
                st.get("argv")
                    .and_then(|a| a.as_array())
                    .map(|a| {
                        // dry-run prints **executable** commands: placeholders are expanded using the same ctx
                        // (`{python}`, etc.), cannot return the template as-is to the user as a command.
                        a.iter()
                            .filter_map(|v| v.as_str())
                            .map(|v| crate::core::steps::tmpl(v, &ctx))
                            .collect::<Vec<_>>()
                            .join(" ")
                    })
                    .unwrap_or_default()
            })
            .collect();
        let out = json!({
            "applied": false,
            "dry_run": true,
            "host": args.host,
            "mechanism": "package",
            "package": pkg,
            "installed": if already.is_empty() { Value::Null } else { json!(already) },
            "commands": cmds,
            "restart": restart,
        });
        println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
        return 0;
    }

    // The exit of `--apply` must be able to explain "not a single step was run": when the step's `when` guard does not hold on this machine (e.g.,
    // `{home}/hermes-agent` does not exist ⇒ no host to install on), it is not allowed to falsely report `applied: true`.
    let empty_when = json!({});
    let live: Vec<&Value> = steps
        .iter()
        .filter(|st| crate::core::steps::when_ok(st.get("when").unwrap_or(&empty_when), &ctx))
        .collect();
    if live.is_empty() {
        let guards: Vec<String> = steps
            .iter()
            .filter_map(|st| st.get("when"))
            .map(|w| crate::core::steps::tmpl(&w.to_string(), &ctx))
            .collect();
        let out = json!({
            "applied": false,
            "host": args.host,
            "mechanism": "package",
            "package": pkg,
            "installed": if already.is_empty() { Value::Null } else { json!(already.clone()) },
            "reason": format!(
                "no install step applies on this machine (step guards unmet: {})",
                guards.join(" ")
            ),
            "restart": restart,
        });
        println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
        return if already.is_empty() { 5 } else { 0 };
    }

    let rc = match crate::core::steps::run_steps(&args.host, &steps, &mut ctx, Path::new("")) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("setup: step failed: {e}");
            1
        }
    };
    let installed = pkg_present(pkg, &host_home, &python);
    let out = json!({
        "applied": true,
        "host": args.host,
        "mechanism": "package",
        "package": pkg,
        "steps_run": live.len(),
        "step_rc": rc,
        "installed": if installed.is_empty() { Value::Null } else { json!(installed) },
        "restart": restart,
    });
    println!("{}", serde_json::to_string_pretty(&out).unwrap_or_default());
    if installed.is_empty() {
        5
    } else {
        rc
    }
}

/// Restart hints come from the platform table's `print` steps (single source).
///
/// Only returns hints that are **valid locally**: the `when` of the `print` step uses the same guards as the install step, container-specific hints
/// (`when.runtime = "docker"`) do not appear in local mode —— otherwise local dry-run would give
/// lines like "docker restart {container}" that do not match the scenario and have unexpanded placeholders. Templates are expanded by the caller's
/// ctx (`{container}`/`{home}`, etc.).
fn all_restart_hints(host: &str, ctx: &serde_json::Map<String, Value>) -> Vec<String> {
    let spec = crate::core::detect::spec_json(None, Some(host));
    let empty_when = json!({});
    let mut out = Vec::new();
    if let Some(arr) = spec
        .get("platforms")
        .and_then(|p| p.get(host))
        .and_then(|p| p.get("install_steps"))
        .and_then(|s| s.as_array())
    {
        for st in arr {
            if !crate::core::steps::when_ok(st.get("when").unwrap_or(&empty_when), ctx) {
                continue;
            }
            if let Some(t) = st.get("text").and_then(|t| t.as_str()) {
                let low = t.to_lowercase();
                if low.contains("restart") || low.contains("reload") || t.contains("restart") {
                    out.push(crate::core::steps::tmpl(t, ctx).trim().to_string());
                }
            }
        }
    }
    out
}
