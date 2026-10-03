//! `aimail check` —— 全机体检（配置 / 运行时资源 / 投递链路）。
//!
//! 编排逐条对齐 Python `check_status.py:1482-1607`（含 CLI 侧转发面
//! `cli/aimail:356-368`：只暴露 `-s/--system-id`、`-H/--home`、`-v/--verbose`；
//! `--json` 是 check_status 的内部开关，**不在命令面**）。
//!
//! 顺序即语义：system 锚点 → L0 配置 → L1 gateway → L2 bridge → bridge 完备性 →
//! 绑定文件 → L2r 运行时资源 → 载荷 → L3/L4 适配面 → 输出（表 + 结论行）→ rc。

use crate::core::check::Check;
use crate::core::checks::adapters::{self, Ctx, DEFAULT_SID_ORDER};
use crate::core::checks::l0::{self, Ctx as L0Ctx};
use crate::core::checks::{l1, l2, l2r};
use crate::core::style::{BOLD, CROSS, GREEN, NC, YELLOW};
use crate::core::{config, home, payload, platforms};
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct Args {
    pub system_id: String,
    pub home: Option<String>,
    pub verbose: bool,
}

fn env_nonempty(k: &str) -> Option<String> {
    std::env::var(k).ok().filter(|v| !v.is_empty())
}

/// `_resolve_platform_sid`：argv 显式 > 该平台指针（root / root_or_profiles）> env `SYSTEM_ID`。
///
/// 细节照抄：指针文件存在但解析失败 → 落到 env；解析成功但缺 `system_id` 键 → 返回空串
/// （**不**落 env）。
fn resolve_platform_sid(
    user_home: &Path,
    agent_type: &str,
    argv_sid: &str,
    env_sid: Option<&str>,
) -> String {
    if !argv_sid.is_empty() {
        return argv_sid.to_string();
    }
    if platforms::home_dir(agent_type).is_some() {
        let ptr = platforms::pointer_path(user_home, agent_type);
        if ptr.is_file() {
            if let Ok(text) = std::fs::read_to_string(&ptr) {
                if let Ok(v) = serde_json::from_str::<Value>(&text) {
                    return v
                        .get("system_id")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                }
            }
        }
    }
    env_sid.unwrap_or("").to_string()
}

/// `_detect_default_sid`：按 Python `PLATFORMS` 声明顺序找"已装且已集成"的首个平台指针。
fn detect_default_sid(user_home: &Path, argv_sid: &str, env_sid: Option<&str>) -> String {
    for pid in DEFAULT_SID_ORDER {
        // detect()：该平台的目录特征存在
        let root = platforms::platform_root(user_home, pid);
        if !root.exists() {
            continue;
        }
        let sid = resolve_platform_sid(user_home, pid, argv_sid, env_sid);
        if !sid.is_empty() {
            return sid;
        }
    }
    String::new()
}

/// `_system_agent_path(sid)` 的 system_home 反查（锚点反选的兜底分支）。
fn platform_by_system_home(user_home: &Path, sid: &str) -> Option<&'static str> {
    let cfg = config::load_gateway_config(sid)?;
    let sh = cfg.system_home.trim();
    if sh.is_empty() {
        return None;
    }
    let sh = std::fs::canonicalize(sh).unwrap_or_else(|_| PathBuf::from(sh));
    for name in platforms::order() {
        let hd = platforms::home_dir(name).unwrap_or("");
        if hd.is_empty() {
            continue;
        }
        let want = std::fs::canonicalize(user_home.join(hd)).unwrap_or_else(|_| user_home.join(hd));
        if sh == want {
            return Some(name);
        }
    }
    None
}

