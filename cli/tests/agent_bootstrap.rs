//! `aimail agent` control-plane tests: doctor → setup → uninstall on a fake host.
//!
//! The fake host is a shell script (so `command -v` and `reload` work) plus a plugin
//! directory holding one "existing plugin" that shows the registration API. No real
//! agent platform is involved — the point is that an **unknown host** is handled from
//! its self-description, which is exactly what generalises beyond the adapted ones.

use std::path::{Path, PathBuf};
use std::process::Command;

struct TempDir(PathBuf);

impl TempDir {
    fn new(tag: &str) -> Self {
        let p = std::env::temp_dir().join(format!(
            "aimail-agent-it-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0),
            tag
        ));
        std::fs::create_dir_all(&p).unwrap();
        Self(p)
    }
    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn run(home: &Path, extra_path: &Path, args: &[&str]) -> (i32, String) {
    let path = format!(
        "{}:{}",
        extra_path.display(),
        std::env::var("PATH").unwrap_or_default()
    );
    let out = Command::new(env!("CARGO_BIN_EXE_aimail"))
        .args(args)
        .env("HOME", home)
        .env("PATH", path)
        .output()
        .expect("run aimail");
    let mut s = String::from_utf8_lossy(&out.stdout).to_string();
    s.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), s)
}

fn fake_host(root: &Path) -> (PathBuf, PathBuf, PathBuf) {
    let bin = root.join("bin");
    let plugins = root.join("plugins");
    let skills = root.join("skills");
    for d in [&bin, &plugins, &skills] {
        std::fs::create_dir_all(d).unwrap();
    }
    let exe = bin.join("acme-agent");
    std::fs::write(
        &exe,
        "#!/bin/sh\ncase \"$1\" in version) echo 'acme-agent 2.3.1';; *) : ;; esac\nexit 0\n",
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    std::fs::write(
        plugins.join("existing-hello.js"),
        "export default function register(api) {\n  api.registerTool({name:'hello'});\n  api.sendMessage = async (sid, t) => {};\n}\n",
    )
    .unwrap();
    (bin, plugins, skills)
}

fn answers(plugins: &Path, skills: &Path) -> String {
    format!(
        r#"{{"host":{{"name":"acme-agent","version":"2.3.1","runtime":"node"}},
            "plugin":{{"dir":{p:?}}},
            "reload":{{"mode":"command","cmd":"acme-agent reload"}},
            "skills":{{"dir":{s:?}}},
            "process":{{"long_running":true}},
            "sample_file":{f:?},
            "inbound":{{"mode":"call","call":"await api.sendMessage(sid, mail.subject)"}}}}"#,
        p = plugins.display().to_string(),
        s = skills.display().to_string(),
        f = plugins.join("existing-hello.js").display().to_string()
    )
}

#[test]
fn unknown_node_host_is_recognised_and_installed_then_removed() {
    let tmp = TempDir::new("node");
    let home = tmp.path().join("home");
    std::fs::create_dir_all(&home).unwrap();
    let (bin, plugins, skills) = fake_host(tmp.path());
    let answers = answers(&plugins, &skills);

    // doctor: ready, generic SDK chosen, every claim backed by evidence
    let (rc, out) = run(
        &home,
        &bin,
        &["agent", "doctor", "--json", "--answers", &answers],
    );
    assert_eq!(rc, 0, "doctor should be ready:\n{out}");
    assert!(out.contains("\"verdict\": \"ready\""), "{out}");
    assert!(out.contains("@aimail/mail-core"), "{out}");
    assert!(out.contains("registerTool"), "{out}");

    // setup: dry-run writes nothing
    let (rc, out) = run(&home, &bin, &["agent", "setup", "--answers", &answers]);
    assert_eq!(rc, 0, "{out}");
    assert!(
        !plugins.join("aimail-acme-agent").exists(),
        "dry-run wrote files"
    );

    // setup --apply: adapter + skill + state
    let (rc, out) = run(
        &home,
        &bin,
        &["agent", "setup", "--answers", &answers, "--apply"],
    );
    assert_eq!(rc, 0, "{out}");
    let adapter = plugins.join("aimail-acme-agent").join("index.mjs");
    let body = std::fs::read_to_string(&adapter).expect("adapter written");
    assert!(body.contains("startAgentPullEntries"), "{body}");
    assert!(body.contains("api.registerTool"), "{body}");
    assert!(body.contains("api.sendMessage"), "{body}");
    assert!(skills
        .join(aimail::core::contract::agent_skill_name())
        .join("SKILL.md")
        .is_file());
    assert!(home.join(".aimail/state/adapter-acme-agent.json").is_file());

    // uninstall: removes exactly what it wrote
    let (rc, out) = run(&home, &bin, &["agent", "uninstall", "--host", "acme-agent"]);
    assert_eq!(rc, 0, "{out}");
    assert!(!adapter.exists(), "adapter left behind");
    assert!(
        plugins.join("existing-hello.js").is_file(),
        "touched a foreign file"
    );
}

