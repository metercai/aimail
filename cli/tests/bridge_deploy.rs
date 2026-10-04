//! P2 切片3a 离线验收：桥二进制就位（`core::bridge_deploy`）。
//!
//! 判据（逐条对齐 `deploy_bridge._ensure_binary`/`latest_bridge_zip`/`_version_key`）：
//! 版本**语义**比较（v1.10.0 > v1.2.3 > v1.2，不是字典序）· 架构后缀筛选 · 顶层
//! `aimail-bridge*` 文件解出并改名成裸名 + 可执行位 · **幂等**（已就位则完全不动 zip）·
//! 无 zip ⇒ false + 告警（不静默）。
//!
//! 与 Python 的差异（刻意改进，语义不变）：Rust 内置 zip 解压，**不再需要宿主有 `unzip`**
//! （Python 的双通道正是为"容器无 unzip"存在的）。

use std::io::Write;
use std::path::Path;

use aimail::core::bridge_deploy::{
    announced_url, bind_address, config_lines, ensure_binary, format_webhook_host, is_executable,
    judge_deliverable, latest_bridge_zip, normalize_announced, split_host_port, version_key,
    write_bridge_config, BridgeConfigSpec, DEFAULT_BIND_PORT,
};

fn make_zip(path: &Path, entry: &str, content: &str, mode: u32) {
    let f = std::fs::File::create(path).unwrap();
    let mut zw = zip::ZipWriter::new(f);
    let opts = zip::write::SimpleFileOptions::default().unix_permissions(mode);
    zw.start_file(entry, opts).unwrap();
    zw.write_all(content.as_bytes()).unwrap();
    zw.finish().unwrap();
}

#[test]
fn version_key_arch_pick_and_idempotent_extract() {
    // 1) 版本键（`-vX.Y[.Z]`），非法名 → (0,0,0)
    assert_eq!(
        version_key("aimail-bridge-v1.2.3-linux-amd64.zip"),
        (1, 2, 3)
    );
    assert_eq!(version_key("aimail-bridge-v1.2-linux-amd64.zip"), (1, 2, 0));
    assert_eq!(version_key("whatever.zip"), (0, 0, 0));

    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().join("prog");
    let zdir = root.join("bridge");
    std::fs::create_dir_all(&zdir).unwrap();
    std::fs::create_dir_all(root.join("ahome")).unwrap();

    // 2) 取最新：语义版本优先（v1.10.0 胜过 v1.2.3），且架构后缀必须匹配
    make_zip(
        &zdir.join("aimail-bridge-v1.2.3-linux-amd64.zip"),
        "aimail-bridge-v1.2.3-amd64",
        "#!/bin/sh\necho v1.2.3\n",
        0o755,
    );
    make_zip(
        &zdir.join("aimail-bridge-v1.2-linux-amd64.zip"),
        "aimail-bridge-v1.2-amd64",
        "#!/bin/sh\necho v1.2\n",
        0o755,
    );
    make_zip(
        &zdir.join("aimail-bridge-v1.10.0-linux-amd64.zip"),
        "aimail-bridge-v1.10.0-amd64",
        "#!/bin/sh\necho v1.10.0\n",
        0o755,
    );
    make_zip(
        &zdir.join("aimail-bridge-v9.9.9-linux-arm64.zip"),
        "aimail-bridge-v9.9.9-arm64",
        "#!/bin/sh\necho arm\n",
        0o755,
    );
    let picked = latest_bridge_zip(&zdir, "amd64");
    assert!(
        picked.ends_with("aimail-bridge-v1.10.0-linux-amd64.zip"),
        "{picked}"
    );

    // 3) 就位：解出 → 裸名 + 0755
    std::env::set_var("AIMAIL_PROG_DIR", &root);
    let bin_dir = tmp.path().join("bridge/bin");
    let bin = bin_dir.join("aimail-bridge");
    assert!(ensure_binary(&bin, &bin_dir), "应从 zip 解出二进制");
    assert!(is_executable(&bin));
    let body = std::fs::read_to_string(&bin).unwrap();
    assert!(body.contains("v1.10.0"), "解出的应是选中的版本: {body}");

    // 4) 幂等：换了 zip 内容也不重解（已就位 ⇒ 完全不动）
    make_zip(
        &zdir.join("aimail-bridge-v1.10.0-linux-amd64.zip"),
        "aimail-bridge-v1.10.0-amd64",
        "#!/bin/sh\necho changed\n",
        0o755,
    );
    assert!(ensure_binary(&bin, &bin_dir));
    let body2 = std::fs::read_to_string(&bin).unwrap();
    assert_eq!(body, body2, "已就位时不得重解/覆盖");

    // 5) 无 zip ⇒ false（并告警），不静默成功
    let empty_root = tmp.path().join("prog-empty");
    std::fs::create_dir_all(empty_root.join("bridge")).unwrap();
    std::env::set_var("AIMAIL_PROG_DIR", &empty_root);
    let bin2 = tmp.path().join("bridge2/bin/aimail-bridge");
    assert!(!ensure_binary(&bin2, &tmp.path().join("bridge2/bin")));
    assert!(!bin2.exists(), "取件失败时不得留半个文件");
}

