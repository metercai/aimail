//! `cli/setup_system.py` 的 Rust 复刻 —— **L1 只激活/复用 worker**（P1 咽喉）。
//!
//! 契约（`cli/aimail:1076-1240` 的 `cmd_ensure_system` 依赖它）：
//! · SDK 反调入口，**绝不**做平台接线、**绝不**部署桥（跳过它们才是打断 install↔plugin 调用环的关键）；
//! · 两种路径：Path A = `admin_key`（已激活系统的复用/重置）；Path B = `product_code`（新系统激活）；
//! · 返回 Python 同形 dict（`{success, error?, system_id?, path?, admin_key?}`），由调用方决定怎么播。
//!
//! 照抄点（易漏，逐条来自 Python 原文）：
//! 1 `webhook_host` 三态：显式/env → admin_key 路径**继承已有 cfg**（reset 语义；实测探测出的 IPv6
//!   地址曾覆写 NAT 公网值）→ `detect_webhook_host` + `judge_deliverable`（**裸 host 不可投递 ⇒ 不写**，
//!   保住"无桥 ⇒ 注册本机端点"的既有状态）；
//! 2 Path A 的 `whoami` 身份对齐：platform 级 key **拒收**（必须留在网关）、system_id 可从 key 采纳、
//!   不符则报错；`whoami` 拿不到时只 warn **不臆断**；
//! 3 Path A 的"空参数继承已有值"（reset 语义）+ 写回后把 prev 里未覆盖的业务字段**补回**
//!   （`default_agent_name` 曾因此丢失）；
//! 4 `_downgrade_to_domain_admin_key`：whoami 预检（已是 agent/domain 级 ⇒ 不降级）+ 配置里已有本域
//!   domain key ⇒ 复用不轮换 + 无域可收窄 ⇒ 保留系统级 key + 失败文案含 "privilege level"/"at or above"
//!   ⇒ 视为"无需降级"（**不得**把受限 key 当系统 key 落盘）；
//! 5 系统级 key **拿到即落盘**（`.system_raw_key.key`），与后续降级成功与否无关；受限 key 不落盘。

use serde_json::{json, Map, Value};

use crate::core::{config, home};

/// 原始系统级 key 文件（`setup_system._persist_system_raw_key`）。
pub const SYSTEM_RAW_KEY_FILE: &str = ".system_raw_key.key";

#[derive(Default, Clone)]
pub struct SetupArgs {
    pub gateway_url: String,
    pub system_id: String,
    pub admin_key: String,
    pub product_code: String,
    pub system_name: String,
    pub domain: String,
    pub save_raw_snapshots: bool,
    pub manager_address: String,
    pub webhook_host: String,
    pub system_home: String,
}

/// Python 侧的日志口径（照抄，别"顺手"升级）：
/// · 默认 root 级别是 **WARNING** ⇒ `logger.info/debug` **不输出**；
/// · 无 handler 时走 lastResort ⇒ **裸消息**（无 `LEVEL:name:` 前缀）打到 stderr；
/// · 一律 stderr（stdout 是"恰一行 JSON"的契约载体，绝不能被日志污染）。
fn log(level: &str, msg: &str) {
    if level == "WARNING" || level == "ERROR" {
        eprintln!("{msg}");
    }
}

fn err(msg: &str) -> Value {
    json!({ "success": false, "error": msg })
}

