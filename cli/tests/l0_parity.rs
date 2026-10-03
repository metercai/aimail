//! L0 探针的**跨语言**验收：同一夹具上，Rust 侧产出的 L0 记录必须与 Python 侧
//! `cli/check_status.py` 的对应记录逐字相同（level / check / pass / detail / fix）。
//!
//! 为什么这么做：S4 的引擎内部先落、命令面最后才接（半个 check = 漏判级别 = 假绿），
//! 所以这一阶段拿不到"CLI 对 CLI"的 parity 证据；这个测试补上同一层证据 ——
//! 真跨语言比对，而不是只把 Python 的用例抄一遍。
//!
//! 环境前提：本机有 python3 且仓库里存在 `cli/check_status.py`；找不到就**明确跳过**
//! （打印原因），不伪装成通过。

mod common;

use std::path::PathBuf;

use aimail::core::check::Check;
use aimail::core::checks::l0::{agentmail_json, bridge_completeness, l0_configs, Ctx};
use aimail::core::{config, contract};
use common::{python_check_json, Record, TempDir};

/// 只比 L0 这三块产出的记录名（其余级别由后续切片落）。
const WANTED: &[(&str, &str)] = &[
    ("config", "gateway_json"),
    ("config", "complete"),
    ("config", "system_home"),
    ("config", "pointer"),
    ("agent", "config-json"),
    ("agent", "config-complete"),
    ("agent", "config-consistency"),
    ("bridge", "config-complete"),
    ("bridge", "config-mode"),
    ("bridge", "pull-entry"),
    ("bridge", "routes-entry"),
    ("bridge", "routes-target"),
];

fn build_fixture(tmp: &TempDir) -> (PathBuf, PathBuf) {
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    let sid = "s1";
    let sysdir = aimail_home.join("systems").join(sid);
    std::fs::create_dir_all(&sysdir).unwrap();
    let platform_root = user_home.join(".hermes");
    std::fs::create_dir_all(&platform_root).unwrap();

    std::fs::write(
        sysdir.join(config::GATEWAY_CONFIG_NAME),
        format!(
            r#"{{
  "gateway_url": "http://127.0.0.1:1",
  "admin_key": "k",
  "system_id": "s1",
  "system_name": "s1",
  "domain": "example.test",
  "system_home": "{}",
  "save_raw_snapshots": true
}}"#,
            platform_root.display()
        ),
    )
    .unwrap();

    // 完整的绑定文件（九字段齐）⇒ config-complete 通过
    let ok_dir = sysdir.join("a_example.test");
    std::fs::create_dir_all(&ok_dir).unwrap();
    std::fs::write(
        ok_dir.join(contract::binding_file()),
        r#"{
  "email": "a@example.test",
  "gateway_url": "http://127.0.0.1:1",
  "domain": "example.test",
  "system_id": "s1",
  "system_name": "s1",
  "manager_address": "m@example.test",
  "api_key": "k",
  "webhook_url": "http://127.0.0.1:1/hook",
  "webhook_secret": "sec",
  "agent_id": "a"
}"#,
    )
    .unwrap();

    // 缺字段的绑定文件 ⇒ config-complete 失败（钉住 detail 文案）
    let bad_dir = sysdir.join("b_example.test");
    std::fs::create_dir_all(&bad_dir).unwrap();
    std::fs::write(
        bad_dir.join(contract::binding_file()),
        r#"{"email":"b@other.test","domain":"example.test","system_id":"s1","agent_id":"b"}"#,
    )
    .unwrap();

    // 平台指针（config.pointer 命中）
    std::fs::write(
        platform_root.join(contract::pointer_file()),
        r#"{"system_id":"s1","email":"a@example.test"}"#,
    )
    .unwrap();

    // 桥：pull 模式 + 匹配的 pull 条目 + 一条指向死端口的本地路由
    let bridge = aimail_home.join("bridge");
    std::fs::create_dir_all(&bridge).unwrap();
    std::fs::write(
        bridge.join("aimail_bridge.toml"),
        "mode = \"pull\"\n\n[[pull.systems]]\nsystem_id = \"s1\"\nadmin_key = \"k\"\n",
    )
    .unwrap();
    std::fs::write(
        bridge.join("aimail_routes.toml"),
        "a@example.test = \"http://127.0.0.1:1/x\"\n",
    )
    .unwrap();

    (aimail_home, user_home)
}

#[test]
fn l0_records_match_python_check_status() {
    let tmp = TempDir::new("l0");
    let (aimail_home, user_home) = build_fixture(&tmp);

    let Some(py_checks) = python_check_json(&aimail_home, &user_home, "s1") else {
        println!("SKIP: python3 或 check_status.py 不可用 —— 未做跨语言比对");
        return;
    };
    let expected: Vec<Record> = common::filter_records(&py_checks, WANTED);
    assert!(
        expected.len() >= 10,
        "L0 记录太少（{} 条）—— 夹具或筛选可能失效，别让这条测试空过",
        expected.len()
    );

    let ctx = Ctx {
        aimail_home,
        user_home,
    };
    let mut c = Check::new();
    // 顺序照抄 Python `main()`（check_status.py:1554-1564）：L0 → [L1] → [L2 bridge]
    // → bridge 完备性 → 绑定文件。记录顺序是 `check --json` 输出的一部分，
    // 不能按"先实现哪个先调哪个"排。
    l0_configs(&mut c, "s1", &ctx);
    bridge_completeness(&mut c, "s1", &ctx);
    agentmail_json(&mut c, "s1", &ctx);
    let actual: Vec<Record> = c
        .checks
        .iter()
        .filter(|r| WANTED.iter().any(|(l, k)| *l == r.level && *k == r.check))
        .map(|r| {
            (
                r.level.clone(),
                r.check.clone(),
                r.pass,
                r.detail.clone(),
                r.fix.clone(),
            )
        })
        .collect();

    assert_eq!(
        actual.len(),
        expected.len(),
        "记录条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致");
    }
}
