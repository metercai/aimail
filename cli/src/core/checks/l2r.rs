//! L2r 探针：平台运行时资源就绪 —— `cli/check_status.py:1706-1853` 的复刻。
//!
//! 结构：平台定位（注册表 `detect`）→ `health_checks` 表驱动（8 种 kind）→ 板级资源
//! （`board/role_prompt/common.md`）→ 宿主对运行时载荷的引用（注册表 `payload_refs`）。
//!
//! 不支持的 kind 会**显式出红记录**（不是静默跳过）：注册表将来加了新 kind 而这里没实现时，
//! 必须看得见 —— 静默跳过正是"漏判级别"那类假绿的来源。

use crate::core::check::Check;
use crate::core::checks::l0::{field_str, read_gw_cfg, Ctx};
use crate::core::{config, contract, platforms};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Command;

/// 已实现的 `health_checks[].kind`（注册表当前用到 7 种；`glob_dir_any` 一并实现备用）。
const SUPPORTED_KINDS: &[&str] = &[
    "file_contains",
    "file_contains_alt",
    "file_exists",
    "file_exists_any",
    "glob_dir_any",
    "command_match",
    "pointer_match",
    "yaml_toolsets",
];

/// 模板填充（`{home}` / `{user_home}` / `{sid}`）。
fn fill(template: &str, home: &str, user_home: &str, sid: &str) -> String {
    template
        .replace("{home}", home)
        .replace("{user_home}", user_home)
        .replace("{sid}", sid)
}

pub fn runtime(c: &mut Check, sid: &str, ctx: &Ctx, prog_root: &Path) {
    let gw = read_gw_cfg(ctx, sid).unwrap_or_else(|| serde_json::json!({}));
    let sh = field_str(&gw, "system_home").to_string();
    let mut platform = String::new();
    if !sh.is_empty() && Path::new(&sh).is_dir() {
        let detected = platforms::detect_platform_from_home(Path::new(&sh));
        if !detected.is_empty() && detected != "unknown" {
            platform = detected.to_string();
        }
    }

    if platform.is_empty() {
        // system_home 缺失/无效已在 L0 报；此处只提示平台不可定位
        c.add(
            "runtime",
            "platform-locatable",
            false,
            "platform root not locatable (system_home missing/invalid)",
            &format!(
                "Run: aimail install --home <platform-root> --system-id {}",
                if sid.is_empty() { "<sid>" } else { sid }
            ),
        );
        return;
    }
    c.add(
        "runtime",
        "platform-locatable",
        true,
        &format!("{platform} @ {sh}"),
        "",
    );

    let fix_install = format!("python -m aimail.install --type {platform} --home {sh}");
    let user_home = ctx.user_home.to_string_lossy().into_owned();
    run_checks(
        c,
        &platforms::health_checks(&platform),
        &sh,
        &user_home,
        sid,
        &fix_install,
    );

    // 通用：板级资源（a2a board 角色查找链）
    let rp = config::system_dir_in(&ctx.aimail_home, sid)
        .join("board")
        .join("role_prompt")
        .join("common.md");
    let present = rp.is_file();
    c.add(
        "runtime",
        "board-resources",
        present,
        if present {
            "board/role_prompt/common.md present"
        } else {
            "board/role_prompt/common.md missing (a2a role rendering falls back empty)"
        },
        &fix_install,
    );

    payload_refs(c, &platform, &sh, &user_home, prog_root);
}

