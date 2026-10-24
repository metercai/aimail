//! Offline acceptance for `uninstall` (actually deletes fixture data; no network, no real machine).
//!
//! Criteria: idempotent short-circuit (data and pointer both absent ⇒ considered uninstalled) · `-y` confirmation path really deletes (mail dir + system dir +
//! original key) · no `-y` and stdin EOF ⇒ cancelled and rc=1 (**deletes nothing**) · missing `-s`/`-H` ⇒ loud failure.

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
    // System data: systems/<sid>/<dir>/<bound file> + mail/ + original key (file names follow contract constants)
    let sys = ahome.join("systems").join(sid);
    let agent_dir = sys.join("billing.example.test");
    fs::create_dir_all(agent_dir.join("mail")).unwrap();
    fs::write(
        agent_dir.join(aimail::core::contract::binding_file()),
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
    // cfg not provided (⇒ skip gateway deregistration path, keep offline)
    // Platform pointer: make idempotent check think "installed"
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

    // 1) missing --system-id/--home ⇒ loud failure
    let rc = aimail::cmd::uninstall::run(&aimail::cmd::uninstall::Args {
        system_id: String::new(),
        home: String::new(),
        gateway_url: String::new(),
        yes: true,
        platform: String::new(),
    });
    assert_eq!(rc, 1);

    // 2) no -y and stdin EOF ⇒ cancelled and rc=1, and **deletes nothing**
    let args = aimail::cmd::uninstall::Args {
        system_id: sid.to_string(),
        home: platform_home.to_string_lossy().to_string(),
        gateway_url: String::new(),
        yes: false,
        platform: "pi".to_string(),
    };
    // Test process stdin is at EOF ⇒ read_line returns 0 bytes ⇒ treated as EOF (equivalent to Python EOFError)
    let rc = aimail::cmd::uninstall::run(&args);
    assert_eq!(rc, 1, "unconfirmed must rc=1");
    assert!(
        ahome
            .join("systems")
            .join(sid)
            .join(".system_raw_key.key")
            .is_file(),
        "must not delete any data when unconfirmed"
    );

    // 3) `-y` ⇒ really deletes (mail dir + system dir + key all disappear)
    let args = aimail::cmd::uninstall::Args { yes: true, ..args };
    let rc = aimail::cmd::uninstall::run(&args);
    assert_eq!(rc, 0, "confirmed uninstall should succeed");
    assert!(
        !ahome.join("systems").join(sid).exists(),
        "system directory must be deleted"
    );

    // 4) Idempotent: run again ⇒ "not installed" and rc=0
    let rc = aimail::cmd::uninstall::run(&args);
    assert_eq!(rc, 0, "repeated uninstall must be idempotent rc=0");
}