/// 合并语义 + **与 Python 逐字节等价**（期望文本取自 `deploy_bridge.write_bridge_config`
/// 在同一场景下的真实输出，见 /tmp/py-bridge-cfg.sh）。
#[test]
fn write_bridge_config_merges_and_matches_python_bytes() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    std::fs::create_dir_all(home.join("bridge")).unwrap();
    std::env::set_var("AIMAIL_HOME", &home);
    let cfg = tmp.path().join("aimail_bridge.toml");
    let log = home
        .join("bridge/aimail-bridge.log")
        .to_string_lossy()
        .to_string();

    // 预置：s2 在册 + s1 的旧条目（旧的 api_key 应被新值覆盖）
    let s2: toml::Value = "aimail_url = \"http://old2\"\nadmin_key = \"ak2\"\nsystem_id = \"s2\"\npoll_interval_sec = 2"
        .parse()
        .unwrap();
    let old1: toml::Value = "aimail_url = \"http://old1\"\nadmin_key = \"OLD1\"\nsystem_id = \"s1\"\npoll_interval_sec = 2\napi_key = \"k1\""
        .parse()
        .unwrap();
    let pre = config_lines("127.0.0.1:38081", "pull", &[s2, old1], &log, "");
    std::fs::write(&cfg, pre.join("\n") + "\n").unwrap();

    write_bridge_config(&BridgeConfigSpec {
        path: cfg.clone(),
        mode: "pull".into(),
        addr: "127.0.0.1:38081".into(),
        gateway_url: "http://gw".into(),
        admin_key: "AK".into(),
        system_id: "s1".into(),
        api_key: "K1".into(),
        webhook_secret: "WH".into(),
        hostname: "".into(),
    })
    .unwrap();

    let got = std::fs::read_to_string(&cfg).unwrap();
    let want = r#"schema_version = 1
bind = "127.0.0.1:38081"
mode = "pull"

[logging]
file = "__LOG__"
level = "info"

[pull]
# 单 bridge 多系统:每系统一条,独立 key/system_id/dedup/backoff
systems = [
  { aimail_url = "http://old2", admin_key = "ak2", system_id = "s2", poll_interval_sec = 2 },
  { aimail_url = "http://gw", admin_key = "AK", system_id = "s1", poll_interval_sec = 2, api_key = "K1", webhook_secret = "WH" }
]

[health]
check_interval_sec = 30
fail_threshold = 6
connect_timeout_sec = 3
"#
    .replace("__LOG__", &log);
    assert_eq!(got, want, "与 Python 的输出必须逐字节一致");

    use std::os::unix::fs::PermissionsExt;
    assert_eq!(
        std::fs::metadata(&cfg).unwrap().permissions().mode() & 0o777,
        0o600,
        "凭据文件必须 0600"
    );
}

