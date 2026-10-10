//! `aimail prompt` —— `cli/aimail:1517-1729`（`cmd_prompt`）的 Rust 复刻。
//!
//! 五个嵌套面：`list`（按名排序列规则）· `add` · `rm` · `create-file`（scaffold 角色文件）·
//! `test`（dry-run，**永不发送**）。
//!
//! 归属铁律：`prompt_rules` 写进 per-agent 绑定文件**只由 SDK 写** ⇒ CLI 只触发
//! `aimail_base.update_binding(sid, cfg, {"prompt_rules": rules})`；名字合法性判定也走 SDK
//! `prompt_rule_name_ok`（不复刻谓词）。角色文件三级查找（地址级 → 系统级 → `common.md` 兜底）
//! 与 `aimail_base._read_role_file` **同序**。
//!
//! 本切片：`list` / `add` / `rm` / `create-file` 已实现；`test`（L1–L5 单源匹配仿真）**响亮未移植**。

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::cmd::report::fail;

pub struct Args {
    pub system_id: String,
    pub agent: Option<String>,
    pub email: Option<String>,
    pub paction: String,
    pub name: String,
    pub file: String,
    pub disable: bool,
    /// add 面的关键词字段（与 Python 同：可重复 ⇒ 列表）
    pub k_subject: Vec<String>,
    pub k_body: Vec<String>,
    pub k_sender: Vec<String>,
    pub k_recipient: Vec<String>,
    /// test 面的输入（不做 split，整体字符串）
    pub subject: String,
    pub body: String,
    pub sender: String,
    pub to: Vec<String>,
    pub header: String,
    pub board_role: String,
}

struct Target {
    dir: PathBuf,
    email: String,
    jf: PathBuf,
    cfg: Value,
}

fn sdk_call(function: &str, args: &[Value]) -> Result<Value, String> {
    let root = crate::core::sdkroot::resolve_or_placeholder();
    crate::core::sdkcall::call_positional(
        "aimail_base",
        function,
        args,
        &json!({}),
        &root.path,
        std::time::Duration::from_secs(30),
        &[],
    )
    .map_err(|e| match &e {
        crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
        other => format!("{other:?}"),
    })
}

/// SDK 谓词；**失败必须响亮**（绝不用 `false` 冒充"名字非法"——那会把 SDK 不可用伪装成校验结果）。
fn rule_name_ok(name: &str) -> Result<bool, String> {
    sdk_call("prompt_rule_name_ok", &[json!(name)]).map(|v| v.as_bool().unwrap_or(false))
}

fn update_binding(sid: &str, cfg: &Value, patch: &Value) -> Result<(), String> {
    // 契约 v1.0 §4.1：经 SDK 门 `update`（判定与落盘在 SDK）；action 由 patch 字段派生。
    // 兼容保留 `updates`/`fields` 两种键名（SDK 侧读入不受影响）。
    let action = if patch.get("persona").is_some() {
        "persona"
    } else {
        "prompt"
    };
    let args = json!({
        "system_id": sid,
        "binding": cfg.clone(),
        "action": action,
        "updates": patch.clone(),
        "fields": patch.clone(),
    });
    crate::core::sdk::sdk_ops_call("update", &args, std::time::Duration::from_secs(60), &[])
        .map(|_| ())
        .map_err(|e| match &e {
            crate::core::sdk::AbiError::Call { msg, .. } => msg.clone(),
            other => format!("{other:?}"),
        })
}

/// 规则文件名（`_stem_from`：`{serial}_{filename}` 取下划线后段，无下划线 ⇒ 空）。
fn stem_from(name: &str) -> String {
    match name.split_once('_') {
        Some((_, rest)) => rest.to_string(),
        None => String::new(),
    }
}