pub fn run(a: Args) -> i32 {
    let user_home = home::user_home();
    let aimail_home = home::aimail_home();

    // AGENT_HOME：`--home` 归一后的平台根 > env AGENT_HOME > `~/.hermes`（Python 同）
    let agent_home_explicit = a.home.is_some();
    let agent_home: PathBuf = match &a.home {
        Some(h) => platforms::normalize_platform_home(Path::new(h)),
        None => match env_nonempty("AGENT_HOME") {
            Some(v) => PathBuf::from(v),
            None => user_home.join(".hermes"),
        },
    };

    // 平台识别：`--agent-type` 显式 > 自动探测
    let mut agent_type = adapters::detect_agent_type(&user_home, None, agent_home_explicit);
    let env_sid = env_nonempty("SYSTEM_ID");

    // sid 归属平台反选（双平台共存时按 sid 指针归属覆盖探测结果）
    if !a.system_id.is_empty() {
        let sid_platform = {
            let hits = l0::pointer_platforms_for_sid(
                &L0Ctx {
                    aimail_home: aimail_home.clone(),
                    user_home: user_home.clone(),
                },
                &a.system_id,
            );
            if !hits.is_empty() {
                Some(hits[0].clone())
            } else {
                platform_by_system_home(&user_home, &a.system_id).map(str::to_string)
            }
        };
        if let Some(sid_platform) = sid_platform {
            if sid_platform != agent_type {
                let head: String = a.system_id.chars().take(8).collect();
                println!("  platform: {agent_type} → {sid_platform} (by system_id {head}…)");
                agent_type = sid_platform;
            }
        }
    }

    // system_id 锚点
    let mut platform_sid =
        resolve_platform_sid(&user_home, &agent_type, &a.system_id, env_sid.as_deref());
    if platform_sid.is_empty() {
        platform_sid = detect_default_sid(&user_home, &a.system_id, env_sid.as_deref());
        if !platform_sid.is_empty() {
            println!(
                "  default system_id: {platform_sid} (from {} pointer)",
                adapters::detect_agent_type(&user_home, None, agent_home_explicit)
            );
        }
    }
    if platform_sid.is_empty() {
        println!(
            "{YELLOW}⚠ No system_id resolved — 请用 --system-id 指定(或确认本机已安装 agent 平台且有 aimail 指针){NC}"
        );
    }

    let mut c = Check::new();
    c.verbose = a.verbose;
    c.add(
        "system",
        "id",
        !platform_sid.is_empty(),
        if platform_sid.is_empty() {
            "none found"
        } else {
            platform_sid.as_str()
        },
        "Run: aimail install (激活系统)",
    );

    let ctx = L0Ctx {
        aimail_home: aimail_home.clone(),
        user_home: user_home.clone(),
    };

    // L0 配置
    l0::l0_configs(&mut c, &platform_sid, &ctx);
    // L1 gateway
    l1::gateway(&mut c, &platform_sid, &ctx);
    // L2 bridge
    l2::bridge(&mut c, &platform_sid, &ctx);
    // L0 扩展：bridge 完备性 + 绑定文件完备性/一致性
    l0::bridge_completeness(&mut c, &platform_sid, &ctx);
    l0::agentmail_json(&mut c, &platform_sid, &ctx);
    // L2r 运行时资源 + 载荷
    let prog_root = home::program_root();
    l2r::runtime(&mut c, &platform_sid, &ctx, &prog_root);
    payload::check_payload(&mut c, &prog_root, resolve_core_dir(&prog_root).as_deref());

    // L3/L4 适配面
    let actx = Ctx {
        user_home: &user_home,
        agent_home: &agent_home,
        systems_dir: &aimail_home.join("systems"),
        sid: &platform_sid,
        resolve_sid: &adapters::resolve_system_id(
            &agent_home,
            Some(&a.system_id),
            env_sid.as_deref(),
        ),
    };
    if let Some(hint) = adapters::run_l3_l4(&mut c, &actx, &agent_type) {
        println!("{YELLOW}{hint}{NC}");
    }

    c.print_table();
    println!();
    if c.all_pass() {
        println!("  {GREEN}{BOLD}✓ All clear — aimail-gateway → agent-platform ready{NC}");
    } else {
        let fail = c.checks.iter().filter(|r| !r.pass).count();
        println!("  {YELLOW}{BOLD}⚠ {fail}  issue(s) — check items marked  {CROSS} {NC}");
        if !a.verbose {
            println!("    Use --verbose for fix suggestions");
        }
    }
    if c.all_pass() {
        0
    } else {
        1
    }
}

/// 运行时核心目录（Python `runtime_core.resolve_core_dir`：仓库 pysdk/ 优先）。
///
/// Rust 单二进制没有"自身所在源码目录" ⇒ 取**部署形态**的程序根源码快照副本
/// （`{program_root}/aimail-src/pysdk`，含 `aimail_base.py` 才算数）；找不到 ⇒ None
/// （等价 Python 侧 `src=""`：跳过 md5 陈旧判读）。
fn resolve_core_dir(prog_root: &Path) -> Option<PathBuf> {
    let cand = prog_root.join("aimail-src").join("pysdk");
    if cand.join("aimail_base.py").is_file() {
        Some(cand)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::testutil::TempDir;

    #[test]
    fn platform_sid_prefers_argv_then_pointer_then_env() {
        let tmp = TempDir::new("check-sid");
        let uh = tmp.path();
        assert_eq!(
            resolve_platform_sid(uh, "hermes", "argv", Some("env")),
            "argv"
        );
        assert_eq!(resolve_platform_sid(uh, "hermes", "", Some("env")), "env");
        // 平台指针（hermes root 布局）
        let hermes = uh.join(".hermes");
        std::fs::create_dir_all(&hermes).unwrap();
        std::fs::write(
            hermes.join(crate::core::contract::pointer_file()),
            r#"{"system_id":"ptr"}"#,
        )
        .unwrap();
        assert_eq!(resolve_platform_sid(uh, "hermes", "", Some("env")), "ptr");
    }

    #[test]
    fn core_dir_is_none_without_deployed_snapshot() {
        let tmp = TempDir::new("check-core");
        assert!(resolve_core_dir(tmp.path()).is_none());
        let sn = tmp.path().join("aimail-src").join("pysdk");
        std::fs::create_dir_all(&sn).unwrap();
        std::fs::write(sn.join("aimail_base.py"), "x").unwrap();
        assert_eq!(resolve_core_dir(tmp.path()), Some(sn));
    }
}