/// 公告地址链（U2 tail）：接受形态 / 拒绝理由逐字 / bind 判定 / 可投递判定。
#[test]
fn announced_address_chain_matches_python_rules() {
    // 1) split_host_port：host:port / [v6]:port / [v6] / 裸 v6 / host
    assert_eq!(
        split_host_port("example.com:38081"),
        ("example.com".into(), "38081".into())
    );
    assert_eq!(
        split_host_port("[2001:db8::1]:38081"),
        ("2001:db8::1".into(), "38081".into())
    );
    assert_eq!(
        split_host_port("[2001:db8::1]"),
        ("2001:db8::1".into(), "".into())
    );
    assert_eq!(
        split_host_port("2001:db8::1"),
        ("2001:db8::1".into(), "".into())
    );
    assert_eq!(
        split_host_port("example.com"),
        ("example.com".into(), "".into())
    );

    // 2) normalize_announced：裸 host 补默认端口；IPv6 加方括号；https 带 note；URL 去 scheme
    assert_eq!(
        normalize_announced("bridge.example.com", 38081).0,
        "bridge.example.com:38081"
    );
    assert_eq!(
        normalize_announced("2001:db8::1:38081", 38081).0,
        "[2001:db8::1:38081]:38081" // 裸 v6+端口有歧义 ⇒ 整体当 host（与 Python 同）
    );
    assert_eq!(
        normalize_announced("2001:db8::1", 38081).0,
        "[2001:db8::1]:38081"
    );
    let (v, note) = normalize_announced("https://bridge.example.com", 38081);
    assert_eq!(v, "bridge.example.com:38081");
    assert!(note.contains("announced over https"), "{note}");
    assert_eq!(
        normalize_announced("http://bridge.example.com:9000", 38081).0,
        "bridge.example.com:9000"
    );

    // 3) 拒绝形态（理由逐字，绝不静默）
    assert_eq!(normalize_announced("", 38081).1, "no address announced");
    assert!(normalize_announced("http://h/x/../p", 38081)
        .1
        .contains("carries a path/query"));
    assert!(normalize_announced("h/p", 38081)
        .1
        .contains("carries a path"));
    assert!(normalize_announced("0.0.0.0:38081", 38081)
        .1
        .contains("is an unspecified/bind address"));
    assert_eq!(
        normalize_announced("h:99999", 38081).1,
        "'h:99999' carries an invalid port '99999'"
    );

    // 4) announced_url / format_webhook_host（路径走本仓 mirror 常量，不写字面量）
    assert_eq!(
        announced_url("bridge.example.com:38081"),
        format!(
            "http://bridge.example.com:38081{}",
            aimail::core::bridge_deploy::BRIDGE_ANNOUNCE_PATH
        )
    );
    assert_eq!(format_webhook_host("203.0.113.9"), "203.0.113.9:38081");
    assert_eq!(format_webhook_host("2001:db8::1"), "[2001:db8::1]:38081");

    // 5) judge_deliverable：绝对 http(s) 才可投递；裸 host:port / 通配 / 空 都拒
    let good = format!(
        "http://bridge.example.com:38081{}",
        aimail::core::bridge_deploy::BRIDGE_ANNOUNCE_PATH
    );
    assert!(judge_deliverable(&good).0);
    let (ok, why) = judge_deliverable(good.trim_start_matches("http://"));
    assert!(!ok);
    assert!(why.contains("is not an absolute http(s) URL"), "{why}");
    assert!(judge_deliverable("").1.contains("the URL is empty"));
    assert!(judge_deliverable("http://0.0.0.0:38081/x")
        .1
        .contains("bind/unspecified"));

    // 6) bind_address：IP 绑该地址；域名 → 0.0.0.0 + 端口并给出告警理由
    assert_eq!(
        bind_address("203.0.113.9:38081"),
        ("203.0.113.9:38081".into(), "".into())
    );
    let (b, w) = bind_address("2001:db8::1:38081");
    assert_eq!(b, format!("0.0.0.0:{}", DEFAULT_BIND_PORT));
    assert!(w.contains("is a domain (not an IP)"), "{w}");
    assert_eq!(bind_address("[2001:db8::1]:38081").0, "[2001:db8::1]:38081");
    let (b, w) = bind_address("bridge.example.com:38081");
    assert_eq!(b, "0.0.0.0:38081");
    assert!(w.contains("is a domain (not an IP)"), "{w}");
    assert_eq!(
        bind_address("bridge.example.com").0,
        format!("0.0.0.0:{}", DEFAULT_BIND_PORT)
    );
}