fn stem_ok(stem: &str) -> bool {
    !stem.is_empty()
        && stem.len() <= 64
        && stem
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

/// `_role_path`：地址级 → 系统级 → `common.md` 兜底（与 `aimail_base._read_role_file` 同序）。
fn role_path(addr_role_dir: &Path, sys_role_dir: &Path, stem: &str) -> String {
    if stem.is_empty() {
        return String::new();
    }
    let p = addr_role_dir.join(format!("{stem}.md"));
    if p.is_file() {
        return p.to_string_lossy().to_string();
    }
    let p = sys_role_dir.join(format!("{stem}.md"));
    if p.is_file() {
        return p.to_string_lossy().to_string();
    }
    let c = sys_role_dir.join("common.md");
    if c.is_file() {
        return format!("{} (common.md fallback)", c.to_string_lossy());
    }
    String::new()
}

fn pyjson_str(v: &Value) -> String {
    crate::core::pyjson::str_python(Some(v))
}

/// 规则字段渲染（`list`）：`subject=[a | b]`；空/缺 ⇒ 不渲染（Python：`if not v: continue`）。
fn field_render(r: &Value, f: &str) -> Option<String> {
    let v = r.get(f)?;
    let empty = match v {
        Value::Null => true,
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        _ => false,
    };
    if empty {
        return None;
    }
    let items: Vec<String> = match v {
        Value::String(s) => vec![s.clone()],
        Value::Array(a) => a.iter().map(pyjson_str).collect(),
        other => vec![pyjson_str(other)],
    };
    Some(format!("{f}=[{}]", items.join(" | ")))
}

/// 目标解析（三处 `_fail` 文案逐字，与 `cli/aimail:1530-1549` 同）。
fn resolve_target(home: &Path, sid: &str, a: &Args) -> Result<Target, String> {
    let cfg_path = crate::core::config::gateway_config_path(sid);
    if !cfg_path.is_file() {
        return Err(format!("no system config: {}", cfg_path.to_string_lossy()));
    }
    let gcfg = crate::core::config::load_gateway_config_in(home, sid).unwrap_or_default();
    let agents = crate::cmd::address::list_agents(home, sid, &gcfg);
    let target = if let Some(em) = &a.email {
        agents.iter().find(|r| r.email == em.to_lowercase())
    } else if let Some(ag) = &a.agent {
        agents.iter().find(|r| {
            r.agent == *ag
                || r.email
                    .split('@')
                    .next()
                    .unwrap_or("")
                    .starts_with(ag.as_str())
        })
    } else {
        return Err(format!(
            "prompt needs -a <agent> or -e <email> to locate {}",
            crate::core::contract::binding_file()
        ));
    };
    let Some(t) = target else {
        if let Some(em) = &a.email {
            return Err(format!(
                "no local {} for {em} (run install on that agent first)",
                crate::core::contract::binding_file()
            ));
        }
        let mut names: Vec<String> = agents.iter().map(|r| r.agent.clone()).collect();
        names.sort();
        let known = if names.is_empty() {
            "none".to_string()
        } else {
            format!("{names:?}")
        };
        return Err(format!(
            "agent '{}' not found locally; known agents: {known}",
            a.agent.clone().unwrap_or_default()
        ));
    };
    let jf = home
        .join("systems")
        .join(sid)
        .join(crate::core::bridge_wire::addr_clean(&t.email))
        .join(crate::core::contract::binding_file());
    if !jf.is_file() {
        return Err(format!("missing {}", jf.to_string_lossy()));
    }
    let cfg: Value = std::fs::read_to_string(&jf)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| Value::Object(Map::new()));
    Ok(Target {
        // Row 不带目录字段 ⇒ 由 email 推 leaf 目录（与绑定文件落盘口径 `addr_clean(email)` 一致）
        dir: home
            .join("systems")
            .join(sid)
            .join(crate::core::bridge_wire::addr_clean(&t.email)),
        email: t.email.clone(),
        jf,
        cfg,
    })
}

fn rules_of(cfg: &Value) -> Vec<Value> {
    cfg.get("prompt_rules")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter(|r| r.is_object()).cloned().collect())
        .unwrap_or_default()
}

