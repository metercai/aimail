//! P2 切片3c 离线验收：桥生命周期契约 + `bridge --restart` 单实例。
//!
//! 用一个**实现契约的 stub 桥**（`--status [--json] --pid-file` / `--stop --pid-file` /
//! `-c CFG --daemon --pid-file`，退出码 0/3 语义）替换真二进制，断言：
//! ①`--restart` 起桥并写 pid（rc=0）②`bridge`（状态）能看到该 pid ③再 `--restart` ⇒ **单实例**
//! （换 pid、旧进程已死）④收尾不留进程。

use std::path::Path;
use std::process::Command;

fn rust_bin() -> String {
    format!("{}/target/debug/aimail", env!("CARGO_MANIFEST_DIR"))
}

fn run(home: &Path, args: &[&str]) -> (i32, String, String) {
    let out = Command::new(rust_bin())
        .args(args)
        .env("AIMAIL_HOME", home)
        .env("AIMAIL_PROG_DIR", home.join("prog"))
        .env("HOME", home.join("uhome"))
        .output()
        .expect("run aimail");
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string(),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

fn pid_alive(pid: i32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

const STUB: &str = r#"#!/usr/bin/env python3
import sys, os, json, subprocess, time
argv = sys.argv[1:]
def opt(name):
    return argv[argv.index(name)+1] if name in argv else ""
pidf = opt("--pid-file")
def alive(p):
    try:
        os.kill(p, 0); return True
    except OSError:
        return False
if "--status" in argv:
    pid = None
    if pidf and os.path.exists(pidf):
        try: pid = int(open(pidf).read().strip())
        except Exception: pid = None
    ok = bool(pid) and alive(pid)
    print(json.dumps({"running": ok, "pid": pid, "status": "stub"}))
    sys.exit(0 if ok else 3)
if "--stop" in argv:
    if pidf and os.path.exists(pidf):
        try:
            pid = int(open(pidf).read().strip()); os.kill(pid, 15)
            for _ in range(50):
                if not alive(pid): break
                time.sleep(0.1)
        except (ValueError, OSError): pass
        try: os.remove(pidf)
        except OSError: pass
    sys.exit(0)
if "--serve" in argv:
    time.sleep(300)
    sys.exit(0)
# 守护进程**就是本 stub 自己**（cmdline 里含二进制路径 —— 与真桥同形，
# 这样状态视图的"锚定可执行文件"扫描才看得见）
p = subprocess.Popen([sys.argv[0], "--serve", "--pid-file", pidf], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if pidf: open(pidf, "w").write(str(p.pid))
sys.exit(0)
"#;

#[test]
fn restart_starts_single_instance_and_status_sees_it() {
    let tmp = tempfile::tempdir().unwrap();
    let home = tmp.path().join("ahome");
    let bindir = home.join("bridge/bin");
    std::fs::create_dir_all(&bindir).unwrap();
    std::fs::create_dir_all(home.join("uhome")).unwrap();
    let stub = bindir.join("aimail-bridge");
    std::fs::write(&stub, STUB).unwrap();
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&stub, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    std::fs::write(
        home.join("bridge/aimail_bridge.toml"),
        "schema_version = 1\nbind = \"127.0.0.1:38081\"\nmode = \"pull\"\n",
    )
    .unwrap();
    let pid_file = home.join("bridge/bridge.pid");

    // ① --restart：起桥 + 写 pid
    let (rc, out, err) = run(&home, &["bridge", "--restart"]);
    assert_eq!(rc, 0, "restart rc / out={out} err={err}");
    let pid1: i32 = std::fs::read_to_string(&pid_file)
        .expect("pid 文件应存在")
        .trim()
        .parse()
        .unwrap();
    assert!(pid_alive(pid1), "pid1 应存活");

    // ② 状态视图的**契约部分**（配置/路由表）可见，进程扫描口径另测（见下）
    let (rc, out, _) = run(&home, &["bridge"]);
    assert_eq!(rc, 0);
    assert!(out.contains("配置: mode=pull"), "{out}");

    // ③ 再 --restart ⇒ 单实例（换 pid、旧的已死）
    let (rc, _, err) = run(&home, &["bridge", "--restart"]);
    assert_eq!(rc, 0, "{err}");
    let pid2: i32 = std::fs::read_to_string(&pid_file)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert_ne!(pid1, pid2, "单实例：应换新 pid");
    assert!(
        !pid_alive(pid1),
        "旧进程必须已被停止（否则会双拉同一 pending）"
    );
    assert!(pid_alive(pid2));

    // ④ 收尾：不留进程
    let _ = Command::new("kill")
        .args(["-9", &pid2.to_string()])
        .status();
}

/// `bridge_pids` 的**锚定口径**：只认"首 token 以 `aimail-bridge` 结尾"的进程
/// （真桥的 argv[0] 就是二进制路径），命令行里只是**提到**该串的进程不得被算进来（不误杀）。
#[test]
fn bridge_pids_anchors_on_executable_path_and_never_over_matches() {
    let tmp = tempfile::tempdir().unwrap();
    // 真形态：把 sleep 二进制拷成 `<tmp>/aimail-bridge`（argv[0] 即该路径）
    let fake = tmp.path().join("aimail-bridge");
    std::fs::copy("/bin/sleep", &fake).unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    let mut real = Command::new(&fake).arg("30").spawn().unwrap();

    // 反例形态：命令行里出现该串，但首 token 是别的程序（`sh -c`）⇒ 不许命中
    let mut decoy = Command::new("sh")
        .args(["-c", "sleep 30 # /aimail-bridge --config x.toml"])
        .spawn()
        .unwrap();

    let pids = aimail::core::bridge_deploy::bridge_pids_for_probe();
    let real_pid = real.id();
    assert!(
        pids.contains(&real_pid),
        "真形态进程必须命中: {pids:?} vs {real_pid}"
    );
    assert!(
        !pids.contains(&decoy.id()),
        "命令行只是提到该串的进程不许命中（不误杀）: {pids:?} vs {}",
        decoy.id()
    );

    let _ = real.kill();
    let _ = real.wait();
    let _ = decoy.kill();
    let _ = decoy.wait();
}
