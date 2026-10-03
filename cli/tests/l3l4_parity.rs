//! L3/L4（平台 agent 配置 + hook 接口）的跨语言验收 —— 以 **hermes** 为锚点。
//!
//! 为什么 hermes：它的 L3 记录最多（name_apikey / webhook / skill / toolset / register 五条）、
//! L4 的 hook 路径取自契约、且 root + profiles 两种布局都在同一条链上，覆盖面最大。
//! dsh / pi / openclaw 的 list_agents 与 check_config 由 `core/checks/adapters.rs`
//! 的单元测试守住（端口/路由名等平台知识同样取自注册表与契约）。
//!
//! 归一化：hook 的"Cannot reach …: <OS 错误原文>"只比错误原文之前的前缀
//! （Python 用 urllib、Rust 用 ureq，错误串天然不同；前缀不同 = 走了不同分支 ⇒ 仍会红）。

mod common;

use std::path::{Path, PathBuf};

use aimail::core::check::Check;
use aimail::core::checks::adapters::{self, Ctx};
use aimail::core::{config, contract};
use common::{filter_records, python_check_json_with, Record, TempDir};

const WANTED: &[(&str, &str)] = &[
    ("agent", "discovery"),
    ("agent", "name_apikey"),
    ("agent", "webhook"),
    ("agent", "skill"),
    ("agent", "toolset"),
    ("agent", "register"),
    ("agent", "hook"),
];

fn normalize_hook(rec: &Record) -> Record {
    let mut out = rec.clone();
    if out.1 == "hook" && out.3.starts_with("Cannot reach ") {
        out.3 = out.3.split(": ").next().unwrap_or(&out.3).to_string();
    }
    out
}

fn write_binding(systems: &Path, sid: &str, dir_name: &str, email: &str, api_key: &str) {
    let d = systems.join(sid).join(dir_name);
    std::fs::create_dir_all(&d).unwrap();
    std::fs::write(
        d.join(contract::binding_file()),
        format!(r#"{{"email":"{email}","api_key":"{api_key}","agent_id":"{dir_name}"}}"#),
    )
    .unwrap();
}

fn write_profile(root: &Path, name: &str, email: &str) {
    let pd = root.join("profiles").join(name);
    std::fs::create_dir_all(pd.join("skills").join(contract::agent_skill_name())).unwrap();
    std::fs::write(
        pd.join(contract::pointer_file()),
        format!(r#"{{"system_id":"s1","email":"{email}"}}"#),
    )
    .unwrap();
    std::fs::write(pd.join("config.yaml"), hermes_config_yaml()).unwrap();
    std::fs::write(
        pd.join("webhook_subscriptions.json"),
        r#"{"aimail-route":"http://127.0.0.1:1/x"}"#,
    )
    .unwrap();
}

fn hermes_config_yaml() -> String {
    format!(
        "platforms:\n  webhook:\n    enabled: true\n    port: 8646\n    extra:\n      secret: s3cr3t\nplatform_toolsets:\n  webhook:\n    - {}\n",
        contract::agent_toolset_name()
    )
}

fn build_fixture(tmp: &TempDir) -> (PathBuf, PathBuf, PathBuf) {
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    let hermes = user_home.join(".hermes");
    let systems = aimail_home.join("systems");
    std::fs::create_dir_all(hermes.join("hermes-agent")).unwrap();
    std::fs::create_dir_all(hermes.join("profiles")).unwrap();
    std::fs::create_dir_all(&user_home).unwrap();

    // 根 profile（default）
    std::fs::write(
        hermes.join(contract::pointer_file()),
        r#"{"system_id":"s1","email":"a@example.test"}"#,
    )
    .unwrap();
    std::fs::write(hermes.join("config.yaml"), hermes_config_yaml()).unwrap();
    std::fs::write(
        hermes.join("webhook_subscriptions.json"),
        r#"{"aimail-route":"http://127.0.0.1:1/x"}"#,
    )
    .unwrap();
    std::fs::create_dir_all(hermes.join("skills").join(contract::agent_skill_name())).unwrap();

    // 具名 profile（p1）
    write_profile(&hermes, "p1", "b@example.test");

    // 系统侧绑定（L3 的 api_key 来源）
    write_binding(&systems, "s1", "a_example.test", "a@example.test", "key-a");
    write_binding(&systems, "s1", "b_example.test", "b@example.test", "key-b");

    // 网关配置（main 的 L0 也会用；这里只要存在）
    std::fs::create_dir_all(systems.join("s1")).unwrap();
    std::fs::write(
        systems.join("s1").join(config::GATEWAY_CONFIG_NAME),
        format!(
            r#"{{"gateway_url":"http://127.0.0.1:1","admin_key":"k","system_id":"s1","system_name":"s1","domain":"example.test","system_home":"{}"}}"#,
            hermes.display()
        ),
    )
    .unwrap();

    (aimail_home, user_home, hermes)
}

#[test]
fn hermes_l3_l4_records_match_python() {
    let tmp = TempDir::new("l3l4-hermes");
    let (aimail_home, user_home, hermes) = build_fixture(&tmp);

    let Some(py_checks) = python_check_json_with(
        &aimail_home,
        &user_home,
        Some(&hermes),
        "s1",
        &[("AGENT_HOME", hermes.to_string_lossy().as_ref())],
    ) else {
        println!("SKIP: python3 不可用");
        return;
    };
    let expected: Vec<Record> = filter_records(&py_checks, WANTED)
        .iter()
        .map(normalize_hook)
        .collect();
    assert!(
        expected.len() >= 12,
        "hermes L3/L4 记录太少（{} 条）：{expected:#?}",
        expected.len()
    );

    let ctx = Ctx {
        user_home: &user_home,
        agent_home: &hermes,
        systems_dir: &aimail_home.join("systems"),
        sid: "s1",
        resolve_sid: "s1",
    };
    let mut c = Check::new();
    let hint = adapters::run_l3_l4(&mut c, &ctx, "hermes");
    assert!(hint.is_none(), "hermes 有适配器，不该出提示行：{hint:?}");
    let actual: Vec<Record> = c
        .checks
        .iter()
        .filter(|r| WANTED.iter().any(|(l, k)| *l == r.level && *k == r.check))
        .map(|r| {
            normalize_hook(&(
                r.level.clone(),
                r.check.clone(),
                r.pass,
                r.detail.clone(),
                r.fix.clone(),
            ))
        })
        .collect();

    assert_eq!(
        actual.len(),
        expected.len(),
        "条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致");
    }
    // 关键分支确认：两条 agent 的 name_apikey 都该通过（绑定里 api_key 齐）
    let apikeys: Vec<&Record> = actual.iter().filter(|r| r.1 == "name_apikey").collect();
    assert_eq!(apikeys.len(), 2, "{actual:#?}");
    assert!(apikeys.iter().all(|r| r.2), "{actual:#?}");
    // toolset / webhook 都该通过（config.yaml 齐）
    assert!(
        actual.iter().filter(|r| r.1 == "toolset").all(|r| r.2),
        "{actual:#?}"
    );
    assert!(
        actual.iter().filter(|r| r.1 == "webhook").all(|r| r.2),
        "{actual:#?}"
    );
}