fn kws(v: &[String]) -> Vec<String> {
    v.iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// `aimail_base.read_prompt_rules` 的**过滤镜像**（L5 匹配必须用同一批规则）：
/// 仅 dict · 名字过 SDK 谓词 · `file` 非空 · `enabled is False` 跳过 · 至少一个非空字段 ⇒ 按名排序。
/// 说明：SDK 侧还对坏项打 WARN 日志；CLI 侧无 logger，静默丢弃（差异已登记）。
fn loader_rules(cfg: &Value) -> Vec<Value> {
    let raw = match cfg.get("prompt_rules").and_then(|v| v.as_array()) {
        Some(a) => a.clone(),
        None => return Vec::new(),
    };
    let mut kept: Vec<Value> = Vec::new();
    for r in raw.iter() {
        if !r.is_object() {
            continue;
        }
        let name = r.get("name").map(pyjson_str).unwrap_or_default();
        if !rule_name_ok(&name).unwrap_or(false) {
            continue;
        }
        let file = r.get("file").map(pyjson_str).unwrap_or_default();
        if file.trim().is_empty() {
            continue;
        }
        if r.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
            continue;
        }
        let has_field =
            ["subject", "body", "sender", "recipient"]
                .iter()
                .any(|k| match r.get(*k) {
                    None | Some(Value::Null) => false,
                    Some(Value::String(s)) => !s.trim().is_empty(),
                    Some(Value::Array(a)) => a.iter().any(|k| pyjson_str(k).trim() != ""),
                    Some(_) => false,
                });
        if !has_field {
            continue;
        }
        kept.push(r.clone());
    }
    kept.sort_by_key(|r| r.get("name").map(pyjson_str).unwrap_or_default());
    kept
}