/// `_run_l2_checks`：注册表 `health_checks` 逐项执行。
fn run_checks(
    c: &mut Check,
    checks: &[Value],
    home: &str,
    user_home: &str,
    sid: &str,
    fix_install: &str,
) {
    for ch in checks {
        let kind = ch.get("kind").and_then(Value::as_str).unwrap_or("");
        let cid = ch.get("id").and_then(Value::as_str).unwrap_or("check");
        let fix = {
            let raw = ch.get("fix").and_then(Value::as_str).unwrap_or("");
            let chosen = if raw.is_empty() { fix_install } else { raw };
            fill(chosen, home, user_home, sid)
        };
        let msg_ok = ch.get("ok_text").and_then(Value::as_str).unwrap_or("ok");
        let msg_fail = ch
            .get("fail_text")
            .and_then(Value::as_str)
            .unwrap_or("fail");
        let mut ok = false;
        let mut path = String::new();

        if !SUPPORTED_KINDS.contains(&kind) {
            c.add(
                "runtime",
                cid,
                false,
                &format!("unsupported check kind (not ported yet): '{kind}'"),
                &fix,
            );
            continue;
        }

        match kind {
            "file_contains" | "file_contains_alt" | "file_exists" => {
                let mut cands = vec![fill(
                    ch.get("path").and_then(Value::as_str).unwrap_or(""),
                    home,
                    user_home,
                    sid,
                )];
                if let Some(alt) = ch.get("alt").and_then(Value::as_str) {
                    if !alt.is_empty() {
                        cands.push(fill(alt, home, user_home, sid));
                    }
                }
                for cand in &cands {
                    let p = Path::new(cand);
                    if !p.is_file() {
                        continue;
                    }
                    if kind == "file_exists" {
                        ok = true;
                        path = cand.clone();
                        break;
                    }
                    let marker = ch.get("marker").and_then(Value::as_str).unwrap_or("");
                    let text = std::fs::read_to_string(p).unwrap_or_default();
                    if text.contains(marker) {
                        ok = true;
                        path = cand.clone();
                        break;
                    }
                }
                if path.is_empty() {
                    if let Some(first) = cands.first() {
                        path = first.clone();
                    }
                }
            }
            "file_exists_any" => {
                let pats = ch
                    .get("paths")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                for pat in pats {
                    let filled = fill(pat.as_str().unwrap_or(""), home, user_home, sid);
                    if glob_matches(&filled) {
                        ok = true;
                        path = filled;
                        break;
                    }
                }
            }
            "glob_dir_any" => {
                let g = fill(
                    ch.get("glob").and_then(Value::as_str).unwrap_or(""),
                    home,
                    user_home,
                    sid,
                );
                ok = glob_matches(&g);
            }
            "command_match" => {
                let argv: Vec<String> = ch
                    .get("argv")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .map(|x| fill(x.as_str().unwrap_or(""), home, user_home, sid))
                            .collect()
                    })
                    .unwrap_or_default();
                let (out, ran) = if !argv.is_empty() {
                    match Command::new(&argv[0]).args(&argv[1..]).output() {
                        Ok(o) => (
                            format!(
                                "{}{}",
                                String::from_utf8_lossy(&o.stdout),
                                String::from_utf8_lossy(&o.stderr)
                            ),
                            o.status.success(),
                        ),
                        Err(_) => (String::new(), false),
                    }
                } else {
                    (String::new(), false)
                };
                if ran {
                    let want = ch
                        .get("match")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_lowercase();
                    ok = out.to_lowercase().contains(&want);
                    path = format!("{} match={}", argv.join(" "), want);
                } else {
                    let fb = fill(
                        ch.get("fallback_glob")
                            .and_then(Value::as_str)
                            .unwrap_or(""),
                        home,
                        user_home,
                        sid,
                    );
                    ok = if fb.is_empty() {
                        false
                    } else {
                        glob_matches(&fb)
                    };
                    let argv_txt = if argv.is_empty() {
                        "无 argv".to_string()
                    } else {
                        argv.join(" ")
                    };
                    path = if fb.is_empty() {
                        format!("命令不可用: {argv_txt}")
                    } else {
                        format!("{fb} (命令不可用: {argv_txt})")
                    };
                }
            }
            "pointer_match" => {
                let p = fill(
                    ch.get("path").and_then(Value::as_str).unwrap_or(""),
                    home,
                    user_home,
                    sid,
                );
                ok = std::fs::read_to_string(&p)
                    .ok()
                    .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                    .and_then(|v| {
                        v.get("system_id")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                    })
                    .as_deref()
                    == Some(sid)
                    && Path::new(&p).is_file();
                path = p;
            }
            "yaml_toolsets" => {
                let token = contract::agent_skill_name();
                let configs = ch
                    .get("configs")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                'outer: for pat in configs {
                    let filled = fill(pat.as_str().unwrap_or(""), home, user_home, sid);
                    for cand in glob_list(&filled) {
                        if yaml_has_toolset_token(&cand, token) {
                            ok = true;
                            break 'outer;
                        }
                    }
                }
            }
            _ => {}
        }

        let msg = if ok {
            msg_ok.to_string()
        } else {
            let suffix = if !path.is_empty()
                && matches!(
                    kind,
                    "file_contains" | "file_contains_alt" | "file_exists" | "command_match"
                ) {
                format!(": {path}")
            } else {
                String::new()
            };
            format!("{msg_fail}{suffix}")
        };
        c.add("runtime", cid, ok, &msg, &fix);
    }
}