#[test]
fn no_inbound_hook_is_unsupported() {
    let tmp = TempDir::new("nohook");
    let home = tmp.path().join("home");
    std::fs::create_dir_all(&home).unwrap();
    let (bin, plugins, skills) = fake_host(tmp.path());
    let answers = format!(
        r#"{{"host":{{"name":"acme-agent","version":"2.3.1","runtime":"node"}},
            "plugin":{{"dir":{p:?}}},"reload":{{"mode":"none"}},
            "skills":{{"dir":{s:?}}},"process":{{"long_running":true}},
            "sample_file":{f:?},"inbound":{{"mode":"none","call":""}}}}"#,
        p = plugins.display().to_string(),
        s = skills.display().to_string(),
        f = plugins.join("existing-hello.js").display().to_string()
    );
    let (rc, out) = run(&home, &bin, &["agent", "doctor", "--answers", &answers]);
    assert_eq!(rc, 4, "no injection hook must be unsupported:\n{out}");
    assert!(out.contains("unsupported"), "{out}");
}

#[test]
fn setup_refuses_a_blocked_profile() {
    let tmp = TempDir::new("blocked");
    let home = tmp.path().join("home");
    std::fs::create_dir_all(&home).unwrap();
    let (bin, plugins, skills) = fake_host(tmp.path());
    // relative plugin dir => rejected => blocked
    let answers = format!(
        r#"{{"host":{{"name":"acme-agent","version":"2.3.1","runtime":"node"}},
            "plugin":{{"dir":"./plugins"}},"reload":{{"mode":"none"}},
            "skills":{{"dir":{s:?}}},"process":{{"long_running":true}},
            "sample_file":{f:?},"inbound":{{"mode":"call","call":"api.sendMessage(x, y)"}}}}"#,
        s = skills.display().to_string(),
        f = plugins.join("existing-hello.js").display().to_string()
    );
    let (rc, out) = run(
        &home,
        &bin,
        &["agent", "setup", "--answers", &answers, "--apply"],
    );
    assert_eq!(rc, 5, "blocked profile must not install:\n{out}");
    assert!(out.contains("\"applied\": false"), "{out}");
}

#[test]
fn package_hosts_show_their_native_command_without_touching_the_machine() {
    let tmp = TempDir::new("pkg");
    let home = tmp.path().join("home");
    std::fs::create_dir_all(&home).unwrap();

    // dry-run: the platform table holds the command, nothing is executed
    let (rc, out) = run(&home, &home, &["agent", "setup", "--host", "openclaw"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("\"mechanism\": \"package\""), "{out}");
    assert!(out.contains("openclaw-aimail"), "{out}");
    assert!(out.contains("openclaw plugins install"), "{out}");

    let (rc, out) = run(&home, &home, &["agent", "setup", "--host", "hermes"]);
    assert_eq!(rc, 0, "{out}");
    assert!(out.contains("aimailsdk"), "{out}");

    // unknown host is refused, and the message names the known ones
    let (rc, out) = run(&home, &home, &["agent", "setup", "--host", "nope"]);
    assert_eq!(rc, 2, "{out}");
    assert!(out.contains("openclaw"), "{out}");
    assert!(out.contains("hermes"), "{out}");
}
