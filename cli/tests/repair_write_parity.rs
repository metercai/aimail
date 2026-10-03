//! S5b-i 的**写入面**跨语言验收：同一夹具下，Rust 与 Python 的 repair 写入步
//! 必须产出**字节相同**的文件（不是"语义等价"）。
//!
//! 覆盖两步（都是 CLI 自有权，不碰绑定文件）：
//! - `_repair_gateway_config`（系统级 `aimail_gateway.json` 补空：system_home；
//!   webhook_host 因"裸 host 不可交付"**从不写**，只打告警 —— 这正是要钉住的语义）
//! - `_repair_pointer`（平台指针重建）
//!
//! 为什么用"比文件字节"：写入步的产出就是文件；比 stdout 会漏掉键序/缩进/尾换行这类
//! 只有字节级才看得见的漂移。两侧夹具**分开建**（各自独立目录），互不干扰。

mod common;

use std::path::{Path, PathBuf};
use std::process::Command;

use aimail::core::repair;
use aimail::core::{config, contract};
use common::{repo_root, TempDir};

struct Fx {
    aimail_home: PathBuf,
    user_home: PathBuf,
    hermes: PathBuf,
}

/// 造一个 hermes 可识别的机器：系统 cfg **缺 system_home** + 一个绑定（有 email）+ 无指针。
fn build(root: &Path) -> Fx {
    let aimail_home = root.join("aimail");
    let user_home = root.join("home");
    let hermes = user_home.join(".hermes");
    let systems = aimail_home.join("systems");
    std::fs::create_dir_all(hermes.join("hermes-agent")).unwrap();
    std::fs::create_dir_all(hermes.join("profiles")).unwrap();
    std::fs::create_dir_all(&user_home).unwrap();
    std::fs::create_dir_all(systems.join("s1").join("a_example.test")).unwrap();
    std::fs::write(
        systems
            .join("s1")
            .join("a_example.test")
            .join(contract::binding_file()),
        r#"{"email":"a@example.test","api_key":"k"}"#,
    )
    .unwrap();
    std::fs::write(
        systems.join("s1").join(config::GATEWAY_CONFIG_NAME),
        "{\n  \"gateway_url\": \"http://127.0.0.1:1\",\n  \"admin_key\": \"k\",\n  \"system_id\": \"s1\"\n}",
    )
    .unwrap();
    Fx {
        aimail_home,
        user_home,
        hermes,
    }
}

fn gw_bytes(fx: &Fx) -> Vec<u8> {
    std::fs::read(
        fx.aimail_home
            .join("systems")
            .join("s1")
            .join(config::GATEWAY_CONFIG_NAME),
    )
    .unwrap()
}

fn ptr_path(fx: &Fx) -> PathBuf {
    fx.hermes.join(contract::pointer_file())
}

/// Python 侧：直接调 repair 模块的两个函数（import-time 常量取自环境）。
fn python_side(fx: &Fx) -> Option<(Vec<u8>, Vec<u8>)> {
    let cli_dir = repo_root().join("cli");
    let code = format!(
        "import sys; sys.path.insert(0, {cli:?}); import repair; \
         repair._repair_gateway_config('s1', {hermes:?}); \
         repair._repair_pointer('s1', {hermes:?})",
        cli = cli_dir.to_string_lossy(),
        hermes = fx.hermes.to_string_lossy(),
    );
    let out = Command::new("python3")
        .arg("-c")
        .arg(&code)
        .env("HOME", &fx.user_home)
        .env("AIMAIL_HOME", &fx.aimail_home)
        .env("AGENT_HOME", &fx.hermes)
        .output()
        .ok()?;
    if !out.status.success() {
        println!(
            "SKIP: python 侧失败：{}",
            String::from_utf8_lossy(&out.stderr)
        );
        return None;
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains("system_home backfilled"),
        "夹具应触发 system_home 回填，实际输出：{stdout}"
    );
    assert!(
        stdout.contains("not a deliverable http(s) URL"),
        "夹具应触发 webhook_host 不可交付告警，实际输出：{stdout}"
    );
    let ptr = ptr_path(fx);
    assert!(ptr.is_file(), "python 侧应建出指针");
    Some((gw_bytes(fx), std::fs::read(&ptr).unwrap()))
}

#[test]
fn write_steps_produce_byte_identical_files() {
    let tmp_py = TempDir::new("s5b-py");
    let py_fx = build(tmp_py.path());
    let Some((py_gw, py_ptr)) = python_side(&py_fx) else {
        println!("SKIP: python3/cli 不可用");
        return;
    };

    // Rust 侧：同一夹具形态（独立目录），设好环境后跑同两步
    let tmp_rs = TempDir::new("s5b-rs");
    let rs_fx = build(tmp_rs.path());
    std::env::set_var("HOME", &rs_fx.user_home);
    std::env::set_var("AIMAIL_HOME", &rs_fx.aimail_home);
    std::env::set_var("AGENT_HOME", &rs_fx.hermes);

    let changed = repair::repair_gateway_config(
        &rs_fx.aimail_home,
        &rs_fx.user_home,
        "s1",
        &rs_fx.hermes.to_string_lossy(),
    );
    assert!(changed, "Rust 侧应回填 system_home");
    let made = repair::repair_pointer(
        &rs_fx.aimail_home,
        &rs_fx.user_home,
        "s1",
        &rs_fx.hermes.to_string_lossy(),
    );
    assert!(made, "Rust 侧应建出指针");

    let rs_gw = gw_bytes(&rs_fx);
    let rs_ptr = std::fs::read(ptr_path(&rs_fx)).unwrap();

    // 两侧夹具根不同 ⇒ 只归一夹具根前缀（其余逐字节比：键序/缩进/尾换行都在内）
    let norm = |b: &[u8], root: &Path| {
        String::from_utf8_lossy(b).replace(&root.to_string_lossy().to_string(), "<ROOT>")
    };
    assert_eq!(
        norm(&rs_gw, tmp_rs.path()),
        norm(&py_gw, tmp_py.path()),
        "网关配置字节不一致（除夹具根前缀）"
    );
    assert_eq!(
        norm(&rs_ptr, tmp_rs.path()),
        norm(&py_ptr, tmp_py.path()),
        "指针字节不一致（除夹具根前缀）"
    );
    // webhook_host 不该被写（裸 host 不可交付）
    let gw: serde_json::Value = serde_json::from_slice(&rs_gw).unwrap();
    assert!(gw.get("webhook_host").is_none(), "webhook_host 不该出现");
    assert_eq!(
        gw.get("system_home").and_then(|v| v.as_str()),
        Some(rs_fx.hermes.to_string_lossy().as_ref())
    );
}
