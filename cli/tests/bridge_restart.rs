#![cfg(unix)]
// the whole file is unix semantics (stub executable bit / sh-driven / symlink); reopen after the windows branch (W2) lands.
//! P2 slice 3c offline acceptance: the bridge lifecycle contract + the single instance of `bridge --restart`.
//!
//! A **contract-implementing stub bridge** (`--status [--json] --pid-file` / `--stop --pid-file` /
//! `-c CFG --daemon --pid-file`, with the 0/3 exit-code semantics) replaces the real binary; assert that:
//! ① `--restart` starts the bridge and writes the pid (rc=0) ② `bridge` (status) can see that pid ③ another `--restart` => **a single instance**
//! (a new pid, the old process is dead) ④ the cleanup leaves no process.

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
# the daemon **is this stub itself** (the cmdline carries the binary path — the same shape as the real bridge,
# so that the status view's "anchored executable file" scan can see it)
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

    // ① --restart: start the bridge + write the pid
    let (rc, out, err) = run(&home, &["bridge", "--restart"]);
    assert_eq!(rc, 0, "restart rc / out={out} err={err}");
    let pid1: i32 = std::fs::read_to_string(&pid_file)
        .expect("the pid file should exist")
        .trim()
        .parse()
        .unwrap();
    assert!(pid_alive(pid1), "pid1 should be alive");

    // ② the **contract part** of the status view (config/route table) is visible; the process-scan criterion is tested separately (below)
    let (rc, out, _) = run(&home, &["bridge"]);
    assert_eq!(rc, 0);
    assert!(out.contains("Config: mode=pull"), "{out}");

    // ③ another --restart => a single instance (a new pid, the old one is dead)
    let (rc, _, err) = run(&home, &["bridge", "--restart"]);
    assert_eq!(rc, 0, "{err}");
    let pid2: i32 = std::fs::read_to_string(&pid_file)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert_ne!(pid1, pid2, "single instance: a new pid should be written");
    assert!(
        !pid_alive(pid1),
        "the old process must have been stopped (otherwise it would double-pull the same pending)"
    );
    assert!(pid_alive(pid2));

    // ④ the cleanup: leave no process
    let _ = Command::new("kill")
        .args(["-9", &pid2.to_string()])
        .status();
}

/// The **anchored criterion** of `bridge_pids`: only processes "whose first token ends in `aimail-bridge`" count
/// (the real bridge's argv[0] is the binary path); a process that merely **mentions** that string on the command line must not be counted (no mis-killing).
#[test]
fn bridge_pids_anchors_on_executable_path_and_never_over_matches() {
    let tmp = tempfile::tempdir().unwrap();
    // the real shape: argv[0] is exactly `<tmp>/aimail-bridge`. Use `exec -a` to set argv[0];
    // no more "copy the binary and then run it" — that path hits ETXTBSY under parallel tests (exec of the same inode while the write fd is not fully closed).
    let fake = tmp.path().join("aimail-bridge");
    // only bash's `exec -a` can change argv[0] (dash's exec does not support -a, and the process would exit immediately)
    let cmd = format!("exec -a '{}' sleep 30", fake.display());
    let mut real = Command::new("bash").args(["-c", &cmd]).spawn().unwrap();

    // the counter shape: the string appears on the command line, but the first token is another program (`sh -c`) => it must not hit
    let mut decoy = Command::new("sh")
        .args(["-c", "sleep 30 # /aimail-bridge --config x.toml"])
        .spawn()
        .unwrap();

    // `sh -c 'exec -a …'` has a sh→exec window: poll until argv[0] is in place (≤2s) to avoid a timing-induced false red.
    let real_pid = real.id();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    let mut pids = Vec::new();
    while std::time::Instant::now() < deadline {
        pids = aimail::core::bridge_deploy::bridge_pids_for_probe();
        if pids.contains(&real_pid) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert!(
        pids.contains(&real_pid),
        "the real-shape process must hit: {pids:?} vs {real_pid}"
    );
    assert!(
        !pids.contains(&decoy.id()),
        "a process that merely mentions the string must not hit (no mis-killing): {pids:?} vs {}",
        decoy.id()
    );

    let _ = real.kill();
    let _ = real.wait();
    let _ = decoy.kill();
    let _ = decoy.wait();
}