/// `setup(**kwargs)`（`setup_system.py:466-630`）。
pub fn setup(a: &SetupArgs) -> Value {
    if a.gateway_url.is_empty() {
        return err("gateway_url is required");
    }
    let webhook_host = resolve_webhook_host(a);

    // ── Path A: admin_key（已激活系统的复用/重置）──────────────────────────────
    if !a.admin_key.is_empty() {
        let mut system_id = a.system_id.clone();
        let me = crate::core::gateway::whoami(&a.gateway_url, &a.admin_key, "");
        let me_empty = matches!(&me, Value::Object(m) if m.is_empty());
        if !me_empty {
            let cat = me
                .get("category")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let scope = me
                .get("scope")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            let ksid = me
                .get("system_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .to_string();
            if cat == "platform" || scope == "platform" {
                return err(
                    "the key is the gateway-side admin key (platform scope) — it must stay on the \
                     gateway and is not used for agent integration. Use the agent-side system key \
                     file (<storage dir>/<system id>.system.key, printed once when the gateway \
                     starts) with -k.",
                );
            }
            if !ksid.is_empty() && system_id.is_empty() {
                log("INFO", &format!("system_id taken from the key: {ksid}"));
                system_id = ksid;
            } else if !ksid.is_empty() && ksid != system_id {
                return err(&format!(
                    "key/system mismatch: this key belongs to system {ksid} but system_id {system_id} \
                     was given — pass the key of {system_id}, or use the key of {ksid} without \
                     --system-id."
                ));
            }
        } else {
            log(
                "WARNING",
                &format!(
                    "could not verify the admin key against {} (gateway unreachable or key unknown) — continuing",
                    a.gateway_url
                ),
            );
        }
        if system_id.is_empty() {
            return err("system_id is required for admin_key path");
        }

        // reset 语义：已有配置存在时，空参数继承已有值（只重写核心连接参数）
        let prev = config::load_gateway_config(&system_id)
            .map(|c| c.to_json())
            .unwrap_or_default();
        let pget = |k: &str| -> String {
            prev.get(k)
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        };
        let explicit_name = config::env_val("INTEGRATE_NAME_EXPLICIT", "") == "true";
        let prev_has_snaps = prev.contains_key("save_raw_snapshots");
        let snaps = if a.save_raw_snapshots || !prev_has_snaps {
            a.save_raw_snapshots
        } else {
            prev.get("save_raw_snapshots")
                .and_then(Value::as_bool)
                .unwrap_or(false)
        };
        let domain = if a.domain.is_empty() {
            let d = pget("domain");
            if d.is_empty() {
                "admin.local".to_string()
            } else {
                d
            }
        } else {
            a.domain.clone()
        };
        let system_name = if explicit_name {
            a.system_name.clone()
        } else {
            let p = pget("system_name");
            if p.is_empty() {
                a.system_name.clone()
            } else {
                p
            }
        };
        let cfg = config::GatewayConfig {
            gateway_url: a.gateway_url.clone(),
            admin_key: a.admin_key.clone(),
            system_id: system_id.clone(),
            system_name,
            save_raw_snapshots: snaps,
            domain,
            manager_address: if a.manager_address.is_empty() {
                pget("manager_address")
            } else {
                a.manager_address.clone()
            },
            webhook_host: if webhook_host.is_empty() {
                pget("webhook_host")
            } else {
                webhook_host.clone()
            },
            system_home: if a.system_home.is_empty() {
                pget("system_home")
            } else {
                a.system_home.clone()
            },
            ..Default::default()
        };
        if let Err(e) = config::save_gateway_config_in(&home::aimail_home(), &system_id, &cfg) {
            return err(&format!("failed to write config: {e}"));
        }
        // 把 prev 中未被覆盖的业务字段补回（通用保护；default_agent_name 曾丢）
        restore_prev_keys(&system_id, &prev);

        // 注意：给降级的 domain 是**未套默认值**的那个（Python `domain or prev.get("domain","")`）；
        // 写进 cfg 的是带了 "admin.local" 默认值的那份 —— 两者不同，别合并
        // （实测：合并会让"无域"的系统误降级出 domain key）。
        let downgrade_domain = if a.domain.is_empty() {
            pget("domain")
        } else {
            a.domain.clone()
        };
        let agent_key = downgrade_to_domain_admin_key(
            &a.gateway_url,
            &a.admin_key,
            &system_id,
            &downgrade_domain,
            &pget("admin_key"),
        );
        return json!({
            "success": true,
            "system_id": system_id,
            "path": "admin_key",
            "admin_key": agent_key,
        });
    }

    // ── Path B: product_code（新系统激活）─────────────────────────────────────
    if !a.product_code.is_empty() {
        let mut r = init_system(
            &a.gateway_url,
            &a.product_code,
            &a.system_id,
            &a.system_name,
            &a.domain,
            a.save_raw_snapshots,
            &a.manager_address,
            &webhook_host,
            &a.system_home,
        );
        if r.get("success").and_then(Value::as_bool).unwrap_or(false) {
            if let Some(o) = r.as_object_mut() {
                o.insert("path".into(), Value::String("activation".into()));
            }
        }
        return r;
    }

    err("Either admin_key or product_code is required")
}

/// `webhook_host` 三态解析（含"裸 host 不可投递则不写"的裁决）。
fn resolve_webhook_host(a: &SetupArgs) -> String {
    if !a.webhook_host.is_empty() {
        return a.webhook_host.clone();
    }
    let env = config::env_val("AIMAIL_WEBHOOK_HOST", "");
    if !env.is_empty() {
        return env;
    }
    if !a.admin_key.is_empty() {
        // reset 场景：继承已有值（探测值曾覆写 NAT 公网 webhook_host）
        if let Some(cfg) = config::load_gateway_config(&a.system_id) {
            if !cfg.webhook_host.is_empty() {
                return cfg.webhook_host;
            }
        }
    }
    let detected = crate::core::repair::detect_webhook_host(&a.gateway_url);
    let (deliverable, why) = crate::core::repair::judge_deliverable(&detected);
    if deliverable {
        return detected;
    }
    if !detected.is_empty() {
        log(
            "INFO",
            &format!(
                "detected callback address {detected:?} is not a deliverable http(s) URL ({why}) — \
                 not writing webhook_host (no bridge entry); the registration will use the local \
                 receive endpoint"
            ),
        );
    }
    String::new()
}

/// `_save_gateway_config` 之后把 prev 里未覆盖的键补回（`setup_system.py:590-605`）。
fn restore_prev_keys(system_id: &str, prev: &Map<String, Value>) {
    let path = config::gateway_config_path(system_id);
    let Ok(text) = std::fs::read_to_string(&path) else {
        return;
    };
    let Ok(Value::Object(mut cfg)) = serde_json::from_str::<Value>(&text) else {
        return;
    };
    let written: [&str; 9] = [
        "gateway_url",
        "admin_key",
        "system_id",
        "system_name",
        "save_raw_snapshots",
        "domain",
        "manager_address",
        "webhook_host",
        "system_home",
    ];
    for (k, v) in prev {
        if !cfg.contains_key(k) && !written.contains(&k.as_str()) {
            cfg.insert(k.clone(), v.clone());
        }
    }
    let text = serde_json::to_string_pretty(&Value::Object(cfg)).unwrap_or_default();
    let _ = std::fs::write(&path, text);
}

/// `_persist_system_raw_key`（幂等；已有**不同**值 ⇒ 保留旧值）。
pub fn persist_system_raw_key(system_id: &str, key: &str) {
    if key.is_empty() {
        return;
    }
    let dir = home::aimail_home().join("systems").join(system_id);
    let p = dir.join(SYSTEM_RAW_KEY_FILE);
    if let Err(e) = std::fs::create_dir_all(&dir) {
        log("WARNING", &format!("failed to persist raw system key: {e}"));
        return;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
    }
    if p.is_file() {
        if let Ok(cur) = std::fs::read_to_string(&p) {
            if !cur.trim().is_empty() {
                log(
                    "DEBUG",
                    &format!("raw system key already on disk for {system_id}"),
                );
                return;
            }
        }
    }
    match config::write_private_text(&p, &format!("{key}\n")) {
        Ok(()) => log("INFO", &format!("raw system key saved ({})", p.display())),
        Err(e) => log("WARNING", &format!("failed to persist raw system key: {e}")),
    }
}

/// `_write_admin_key`：替换既有配置里的 `admin_key`（其余字段保持，重新落盘）。
pub fn write_admin_key(cfg_path: &std::path::Path, key: &str) {
    let Ok(text) = std::fs::read_to_string(cfg_path) else {
        return;
    };
    let Ok(Value::Object(mut cfg)) = serde_json::from_str::<Value>(&text) else {
        return;
    };
    cfg.insert("admin_key".into(), Value::String(key.to_string()));
    let _ = std::fs::write(
        cfg_path,
        serde_json::to_string_pretty(&Value::Object(cfg)).unwrap_or_default(),
    );
}

/// `_downgrade_to_domain_admin_key`（`setup_system.py:76-202`）。
///
/// 成功 ⇒ 新 domain 级 key（并写回配置）；无需降级/失败 ⇒ **原系统级 key**（绝不把受限 key 当系统 key 落盘）。
pub fn downgrade_to_domain_admin_key(
    gateway_url: &str,
    system_admin_key: &str,
    system_id: &str,
    domain: &str,
    existing_key: &str,
) -> String {
    // 0) whoami 预检：已是受限级 ⇒ 无需降级（网关会以 "cannot create scopes at level 1 or above" 拒绝）
    let me = crate::core::gateway::whoami(gateway_url, system_admin_key, system_id);
    let me_nonempty = matches!(&me, Value::Object(m) if !m.is_empty());
    if me_nonempty {
        let scope = me
            .get("scope")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase();
        let cat = me
            .get("category")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase();
        let email = me
            .get("email")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if cat == "agent" || cat == "agent_admin" || scope == "agent" || scope == "agent_admin" {
            log("INFO", "key already agent-scoped — downgrade not needed");
            return system_admin_key.to_string();
        }
        if cat == "system" || (scope == "system" && email.is_empty()) {
            // whoami 证明这是系统级 key（空 email）⇒ 立刻落盘（"拿到即落盘"契约）
            persist_system_raw_key(system_id, system_admin_key);
        }
        if cat == "domain" && !email.is_empty() && email == domain {
            log(
                "INFO",
                &format!("key already domain-scoped ({email}) — downgrade not needed"),
            );
            return system_admin_key.to_string();
        }
    }

    // 0b) 配置里已存**本域** domain 级 key ⇒ 复用不轮换
    if !domain.is_empty() && !existing_key.is_empty() && existing_key != system_admin_key {
        let ex = crate::core::gateway::whoami(gateway_url, existing_key, "");
        let ex_ok = ex
            .get("category")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_lowercase()
            == "domain"
            && ex.get("email").and_then(Value::as_str).unwrap_or("") == domain;
        if ex_ok {
            log(
                "INFO",
                &format!("config already holds a domain-scoped key for {domain} — keeping it (no rotation)"),
            );
            write_admin_key(&config::gateway_config_path(system_id), existing_key);
            return existing_key.to_string();
        }
    }

    if domain.is_empty() {
        log(
            "INFO",
            &format!(
                "system {system_id} has no domain — skipping the least-privilege downgrade (the \
                 domain-category key needs the bare domain); the config keeps the system-level key"
            ),
        );
        return system_admin_key.to_string();
    }

    let scopes = vec!["system".to_string()];
    let result = crate::core::gateway::create_api_key(
        gateway_url,
        system_admin_key,
        system_id,
        domain,
        &scopes,
        "domain",
    );
    let raw = result
        .get("raw_key")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if raw.is_empty() {
        let e = result.get("error").and_then(Value::as_str).unwrap_or("");
        let d = result.get("detail").and_then(Value::as_str).unwrap_or("");
        let err_text = format!("{e} {d}").to_lowercase();
        if err_text.contains("privilege level") || err_text.contains("at or above") {
            log(
                "INFO",
                "key already agent/domain-scoped — downgrade not needed",
            );
            return system_admin_key.to_string();
        }
        log(
            "ERROR",
            &format!(
                "domain-scoped key NOT created ({e} {d}) — FALLING BACK TO SYSTEM KEY: the agent \
                 runtime keeps system-level privileges instead of the intended least-privilege \
                 scope (registration still works — a system key is unrestricted). Re-run `aimail \
                 repair` / install once the gateway accepts it."
            ),
        );
        return system_admin_key.to_string();
    }

    persist_system_raw_key(system_id, system_admin_key);
    write_admin_key(&config::gateway_config_path(system_id), &raw);
    log(
        "INFO",
        &format!("domain-scoped admin key created and saved (domain={domain})"),
    );
    raw
}

/// `init_system`（Path B，`setup_system.py:377-459`）。
#[allow(clippy::too_many_arguments)]
pub fn init_system(
    gateway_url: &str,
    product_code: &str,
    system_id: &str,
    system_name: &str,
    domain: &str,
    save_raw_snapshots: bool,
    manager_address: &str,
    webhook_host: &str,
    system_home: &str,
) -> Value {
    if product_code.is_empty() {
        return err("product_code is required");
    }
    let client = crate::core::gateway::GatewayClient::new(gateway_url, "", "", 30);
    let mut body = Map::new();
    body.insert("code".into(), Value::String(product_code.to_string()));
    if !system_name.is_empty() {
        body.insert("system_name".into(), Value::String(system_name.to_string()));
    }
    if !domain.is_empty() {
        body.insert("domain".into(), Value::String(domain.to_string()));
    }
    let result = client.post("/api/v1/activate-system", &Value::Object(body));
    let status = result.get("status").cloned().unwrap_or(json!(0));
    // 成功判定照抄：success ∈ {true,"true","ok"} / status ∈ {activated,200,201} / 有 raw_key
    let is_ok = matches!(result.get("success"), Some(Value::Bool(true)))
        || matches!(
            result.get("success").and_then(Value::as_str),
            Some("true") | Some("ok")
        )
        || matches!(
            status.as_str().map(|s| s.to_lowercase()).as_deref(),
            Some("activated") | Some("200") | Some("201")
        )
        || result
            .get("raw_key")
            .and_then(Value::as_str)
            .map(|s| !s.is_empty())
            .unwrap_or(false);
    if !is_ok {
        let e = result
            .get("error")
            .and_then(Value::as_str)
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("Activation failed (HTTP {status})"));
        return json!({ "success": false, "error": e, "status": status });
    }
    let admin_key = result
        .get("raw_key")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let created_system_id = result
        .get("system_id")
        .and_then(Value::as_str)
        .unwrap_or(system_id)
        .to_string();
    let created_domain = result
        .get("domain")
        .and_then(Value::as_str)
        .unwrap_or(domain)
        .to_string();
    if admin_key.is_empty() {
        return json!({ "success": false, "error": "No admin_key returned from server", "status": status });
    }
    persist_system_raw_key(&created_system_id, &admin_key);
    let sys_name_final = if system_name.is_empty() {
        result
            .get("system_name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    } else {
        system_name.to_string()
    };
    let cfg = config::GatewayConfig {
        gateway_url: gateway_url.to_string(),
        admin_key: admin_key.clone(),
        system_id: created_system_id.clone(),
        system_name: sys_name_final.clone(),
        save_raw_snapshots,
        domain: created_domain.clone(),
        manager_address: manager_address.to_string(),
        webhook_host: webhook_host.to_string(),
        system_home: if system_home.is_empty() {
            String::new()
        } else {
            home::abs_path(std::path::Path::new(system_home))
                .to_string_lossy()
                .to_string()
        },
        ..Default::default()
    };
    if let Err(e) = config::save_gateway_config_in(&home::aimail_home(), &created_system_id, &cfg) {
        return err(&format!("failed to write config: {e}"));
    }
    log(
        "INFO",
        &format!(
            "Gateway config saved to {}",
            config::gateway_config_path(&created_system_id).display()
        ),
    );
    let agent_key = downgrade_to_domain_admin_key(
        gateway_url,
        &admin_key,
        &created_system_id,
        &created_domain,
        "",
    );
    json!({
        "success": true,
        "system_id": created_system_id,
        "admin_key": agent_key,
        "gateway_url": gateway_url,
        "domain": created_domain,
        "system_name": sys_name_final,
    })
}

/// `__main__` 的播报形状（`setup_system.py:637-658`）：**去掉 success/path** 后的缩进 JSON。
pub fn display_json(result: &Value) -> String {
    let mut map = match result {
        Value::Object(m) => m.clone(),
        _ => return "{}".to_string(),
    };
    map.remove("success");
    map.remove("path");
    serde_json::to_string_pretty(&Value::Object(map)).unwrap_or_else(|_| "{}".to_string())
}
