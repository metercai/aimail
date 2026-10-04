//! `uninstall` 的离线验收（真删夹具数据；不触网、不碰真机）。
//!
//! 判据：幂等短路（数据与指针都不在 ⇒ 视为已卸载）· `-y` 确认路径真删（mail 目录 + 系统目录 +
//! 原始 key）· 未给 `-y` 且 stdin EOF ⇒ cancelled 且 rc=1（**什么都不删**）· 缺 `-s`/`-H` ⇒ 响亮失败。

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::json;

fn core_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .join("pysdk")
}

fn setup_fixture(ahome: &Path, uh: &Path, sid: &str, platform_home: &Path) {
    // 系统数据：systems/<sid>/<dir>/agentmail.json + mail/ + 原始 key
    let sys = ahome.join("systems").join(sid);
    let agent_dir = sys.join("billing.example.test");
    fs::create_dir_all(agent_dir.join("mail")).unwrap();
    fs::write(
        agent_dir.join("agentmail.json"),
        serde_json::to_string_pretty(&json!({
            "email": "billing@example.test",
            "system_id": sid,
            "domain": "example.test",
        }))
        .unwrap(),
    )
    .unwrap();
    fs::write(agent_dir.join("mail").join("1.eml"), "x").unwrap();
    fs::write(sys.join(".system_raw_key.key"), "k\n").unwrap();
    // cfg 不给（⇒ 跳过网关注销路径，保持离线）
    // 平台指针：让幂等判定认为"装着"
    let ptr_dir = uh.join(".pi");
    fs::create_dir_all(&ptr_dir).unwrap();
    fs::write(
        ptr_dir.join(aimail::core::contract::pointer_file()),
        json!({"system_id": sid}).to_string(),
    )
    .unwrap();
    let _ = platform_home;
}

#[test]
fn uninstall_offline_behaviour() {
    let tmp = tempfile::tempdir().unwrap();
    let ahome = tmp.path().join("ahome");
    let uh = tmp.path().join("uh");
    let platform_home = tmp.path().join("platform");
    fs::create_dir_all(&platform_home).unwrap();
    fs::create_dir_all(&ahome).unwrap();
    fs::create_dir_all(&uh).unwrap();
    std::env::set_var("AIMAIL_HOME", &ahome);
    std::env::set_var("HOME", &uh);
    let _ = core_dir();

    let sid = "testsid01";
    setup_fixture(&ahome, &uh, sid, &platform_home);

    // 1) 缺 --system-id/--home ⇒ 响亮失败
    let rc = aimail::cmd::uninstall::run(&aimail::cmd::uninstall::Args {
        system_id: String::new(),
        home: String::new(),
        gateway_url: String::new(),
        yes: true,
        platform: String::new(),
    });
    assert_eq!(rc, 1);

    // 2) 未给 -y 且 stdin EOF ⇒ cancelled 且 rc=1，且**什么都不删**
    let args = aimail::cmd::uninstall::Args {
        system_id: sid.to_string(),
        home: platform_home.to_string_lossy().to_string(),
        gateway_url: String::new(),
        yes: false,
        platform: "pi".to_string(),
    };
    // 测试进程的 stdin 已到 EOF ⇒ read_line 返回 0 字节 ⇒ 视作 EOF（等同 Python EOFError）
    let rc = aimail::cmd::uninstall::run(&args);
    assert_eq!(rc, 1, "未确认必须 rc=1");
    assert!(
        ahome
            .join("systems")
            .join(sid)
            .join(".system_raw_key.key")
            .is_file(),
        "未确认时不得删任何数据"
    );

    // 3) `-y` ⇒ 真删（mail 目录 + 系统目录 + key 都消失）
    let args = aimail::cmd::uninstall::Args { yes: true, ..args };
    let rc = aimail::cmd::uninstall::run(&args);
    assert_eq!(rc, 0, "确认卸载应成功");
    assert!(
        !ahome.join("systems").join(sid).exists(),
        "系统目录必须删除"
    );

    // 4) 幂等：再跑一次 ⇒ "not installed" 且 rc=0
    let rc = aimail::cmd::uninstall::run(&args);
    assert_eq!(rc, 0, "重复卸载必须幂等 rc=0");
}
