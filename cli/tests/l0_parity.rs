//! L0 探针的**跨语言**验收：同一夹具上，Rust 侧产出的 L0 记录必须与 Python 侧
//! `cli/check_status.py` 的对应记录逐字相同（level / check / pass / detail / fix）。
//!
//! 为什么这么做：S4 的引擎内部先落、命令面最后才接（半个 check = 漏判级别 = 假绿），
//! 所以这一阶段拿不到"CLI 对 CLI"的 parity 证据；这个测试补上同一层证据 ——
//! 真跨语言比对，而不是只把 Python 的用例抄一遍。
//!
//! 环境前提：本机有 python3 且仓库里存在 `cli/check_status.py`；找不到就**明确跳过**
//! （打印原因），不伪装成通过。

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

use aimail::core::check::Check;
use aimail::core::checks::l0::{agentmail_json, bridge_completeness, l0_configs, Ctx};
use aimail::core::{config as gwconfig, contract};
use serde_json::Value;

static SEQ: AtomicU32 = AtomicU32::new(0);

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(tag: &str) -> Self {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "aimail-l0-parity-{}-{}-{}",
            std::process::id(),
            seq,
            tag
        ));
        std::fs::create_dir_all(&path).expect("temp dir");
        Self { path }
    }
    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// 仓库根（`cli/tests/l0_parity.rs` → 上两级）。
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("repo root")
        .to_path_buf()
}

/// 只比 L0 这三块产出的记录名（其余级别由后续切片落）。
fn is_l0_record(level: &str, check: &str) -> bool {
    matches!(
        (level, check),
        ("config", "gateway_json")
            | ("config", "complete")
            | ("config", "system_home")
            | ("config", "pointer")
            | ("agent", "config-json")
            | ("agent", "config-complete")
            | ("agent", "config-consistency")
            | ("bridge", "config-complete")
            | ("bridge", "config-mode")
            | ("bridge", "pull-entry")
            | ("bridge", "routes-entry")
            | ("bridge", "routes-target")
    )
}

fn tuple_of(rec: &Value) -> (String, String, bool, String, String) {
    (
        rec["level"].as_str().unwrap_or("").to_string(),
        rec["check"].as_str().unwrap_or("").to_string(),
        rec["pass"].as_bool().unwrap_or(false),
        rec["detail"].as_str().unwrap_or("").to_string(),
        rec["fix"].as_str().unwrap_or("").to_string(),
    )
}

fn build_fixture(tmp: &TempDir) -> (PathBuf, PathBuf) {
    let aimail_home = tmp.path().join("aimail");
    let user_home = tmp.path().join("home");
    let sid = "s1";
    let sysdir = aimail_home.join("systems").join(sid);
    std::fs::create_dir_all(&sysdir).unwrap();
    let platform_root = user_home.join(".hermes");
    std::fs::create_dir_all(&platform_root).unwrap();

    std::fs::write(
        sysdir.join(gwconfig::GATEWAY_CONFIG_NAME),
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

fn python_json(aimail_home: &Path, user_home: &Path) -> Option<Vec<Value>> {
    let script = repo_root().join("cli").join("check_status.py");
    if !script.is_file() {
        println!("SKIP: 找不到 {}（非仓库检出？）", script.display());
        return None;
    }
    let out = Command::new("python3")
        .arg(&script)
        .args(["--json", "--system-id", "s1"])
        .arg("--agent-home")
        .arg(user_home)
        .env("HOME", user_home)
        .env("AIMAIL_HOME", aimail_home)
        .output()
        .ok()?;
    if !out.status.success() {
        // check 的 rc 反映"有失败项"，夹具里本来就故意有失败项 ⇒ 不看 rc，只看输出
        println!(
            "note: python check_status rc={:?}（夹具含故意失败项，正常）",
            out.status.code()
        );
    }
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    let start = stdout.find("{\n  \"all_pass\"")?;
    let parsed: Value = serde_json::from_str(&stdout[start..]).ok()?;
    Some(parsed["checks"].as_array()?.clone())
}

#[test]
fn l0_records_match_python_check_status() {
    let tmp = TempDir::new("l0");
    let (aimail_home, user_home) = build_fixture(&tmp);

    let Some(py_checks) = python_json(&aimail_home, &user_home) else {
        println!("SKIP: python3 或 check_status.py 不可用 —— 未做跨语言比对");
        return;
    };
    let expected: Vec<_> = py_checks
        .iter()
        .filter(|r| {
            is_l0_record(
                r["level"].as_str().unwrap_or(""),
                r["check"].as_str().unwrap_or(""),
            )
        })
        .map(tuple_of)
        .collect();
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
    let actual: Vec<_> = c
        .checks
        .iter()
        .filter(|r| is_l0_record(&r.level, &r.check))
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

    // 逐条比对（顺序即产出顺序；两侧都按同一算法遍历）
    assert_eq!(
        actual.len(),
        expected.len(),
        "记录条数不同\nrust={actual:#?}\npython={expected:#?}"
    );
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(got, want, "第 {i} 条与 Python 不一致");
    }
}
