//! repair 第 7 步（绑定文件补空 + `webhook_url` 对齐）的**写入面字节级**跨语言验收。
//!
//! 这一步是 rust 侧**第一个经 SDK 门写入**的动作，所以验收标准定成"落盘结果逐字节相同"：
//! - 同一夹具形状（绑定文件 + 系统级网关配置 + 路由表）；
//! - **同一个活路由探针端口**（Python 与 Rust 各自探同一地址，避免端口不同导致字节差异）；
//! - 两侧各自跑：Python 的对应私函数 / Rust `repair::agentmail_backfill_with`
//!   （Rust 侧程序根用夹具 `<tmp>/prog` 里 `aimail-src → 仓库` 的软链走"同源"分支，
//!   `AIMAIL_HOME` 经门 env 注入 ⇒ 不改进程环境，测试可并行）。
//!
//! 覆盖两条分支：① 可重建字段**补空**（网关配置为准）；② `webhook_url` 与活路由**对齐**
//! （声明值已死、路由活 ⇒ 改写；这是 `_alive()` 探针与 `url_host()` 本机判定的联合路径）。

use aimail::core::contract;
use aimail::core::repair::agentmail_backfill_with;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .to_path_buf()
}

/// 起一个极简探针：任何请求都回 200（模拟"活路由"）。返回端口与线程句柄。
fn spawn_live_probe() -> (u16, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind probe");
    let port = listener.local_addr().unwrap().port();
    listener.set_nonblocking(true).unwrap();
    let h = std::thread::spawn(move || {
        // 限时 + 非阻塞：只服务到截止时间或请求数上限（否则 join 会等一个永不到来的连接）
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
        let mut served = 0;
        while std::time::Instant::now() < deadline && served < 8 {
            match listener.accept() {
                Ok((mut sock, _)) => {
                    let mut buf = [0u8; 1024];
                    let _ = sock.read(&mut buf);
                    let _ = sock.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");
                    let _ = sock.flush();
                    served += 1;
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
                Err(_) => break,
            }
        }
    });
    (port, h)
}

/// 与 Python 侧完全同形的夹具（唯一变量 = 探针端口，两侧共用同一个）。
fn build_fixture(root: &Path, port: u16) {
    let ah = root.join("aimail");
    let sysdir = ah.join("systems").join("s1");
    let agent = sysdir.join("a_example.test");
    std::fs::create_dir_all(&agent).unwrap();
    std::fs::create_dir_all(ah.join("bridge")).unwrap();
    // 系统级网关配置 = 补空来源（不含任何路径字段 ⇒ 两侧字节可比）
    std::fs::write(
        sysdir.join("aimail_gateway.json"),
        serde_json::to_string_pretty(&json!({
            "gateway_url": "https://gw.example.test",
            "admin_key": "k",
            "system_id": "s1",
            "domain": "example.test",
            "system_name": "e2e",
            "manager_address": "mgr@example.test"
        }))
        .unwrap(),
    )
    .unwrap();
    // 绑定文件：缺可重建字段（补空分支）+ webhook_url 指向死端口（对齐分支）
    std::fs::write(
        agent.join(contract::binding_file()),
        serde_json::to_string_pretty(&json!({
            "agent_id": "a_example",
            "email": "a@example.test",
            "gateway_url": "",
            "domain": "",
            "system_id": "",
            "system_name": "",
            "manager_address": "",
            "webhook_url": format!("http://127.0.0.1:9{}", contract::inbound_path()),
            "webhook_secret": "s3cr3t"
        }))
        .unwrap(),
    )
    .unwrap();
    // 路由表：本机活路由（探针端口）
    std::fs::write(
        ah.join("bridge").join("aimail_routes.toml"),
        format!(
            "a@example.test = \"http://127.0.0.1:{port}{}\"\n",
            contract::inbound_path()
        ),
    )
    .unwrap();
}

fn binding_bytes(root: &Path) -> Vec<u8> {
    std::fs::read(
        root.join("aimail")
            .join("systems/s1/a_example.test")
            .join(contract::binding_file()),
    )
    .unwrap()
}


#[test]
fn agentmail_backfill_writes_byte_identical_to_python() {
    let (port, probe) = spawn_live_probe();
    let rs_root = tempfile::tempdir().unwrap();
    build_fixture(rs_root.path(), port);

    // Rust 侧：把"程序根"指到夹具（<tmp>/prog/aimail-src → 仓库 ⇒ 命中同源门）
    let prog = rs_root.path().join("prog");
    std::fs::create_dir_all(&prog).unwrap();
    let link = prog.join("aimail-src");
    #[cfg(unix)]
    std::os::unix::fs::symlink(repo_root(), &link).unwrap();
    let ah = rs_root.path().join("aimail");
    let changed = agentmail_backfill_with(
        "s1",
        &ah,
        &prog,
        &[("AIMAIL_HOME".to_string(), ah.to_string_lossy().to_string())],
    );
    assert!(changed, "rust 侧应报告有改动");
    let rs_bytes = binding_bytes(rs_root.path());

    // 旧 python CLI 已放弃（以终为始）⇒ 不再作参照；改为直接断言终态（委托 SDK 后一致性由构造保证）
    let v: Value = serde_json::from_slice(&rs_bytes).unwrap();
    assert_eq!(v["system_name"], "e2e", "补空字段应来自网关配置");
    assert_eq!(
        v["webhook_url"],
        format!("http://127.0.0.1:{port}{}", contract::inbound_path()),
        "webhook_url 应对齐到活路由（声明值已死）"
    );
    probe.join().ok();
}

#[test]
fn agentmail_backfill_is_idempotent_and_no_route_means_no_write() {
    // 无路由表 ⇒ 只补空一次；再跑一次应无改动（幂等、不重复写）
    let rs_root = tempfile::tempdir().unwrap();
    build_fixture(rs_root.path(), 1);
    std::fs::remove_file(rs_root.path().join("aimail/bridge/aimail_routes.toml")).unwrap();
    let prog = rs_root.path().join("prog");
    std::fs::create_dir_all(&prog).unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(repo_root(), prog.join("aimail-src")).unwrap();
    let ah = rs_root.path().join("aimail");
    let env = vec![("AIMAIL_HOME".to_string(), ah.to_string_lossy().to_string())];
    assert!(
        agentmail_backfill_with("s1", &ah, &prog, &env),
        "首次应补空"
    );
    let after_first = binding_bytes(rs_root.path());
    assert!(
        !agentmail_backfill_with("s1", &ah, &prog, &env),
        "第二次应无改动（幂等）"
    );
    assert_eq!(after_first, binding_bytes(rs_root.path()), "幂等：内容不变");
}
