//! Bridge lifecycle contract client ("contract first, old bridge fallback").
//!
//! Current code: `cli/_common.py:42-121` (`bridge_ctl_supported` / `bridge_status` /
//! `_read_pid_file` / `pid_alive`）。
//!
//! Contract (aimail-bridge ≥ 0.7.4): `--status [--json] [--pid-file p]`;
//! Exit codes `0` success|running · `1` error|rejected (pid not belonging to bridge) · `2` invalid config · `3` not running.
//! Unsupported old bridge falls back to "pid file + liveness probe", and **CLI side does not kill process itself**.

use serde_json::{json, Map, Value};
use std::path::Path;
use std::process::Command;

/// Whether bridge binary supports lifecycle contract (rc∈{0,3} and stdout contains `"running"`).
pub fn ctl_supported(bin: &Path) -> bool {
    let Ok(out) = Command::new(bin).args(["--status", "--json"]).output() else {
        return false;
    };
    let code = out.status.code().unwrap_or(-1);
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    (code == 0 || code == 3) && stdout.contains("\"running\"")
}

/// Bridge running state. Contract first; legacy bridge falls back to pid file + liveness probe.
pub fn status(bin: &Path, pid_file: &Path) -> Value {
    if ctl_supported(bin) {
        let out = Command::new(bin)
            .args(["--status", "--json", "--pid-file"])
            .arg(pid_file)
            .output();
        return match out {
            Ok(o) => {
                let stdout = String::from_utf8_lossy(&o.stdout).into_owned();
                let last = stdout.trim().lines().last().unwrap_or("").to_string();
                match serde_json::from_str::<Value>(&last) {
                    Ok(Value::Object(mut map)) => {
                        map.entry("running".to_string())
                            .or_insert(Value::Bool(false));
                        map.insert("_via".into(), Value::String("bridge-ctl".into()));
                        Value::Object(map)
                    }
                    Ok(other) => {
                        let mut map = Map::new();
                        map.insert("running".into(), Value::Bool(false));
                        map.insert("_via".into(), Value::String("bridge-ctl".into()));
                        map.insert("_raw".into(), other);
                        Value::Object(map)
                    }
                    Err(e) => json!({
                        "running": false,
                        "reason": format!("status-failed: {e}"),
                        "_via": "bridge-ctl",
                    }),
                }
            }
            Err(e) => json!({
                "running": false,
                "reason": format!("status-failed: {e}"),
                "_via": "bridge-ctl",
            }),
        };
    }
    let pid = read_pid_file(pid_file);
    json!({
        "running": pid.map(pid_alive).unwrap_or(false),
        "pid": pid,
        "reason": "legacy-pid-check",
        "_via": "pid-file",
    })
}

/// `_read_pid_file`: integer pid or None.
pub fn read_pid_file(pid_file: &Path) -> Option<i32> {
    std::fs::read_to_string(pid_file)
        .ok()
        .and_then(|t| t.trim().parse::<i32>().ok())
}

/// Process liveness probe (`pid_alive`: unix = signal 0; windows = tasklist).
#[cfg(unix)]
pub fn pid_alive(pid: i32) -> bool {
    // Use `kill -0` (POSIX standard) to avoid introducing a libc dependency for this
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(windows)]
pub fn pid_alive(pid: i32) -> bool {
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}")])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};

    static SEQ: AtomicU32 = AtomicU32::new(0);

    fn tmp_pid_file(tag: &str) -> PathBuf {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!(
            "aimail-bridge-test-{}-{seq}-{tag}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir.join("bridge.pid")
    }

    #[test]
    fn missing_binary_is_not_ctl_supported() {
        assert!(!ctl_supported(Path::new("/nonexistent/aimail-bridge")));
    }

    #[test]
    fn legacy_fallback_reads_pid_file_and_probes_liveness() {
        let pid_file = tmp_pid_file("legacy");
        // Non-existent binary ⇒ fallback branch; no pid file ⇒ running=false
        let v = status(Path::new("/nonexistent/aimail-bridge"), &pid_file);
        assert_eq!(v.get("running").and_then(Value::as_bool), Some(false));
        assert_eq!(v.get("_via").and_then(Value::as_str), Some("pid-file"));
        assert_eq!(
            v.get("reason").and_then(Value::as_str),
            Some("legacy-pid-check")
        );

        // Write a "definitely non-existent" pid ⇒ still false (covers the failure path of pid_alive)
        std::fs::write(&pid_file, "999999").unwrap();
        let v = status(Path::new("/nonexistent/aimail-bridge"), &pid_file);
        assert_eq!(v.get("running").and_then(Value::as_bool), Some(false));

        // Write this process's pid ⇒ true
        std::fs::write(&pid_file, std::process::id().to_string()).unwrap();
        let v = status(Path::new("/nonexistent/aimail-bridge"), &pid_file);
        assert_eq!(v.get("running").and_then(Value::as_bool), Some(true));

        std::fs::write(&pid_file, "not-a-pid").unwrap();
        assert!(read_pid_file(&pid_file).is_none());
        let _ = std::fs::remove_dir_all(pid_file.parent().unwrap());
    }
}