pub fn run(a: &Args) -> i32 {
    let sid = a.system_id.clone();
    if sid.is_empty() {
        return fail("prompt needs --system-id");
    }
    let home = crate::core::home::aimail_home();
    let t = match resolve_target(&home, &sid, a) {
        Ok(t) => t,
        Err(msg) => return fail(&msg),
    };
    let addr_role_dir = t.dir.join("role_prompt");
    let sys_role_dir = home
        .join("systems")
        .join(&sid)
        .join("board")
        .join("role_prompt");
    let rules = rules_of(&t.cfg);

    match a.paction.as_str() {
        "list" => {
            if rules.is_empty() {
                println!("  (no prompt rules in {})", t.jf.to_string_lossy());
                return 0;
            }
            let mut sorted = rules.clone();
            sorted.sort_by_key(|r| r.get("name").map(pyjson_str).unwrap_or_default());
            for r in &sorted {
                let mut flds: Vec<String> = Vec::new();
                for f in ["subject", "body", "sender", "recipient"] {
                    if let Some(s) = field_render(r, f) {
                        flds.push(s);
                    }
                }
                let name = r.get("name").map(pyjson_str).unwrap_or_else(|| "-".into());
                let file = r.get("file").map(pyjson_str).unwrap_or_else(|| "-".into());
                let dis = if r.get("enabled").and_then(|v| v.as_bool()) == Some(false) {
                    "  [disabled]"
                } else {
                    ""
                };
                println!("  {name:<28} file={file}{dis}");
                println!(
                    "      {}",
                    if flds.is_empty() {
                        "(NO FIELD — loader skips this rule)".to_string()
                    } else {
                        flds.join("; ")
                    }
                );
                let rp = role_path(&addr_role_dir, &sys_role_dir, &file);
                println!(
                    "      role: {}",
                    if rp.is_empty() {
                        "MISSING (loader will skip on match)".to_string()
                    } else {
                        rp
                    }
                );
            }
            0
        }
        "add" => {
            let name = a.name.trim().to_string();
            let ok = match rule_name_ok(&name) {
                Ok(v) => v,
                Err(e) => return fail(&format!("prompt_rule_name_ok failed: {e}")),
            };
            if !ok {
                return fail(&format!(
                    "bad -n {name:?}: need {{serial}}_{{filename}} with serial 10-99 and filename [a-z0-9_-] (1-64 chars)"
                ));
            }
            let stem = if !a.file.trim().is_empty() {
                a.file.trim().to_string()
            } else {
                stem_from(&name)
            };
            if !stem_ok(&stem) {
                return fail(&format!(
                    "bad file stem {stem:?} (from -n or --file): [a-z0-9_-] 1-64 chars"
                ));
            }
            let mut rule = Map::new();
            rule.insert("name".into(), json!(name));
            rule.insert("file".into(), json!(stem));
            let mut any = false;
            for (f, vals) in [
                ("subject", kws(&a.k_subject)),
                ("body", kws(&a.k_body)),
                ("sender", kws(&a.k_sender)),
                ("recipient", kws(&a.k_recipient)),
            ] {
                if !vals.is_empty() {
                    rule.insert(f.into(), json!(vals));
                    any = true;
                }
            }
            if !any {
                return fail(
                    "need at least one non-empty keyword: --subject/--body/--sender/--recipient",
                );
            }
            if a.disable {
                rule.insert("enabled".into(), json!(false));
            }
            if rules
                .iter()
                .any(|r| r.get("name").map(pyjson_str) == Some(name.clone()))
            {
                return fail(&format!(
                    "rule {name} already exists — remove it first: aimail prompt rm -s {sid} -e {} -n {name}",
                    t.email
                ));
            }
            let mut next = rules.clone();
            next.push(Value::Object(rule));
            next.sort_by_key(|r| r.get("name").map(pyjson_str).unwrap_or_default());
            // 落盘**只经 SDK**（per-agent 绑定文件只由 SDK 写）
            if let Err(e) = update_binding(&sid, &t.cfg, &json!({"prompt_rules": next})) {
                return fail(&format!("update_binding failed: {e}"));
            }
            println!(
                "  added {name} → role_prompt/{}.md",
                stem_from(&name).max(stem.clone())
            );
            println!(
                "  {}: {}",
                crate::core::contract::binding_file(),
                t.jf.to_string_lossy()
            );
            if role_path(&addr_role_dir, &sys_role_dir, &stem).is_empty() {
                println!(
                    "  note: role file not present yet — scaffold it with: aimail prompt create-file -s {sid} -e {} -n {name}",
                    t.email
                );
            }
            0
        }
        "rm" => {
            let name = a.name.trim().to_string();
            let keep: Vec<Value> = rules
                .iter()
                .filter(|r| r.get("name").map(pyjson_str) != Some(name.clone()))
                .cloned()
                .collect();
            if keep.len() == rules.len() {
                return fail(&format!("rule {name} not found for this agent"));
            }
            if let Err(e) = update_binding(&sid, &t.cfg, &json!({"prompt_rules": keep})) {
                return fail(&format!("update_binding failed: {e}"));
            }
            println!("  removed {name} ({})", t.jf.to_string_lossy());
            0
        }
        "create-file" => {
            let name = a.name.trim().to_string();
            let ok = match rule_name_ok(&name) {
                Ok(v) => v,
                Err(e) => return fail(&format!("prompt_rule_name_ok failed: {e}")),
            };
            if !ok {
                return fail(&format!(
                    "bad -n {name:?}: need {{serial}}_{{filename}} with serial 10-99 and filename [a-z0-9_-] (1-64 chars)"
                ));
            }
            let stem = if !a.file.trim().is_empty() {
                a.file.trim().to_string()
            } else {
                stem_from(&name)
            };
            if !stem_ok(&stem) {
                return fail(&format!("bad file stem {stem:?}: [a-z0-9_-] 1-64 chars"));
            }
            let _ = std::fs::create_dir_all(&addr_role_dir);
            let p = addr_role_dir.join(format!("{stem}.md"));
            if p.exists() {
                return fail(&format!("already exists: {}", p.to_string_lossy()));
            }
            let body = format!(
                "# {stem}\n\n\
Role prompt injected when a prompt rule (or the gateway\n\
X-AIMail-Prompt header) selects `{stem}` for an inbound mail.\n\n\
Template variables available: {{{{AGENTMAIL_ADDRESS}}}}, {{{{FROM_ROLE}}}}, {{{{INQUIRY_SUBJECT}}}}, {{{{SOUL_MD_CONTENT}}}}, {{{{SKILLS_LIST}}}}.\n\n\
Describe how this agent should behave for that class of mail here.\n"
            );
            if let Err(e) = std::fs::write(&p, body) {
                return fail(&format!("write failed: {e}"));
            }
            println!("  created {}", p.to_string_lossy());
            0
        }
        "test" => {
            // ── L1–L5 单源匹配仿真（**永不发送**）；匹配判定全部委托 SDK ──
            let subj = a.subject.clone();
            let body = a.body.clone();
            let sender = a.sender.clone();
            let recp = a.to.join(" ");
            let s_l = subj.to_lowercase();
            let hit = |layer: &str, stem: &str| -> i32 {
                let path = role_path(&addr_role_dir, &sys_role_dir, stem);
                println!("  {layer} → file={stem}");
                println!(
                    "  role: {}",
                    if path.is_empty() {
                        "MISSING (no injection; chain continues at runtime)".to_string()
                    } else {
                        path
                    }
                );
                0
            };
            // L1 内建 [WHOAMI]
            if s_l.starts_with("[whoami]") {
                return hit("L1 [WHOAMI] (built-in)", "whoami");
            }
            // L2 内建 welcome（标记 + 三标签，与 preprocess 同字面量）
            let marker = s_l.contains("welcome to aimail world");
            let labels_ok = ["persona", "signature", "current_time"].iter().all(|k| {
                body.lines()
                    .any(|ln| ln.trim_start().starts_with(&format!("{k}:")))
            });
            if marker && labels_ok {
                return hit("L2 welcome (built-in)", "role_calibrator");
            }
            let mut line = "  L1 [WHOAMI]: no    L2 welcome: no".to_string();
            if marker || labels_ok {
                line.push_str(&format!(
                    "  (MISMATCH warn at runtime: marker={marker} labels={labels_ok})"
                ));
            }
            println!("{line}");
            // L3 板级（网关注入；--board-role 仿真）
            if !a.board_role.trim().is_empty() {
                return hit("L3 board (simulated)", a.board_role.trim());
            }
            println!("  L3 board: gateway-injected — pass --board-role to simulate");
            // L4 X-AIMail-Prompt 头
            if !a.header.trim().is_empty() {
                let stem = a.header.trim();
                if !role_path(&addr_role_dir, &sys_role_dir, stem).is_empty() {
                    return hit("L4 X-AIMail-Prompt (gateway header)", stem);
                }
                println!(
                    "  L4 header={}: role file MISSING (incl. common.md) — runtime falls through to L5",
                    crate::core::pyjson::repr_python(&json!(stem))
                );
            }
            // L5 本机 prompt_rules：先过 `loader_rules`（与 SDK `read_prompt_rules` 同过滤），
            // 匹配判定**委托 SDK** `prompt_rule_matches`（不复刻"字段内或 / 字段间且"的裁决）。
            let sorted = loader_rules(&t.cfg);
            let mut matched_any = false;
            for rule in &sorted {
                let name = rule.get("name").map(pyjson_str).unwrap_or_default();
                let is_match = match sdk_call(
                    "prompt_rule_matches",
                    &[
                        rule.clone(),
                        json!(subj),
                        json!(body),
                        json!(sender),
                        json!(recp),
                    ],
                ) {
                    Ok(v) => v.as_bool().unwrap_or(false),
                    Err(e) => return fail(&format!("prompt_rule_matches failed: {e}")),
                };
                if !is_match {
                    println!("  L5 miss {name}");
                    continue;
                }
                let file = rule.get("file").map(pyjson_str).unwrap_or_default();
                let path = role_path(&addr_role_dir, &sys_role_dir, &file);
                if path.is_empty() {
                    println!(
                        "  L5 hit {name} but role file '{file}' MISSING (incl. common.md) — next rule"
                    );
                    continue;
                }
                println!("  L5 HIT {name} → file={file}");
                println!("  role: {path}"); // 路径可能带 common.md fallback 标注 = 运行时实际注入内容
                matched_any = true;
                break;
            }
            if !matched_any {
                println!("  L5: no rule matched — chain falls through (default prompt)");
            }
            println!(
                "  (matched against: subject={} sender={} to={})",
                crate::core::pyjson::repr_python(&json!(subj)),
                crate::core::pyjson::repr_python(&json!(sender)),
                crate::core::pyjson::repr_python(&json!(recp))
            );
            0
        }
        other => fail(&format!("unknown prompt action: {other}")),
    }
}