/// `platform_toolsets` 下是否有取值（映射/列表）含契约 skill/toolset 名。
fn yaml_has_toolset_token(path: &Path, token: &str) -> bool {
    let Ok(text) = std::fs::read_to_string(path) else {
        return false;
    };
    let Ok(doc) = yaml_rust2::YamlLoader::load_from_str(&text) else {
        return false;
    };
    let Some(root) = doc.first() else {
        return false;
    };
    let Some(pt) = root["platform_toolsets"].as_hash() else {
        return false;
    };
    pt.iter().any(|(_k, v)| match v {
        yaml_rust2::Yaml::Hash(h) => h.keys().any(|k| k.as_str() == Some(token)),
        yaml_rust2::Yaml::Array(a) => a.iter().any(|e| e.as_str() == Some(token)),
        _ => false,
    })
}

/// `glob.glob(pattern)` 是否有匹配（Python 语义：`*` 不跨 `/`）。
fn glob_list(pattern: &str) -> Vec<PathBuf> {
    match glob::glob(pattern) {
        Ok(paths) => paths.flatten().collect(),
        Err(_) => Vec::new(),
    }
}

fn glob_matches(pattern: &str) -> bool {
    !glob_list(pattern).is_empty()
}

/// `_check_payload_refs`：宿主配置里引用的载荷文件是否还在。
///
/// 载荷目录 = 程序根 `/mcp`（`runtime_bundle.payload_dir("mcp")`）；宿主文件路径由注册表
/// `payload_refs` 声明（CLI 零平台字面量）。查不到的引用 = 改名/prune 后的静默断链。
fn payload_refs(c: &mut Check, platform: &str, home: &str, user_home: &str, prog_root: &Path) {
    let refs = platforms::payload_refs(platform);
    if refs.is_empty() {
        return;
    }
    let dest = prog_root.join("mcp");
    let dest_str = dest.to_string_lossy().into_owned();
    let variants = if user_home.is_empty() {
        vec![dest_str.clone()]
    } else {
        vec![dest_str.clone(), dest_str.replace(user_home, "~")]
    };
    for tpl in refs {
        let f = fill(&tpl, home, user_home, "");
        if !Path::new(&f).is_file() {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&f) else {
            continue;
        };
        let mut names: Vec<String> = variants
            .iter()
            .flat_map(|v| refs_in_text(&text, v))
            .collect();
        names.sort();
        names.dedup();
        if names.is_empty() {
            continue;
        }
        let missing: Vec<&String> = names
            .iter()
            .filter(|n| !dest.join(n.as_str()).is_file())
            .collect();
        let detail = if missing.is_empty() {
            format!("{f}: {}", names.join(", "))
        } else {
            format!(
                "{f}: missing {}",
                missing
                    .iter()
                    .map(|s| s.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        };
        c.add(
            "runtime",
            "host-payload-refs",
            missing.is_empty(),
            &detail,
            &format!(
                "python3 {} install mcp",
                // 提示里的脚本路径按**部署形态**给：已装程序根下的源码快照副本
                // （`toolkit_dir()/cli/runtime_bundle.py`）。源码检出的 Python 侧给的是
                // 仓库路径 —— 两者在开发机上天然不同，parity 侧对该字段做窄归一。
                prog_root
                    .join("aimail-src")
                    .join("cli")
                    .join("runtime_bundle.py")
                    .display()
            ),
        );
    }
}

/// 文本里 `<variant>/<名字>.py` 形式的引用（对应 Python 的 `re.escape(v)+r"/([\w.@+-]+\.py)"`）。
fn refs_in_text(text: &str, variant: &str) -> Vec<String> {
    let needle = format!("{variant}/");
    let mut out = Vec::new();
    let mut idx = 0usize;
    while let Some(pos) = text[idx..].find(&needle) {
        let start = idx + pos + needle.len();
        let mut end = start;
        for (i, ch) in text[start..].char_indices() {
            if ch.is_alphanumeric() || matches!(ch, '_' | '.' | '@' | '+' | '-') {
                end = start + i + ch.len_utf8();
            } else {
                break;
            }
        }
        let token = &text[start..end];
        if token.ends_with(".py") {
            out.push(token.to_string());
        }
        idx = if end > start { end } else { start + 1 };
        if idx >= text.len() {
            break;
        }
    }
    out
}
