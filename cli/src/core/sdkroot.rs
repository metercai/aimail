//! **SDK root location** — the **only** place in the CLI that knows "where the SDK is and in what form" (the unification point of C).
//!
//! Stance (owner 2026-10-04): **rust CLI = a pure binary command line with zero environment dependencies of its own**; anything "that depends on the environment"
//! belongs to the SDK (pysdk → python / tssdk → node). So the CLI does only two things:
//! ① land the **logical entry names** (registry SDK-root-relative paths like `hermes/register_profiles.py`) into actual invocations;
//! ② start an SDK execution process. **Layout knowledge must not be scattered elsewhere** — every other module asks this module.
//!
//! **The only form** (finalized by owner 2026-10-06): `pip` = the directory of the installed `aimail` package
//! (`python -c "import aimail;print(dirname(aimail.__file__))`) — there is **no** `aimail-src/`
//! repo snapshot state, and no implicit or explicit "repo state first" branch ✗; resources always come from the **installed package** ✓.

use std::path::{Path, PathBuf};

/// P8 (2026-10-17, owner ruling): the interpreter is a **target-agent variable auto-detected by the CLI**, not an external input
/// => the process-environment `AIMAIL_PYTHON` write (`set_var`) was removed; the binding now lives in **process-local state**
/// (`bind_domain` is the only write point; the "single source" semantics are unchanged). Child processes (door/steps/registrar/checks)
/// are injected explicitly via `bound_python_env` (setting env for a child process is not pollution, owner ruling 2026-10-16),
/// no longer relying on process-environment inheritance.
static BOUND_PYTHON: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

/// Read the binding (None = not bound).
fn bound_python() -> Option<String> {
    BOUND_PYTHON.lock().ok().and_then(|g| g.clone())
}

/// The explicit child-process env entry for the bound interpreter (not bound => None).
pub(crate) fn bound_python_env() -> Option<(String, String)> {
    bound_python()
        .filter(|v| !v.trim().is_empty())
        .map(|v| ("AIMAIL_PYTHON".to_string(), v))
}

/// A located SDK root. `kind` ∈ `repo` | `pip` (same vocabulary as the Python side's `resolve_source_root`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SdkRoot {
    pub path: PathBuf,
    pub kind: &'static str,
}

/// Judge whether a directory is an "SDK root" (marker: `aimail_base.py`, placed directly under the root in both forms).
pub fn is_sdk_root(dir: &Path) -> bool {
    dir.join("aimail_base.py").is_file()
}

/// An explicitly given root (`--source-root` / specified by the caller): it must really contain `aimail_base.py`, or error.
pub fn at(explicit: &str) -> Result<SdkRoot, String> {
    let root = crate::core::home::abs_path(&crate::core::home::expand_user(explicit));
    if !is_sdk_root(&root) {
        return Err(format!(
            "ERROR: --source-root invalid (no aimail_base.py): {}",
            root.to_string_lossy()
        ));
    }
    Ok(SdkRoot {
        path: root,
        // owner 2026-10-06: there is only one form (the pip-installed package) => `kind` is always "pip"
        kind: "pip",
    })
}

/// Probe the interpreter used by the agent running on this machine (requires aimail to actually be in that venv => no mis-picking unrelated venvs)
pub fn probe_agent_python() -> Option<String> {
    #[cfg(windows)]
    {
        // Windows mirror: enumerate the python family from the process table + the aimail-inside-venv criterion (single source = procwin)
        return crate::core::procwin::probe_agent_python();
    }
    #[cfg(unix)]
    {
        let rd = std::fs::read_dir("/proc").ok()?;
        for e in rd.flatten() {
            let Ok(s) = std::fs::read(e.path().join("cmdline")) else {
                continue;
            };
            for tok in s.split(|b| *b == 0) {
                let t = String::from_utf8_lossy(tok).to_string();
                if !t.contains("/bin/python") {
                    continue;
                }
                let pb = std::path::Path::new(&t);
                if !pb.is_file() {
                    continue;
                }
                if let Some(venv) = pb.parent().and_then(|b| b.parent()) {
                    if let Ok(ls) = std::fs::read_dir(venv.join("lib")) {
                        if ls
                            .flatten()
                            .any(|d| d.path().join("site-packages/aimail").is_dir())
                        {
                            return Some(t);
                        }
                    }
                }
            }
        }
        None
    }
}

const PROBE_SH: &str = r#"for c in /proc/[0-9]*/cmdline; do tr "\0" "\n" <"$c" 2>/dev/null; done | grep -E "/bin/python" | sort -u | while read -r x; do "$x" -c "import importlib.util as u,sys;s=u.find_spec('aimail');sys.exit(0 if (s and s.origin) else 1)" 2>/dev/null && { echo "$x"; exit 0; }; done;"#;

/// In-domain probe: container => docker exec; host => try each candidate for "can import aimail"
pub fn probe_in_domain(container: &str, home: &str) -> String {
    // single-source entry: external override (read-only) => process-local binding (consistent with the `python_bin()` chain)
    if let Ok(p) = std::env::var("AIMAIL_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Some(p) = bound_python() {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if !container.is_empty() {
        if let Ok(o) = std::process::Command::new("docker")
            .args(["exec", container, "sh", "-c", PROBE_SH])
            .output()
        {
            let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if !v.is_empty() {
                return v;
            }
        }
    }
    for rel in [
        "bin/python3",
        "bin/python",
        "venv/bin/python3",
        "venv/bin/python",
        ".venv/bin/python3",
        ".venv/bin/python",
    ] {
        let c = std::path::Path::new(home).join(rel);
        if c.is_file() {
            return c.to_string_lossy().to_string();
        }
    }
    // uv-managed dependency environment (hermes PM layout, proven by a full local reproduction of the L3 hermes cell on 2026-10-15):
    // the host launcher = `tools/python -I` (isolated mode: **its own site-packages is not on sys.path**)
    // + `sys.path = <tree> + {home}/installs/<key>/environments/<key>/venv/site-packages`。
    // the only record of "which venv the running process imports from" = that install's `facts.json`
    // (`packages.venv.environment`) — written last by the hermes PM (completion marker) and
    // `pm/environments.py::_recorded_venv()` resolves by it => read the record, do not guess the layout.
    // mirroring the `_recorded_venv` validation: the env must live under this install's `environments/` + `pyvenv.cfg`
    // must be present (hermes's own sanity check) + the interpreter must be present. With multiple installs, take the newest facts.json
    // (the last writer = the currently selected environment). This must be ranked **before** nested venvs / the tools toolchain:
    // the historical assertion of the tools toolchain entry — "this layout does not build a venv" — is outdated for the new hermes (the measured host process
    // imports the uv-managed venv); picking it = installing into tools-python's site-packages = the host
    // process's import of aimail is guaranteed to fail (the root cause of welcome not closing the loop).
    if let Ok(rd) = std::fs::read_dir(Path::new(home).join("installs")) {
        let mut cands: Vec<(std::time::SystemTime, String)> = Vec::new();
        for e in rd.flatten() {
            let key = e.path();
            let facts = key.join("facts.json");
            let Ok(text) = std::fs::read_to_string(&facts) else {
                continue;
            };
            let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
                continue;
            };
            let Some(env) = v
                .get("packages")
                .and_then(|p| p.get("venv"))
                .and_then(|e2| e2.get("environment"))
                .and_then(|s| s.as_str())
            else {
                continue;
            };
            let envp = Path::new(env);
            // the env in facts.json is an **absolute** path; when home is a relative path the key is relative too =>
            // canonicalize both sides before comparing; on failure fall back to a literal comparison.
            let envd = key.join("environments");
            let under = (|| -> Option<bool> {
                let a = std::fs::canonicalize(envp).ok()?;
                let b = std::fs::canonicalize(&envd).ok()?;
                Some(a.starts_with(b))
            })()
            .unwrap_or_else(|| envp.starts_with(&envd));
            if !under || !envp.join("pyvenv.cfg").is_file() {
                continue;
            }
            for bin in [
                "bin/python3",
                "bin/python",
                // Windows hermes PM venv = `Scripts/` layout (2026-10-17 L3 windows/hermes
                // proven: "Activate with: venv\Scripts\activate" + "Using Python ... at: venv").
                // on unix that path does not exist => naturally skipped, no cross-platform side effects.
                "Scripts/python.exe",
                "Scripts/python",
            ] {
                let c = envp.join(bin);
                if c.is_file() {
                    let mt = std::fs::metadata(&facts)
                        .and_then(|m| m.modified())
                        .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
                    cands.push((mt, c.to_string_lossy().to_string()));
                    break;
                }
            }
        }
        if !cands.is_empty() {
            cands.sort();
            return cands.pop().unwrap().1;
        }
    }
    // agent-side nested venv discovery (layout-driven, zero platform presets): a venv under a home subdirectory
    // (the official hermes install.sh builds the venv at `{home}/hermes-agent/venv`, and the official mirror is
    // `.venv`; the gateway process's sys.path points at that venv's site-packages, and the SDK must be installed there).
    if let Ok(rd) = std::fs::read_dir(home) {
        let mut subs: Vec<std::path::PathBuf> = rd.flatten().map(|e| e.path()).collect();
        subs.sort();
        for sub in subs {
            if !sub.is_dir() {
                continue;
            }
            for rel in [
                "venv/bin/python3",
                "venv/bin/python",
                ".venv/bin/python3",
                ".venv/bin/python",
            ] {
                let c = sub.join(rel);
                if c.is_file() {
                    return c.to_string_lossy().to_string();
                }
            }
        }
    }
    // official host toolchain fallback: `{home}/tools/python-<ver>-*/bin/python3` (for older hermes without the installs/ store:
    // the host process runs that interpreter directly; see the interpreter matrix of L3 run 38139097257).
    // Note: the new hermes (PM/uv-managed layout) host process imports the uv venv under installs =>
    // the facts.json entry above takes precedence; this entry only covers the old layout without an installs store, and no longer claims "no venv is built".
    // criterion = **can actually run** `-c "import sys"`; with multiple candidates take the last in sort order (newest version): a leftover broken toolchain on a dev machine
    // directory must not be picked (the same rule as tests/agent-env/verify.sh — that file has long included this candidate; the CLI missing it is what was inconsistent).
    let tools = std::path::Path::new(home).join("tools");
    if let Ok(rd) = std::fs::read_dir(&tools) {
        let mut subs: Vec<std::path::PathBuf> = rd.flatten().map(|e| e.path()).collect();
        subs.sort();
        for sub in subs.iter().rev() {
            let c = sub.join("bin/python3");
            if !c.is_file() {
                continue;
            }
            if let Ok(o) = std::process::Command::new(&c)
                .args(["-c", "import sys"])
                .output()
            {
                if o.status.success() {
                    return c.to_string_lossy().to_string();
                }
            }
        }
    }
    #[cfg(not(windows))]
    if let Ok(o) = std::process::Command::new("sh")
        .env("AIMAIL_HOME", home)
        .current_dir(if home.is_empty() { "/" } else { home })
        .args(["-c", PROBE_SH])
        .output()
    {
        let v = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if !v.is_empty() {
            return v;
        }
    }
    probe_agent_python().unwrap_or_else(|| "python3".to_string())
}

/// The interpreter **read point** (used when starting an SDK execution process): `$AIMAIL_PYTHON` => live-process probe => PATH `python3`.
/// **There is exactly one write point** = [`bind_domain`] (contract §4.1(2) single source) — command entries must bind first,
/// otherwise this degenerates to the CLI's own `python3` (= "mismatch with the target system", measured on deerflow).
pub fn python_bin() -> String {
    // the **location chain** of contract v1.0 §4.1(2) (single source, consumed in three places: this module's resolution, `sdkcall` by-name invocation, the `sdk` door):
    // `$AIMAIL_PYTHON` (read-only override) => **process-local binding** (set by bind_domain) => host venv probe
    // (the agent interpreter that is running and can `import aimail`) => PATH `python3`.
    if let Ok(p) = std::env::var("AIMAIL_PYTHON") {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Some(p) = bound_python() {
        if !p.trim().is_empty() {
            return p;
        }
    }
    if let Some(p) = probe_agent_python() {
        return p;
    }
    "python3".to_string()
}

/// **Domain binding** (the only write point, contract §4.1(2) single source): resolve the host interpreter inside the target system's domain and store it in
/// **process-local state** (P8 (2026-10-17): the process-environment `AIMAIL_PYTHON` write was removed; the door/steps/registrar/checks
/// child processes are injected explicitly via `bound_python_env`) => one binding is valid for the whole run.
///
/// In the container scenario (`docker exec`) what is returned is a **path inside the container** => return it only, do not bind it (using it as a local interpreter would be wrong).
/// When `$AIMAIL_PYTHON` is already in the environment, `probe_in_domain` returns it directly => an explicit override wins (chain head, semantics unchanged).
pub fn bind_domain(container: &str, home: &str) -> String {
    let py = probe_in_domain(container, home);
    if container.is_empty() && !py.trim().is_empty() {
        if let Ok(mut g) = BOUND_PYTHON.lock() {
            *g = Some(py.clone());
        }
    }
    py
}

/// Same as above, with the domain information taken from the system-level cfg (`system_home` + `container`) — the unified entry for command entries without `-H/--container`
/// (`address` / `prompt` / `repair`, etc.: they only have `--system-id`).
pub fn bind_domain_for_system(sid: &str) -> String {
    let cfg = crate::core::config::load_gateway_config(sid)
        .map(|c| serde_json::Value::Object(c.to_json()))
        .unwrap_or_else(|| serde_json::json!({}));
    let g = |k: &str| -> String {
        cfg.get(k)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    bind_domain(&g("container"), &g("system_home"))
}

/// SDK root location: **the only form is the pip-installed package** (owner 2026-10-06: resolute and thorough — there is no `aimail-src/`,
/// no "repo state" ✗; resources always come from the **installed package**). Neither the implicit nor the explicit path is an exception.
pub fn resolve() -> Result<SdkRoot, String> {
    resolve_with(&python_bin())
}

pub fn resolve_with(python: &str) -> Result<SdkRoot, String> {
    let out = std::process::Command::new(python)
        .args([
            "-c",
            // locate only, do not execute (owner 2026-10-12): locating the SDK root **must not rely on running the aimail package**.
            // `find_spec` does not import the module body; an empty `origin` = a namespace package => treated as not installed.
            "import importlib.util as u,os;s=u.find_spec('aimail');\
             print(os.path.dirname(s.origin) if (s and s.origin) else '')",
        ])
        .output();
    if let Ok(o) = out {
        if o.status.success() {
            let dir = String::from_utf8_lossy(&o.stdout).trim().to_string();
            let p = PathBuf::from(&dir);
            if !dir.is_empty() && is_sdk_root(&p) {
                return Ok(SdkRoot {
                    path: p,
                    kind: "pip",
                });
            }
        }
    }
    Err(format!(
        "ERROR: no installed agent-aimail found (no aimail package located under interpreter {python})"
    ))
}

/// The registry's "SDK-root-relative entry" -> absolute path. The **only** place that lands a logical name into a path.
pub fn entry(root: &SdkRoot, rel: &str) -> PathBuf {
    root.path.join(rel)
}

/// Same as above, in `&Path` form (reused by callers that already have the root path, avoiding re-assembly everywhere).
pub fn entry_path(root: &Path, rel: &str) -> PathBuf {
    root.join(rel)
}

/// Convenience: the **reportable** form on resolution failure (only for logs / `{sdk}` context; **do not** use it to decide "whether it can run").
/// owner 2026-10-06: no longer degenerate to any `aimail-src/` snapshot path ✗ (that directory does not exist and should not exist).
pub fn resolve_or_placeholder() -> SdkRoot {
    match resolve() {
        Ok(r) => r,
        Err(e) => {
            // owner 2026-10-06: there is **no** repo state / self-contained payload => unresolvable = "SDK not installed" => it must **fail loudly** ✗
            eprintln!("✗ SDK not installed or unavailable: {e}");
            eprintln!("  Install the latest released version first: pip install --index-url https://pypi.org/simple/ agent-aimail");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // `AIMAIL_PYTHON` / the process-local binding are process-level global state: cross-file cases (l0/repair read python_bin)
    // stepping on each other if they each mutate their own copy (the P8 class) => serialize on the shared testutil process-level lock.
    use crate::core::testutil::ENV_LOCK;

    // P8 (2026-10-17): the process-local binding (BOUND_PYTHON) is also process-level global state — RAII save/clear/restore,
    // the same pattern as ENV_LOCK + env save/restore.
    struct BoundGuard(Option<String>);
    impl BoundGuard {
        fn new() -> Self {
            let prev = BOUND_PYTHON.lock().unwrap().clone();
            *BOUND_PYTHON.lock().unwrap() = None;
            Self(prev)
        }
    }
    impl Drop for BoundGuard {
        fn drop(&mut self) {
            *BOUND_PYTHON.lock().unwrap() = self.0.clone();
        }
    }

    #[test]
    fn repo_layout_entry_resolution() {
        // repo layout (this repo): SDK root = <prog>/aimail-src/pysdk => the two SDK-root-relative entries land directly
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("repo root")
            .join("pysdk");
        assert!(is_sdk_root(&repo), "the repo pysdk should contain aimail_base.py");
        for rel in [
            "hermes/register_profiles.py",
            "deer-flow/manage.py",
            "install.py",
        ] {
            let p = entry(
                &SdkRoot {
                    path: repo.clone(),
                    kind: "repo",
                },
                rel,
            );
            assert!(
                p.is_file(),
                "an SDK-root-relative entry should land directly as a file: {}",
                p.display()
            );
            // key: the entry carries **no** repo prefix => isomorphic with the pip form (aimail/<rel>)
            assert!(
                !rel.starts_with("pysdk/"),
                "registry entries must not carry a repo prefix: {rel}"
            );
        }
    }

    #[test]
    fn explicit_root_must_be_a_sdk_root() {
        let tmp = tempfile::tempdir().unwrap();
        let err = at(&tmp.path().to_string_lossy()).unwrap_err();
        assert!(
            err.contains("--source-root invalid (no aimail_base.py)"),
            "{err}"
        );
    }

    #[test]
    fn host_venv_without_python3_is_found_before_sdk_is_installed() {
        // contract §4.1(2): the host interpreter = **the environment the host itself configured**. Before the first install the SDK is certainly not yet installed =>
        // the criterion must be the **layout**, not "can import aimail" (deerflow = `<home>/backend/.venv/bin/python`,
        // and that venv is not guaranteed to have `python3`).
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let bin = home.join("backend").join(".venv").join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let py = bin.join("python"); // only `python`
        std::fs::write(&py, b"#!/bin/sh\n").unwrap();

        let _g = ENV_LOCK.lock().unwrap();
        let _bg = BoundGuard::new();
        let saved = std::env::var("AIMAIL_PYTHON").ok();
        std::env::remove_var("AIMAIL_PYTHON");
        let got = probe_in_domain("", &home.to_string_lossy());
        if let Some(v) = saved {
            std::env::set_var("AIMAIL_PYTHON", v);
        }
        assert_eq!(
            got,
            py.to_string_lossy().to_string(),
            "should hit the host venv's bin/python (without relying on the SDK being installed)"
        );
    }

    #[test]
    fn bind_domain_exports_the_host_interpreter_for_the_door() {
        // after binding, every consumer (the python door / step child processes / the registration chain) reads the **host** interpreter.
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let bin = home.join("backend").join(".venv").join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let py = bin.join("python3");
        std::fs::write(&py, b"#!/bin/sh\n").unwrap();

        let _g = ENV_LOCK.lock().unwrap();
        let _bg = BoundGuard::new();
        let saved = std::env::var("AIMAIL_PYTHON").ok();
        std::env::remove_var("AIMAIL_PYTHON");
        let got = bind_domain("", &home.to_string_lossy());
        let seen = python_bin();
        match saved {
            Some(v) => std::env::set_var("AIMAIL_PYTHON", v),
            None => std::env::remove_var("AIMAIL_PYTHON"),
        }
        assert_eq!(
            got,
            py.to_string_lossy().to_string(),
            "bind_domain should return the host interpreter"
        );
        assert_eq!(seen, got, "after binding python_bin() must read the same host interpreter");
    }

    #[test]
    fn nested_agent_venv_discovered_without_platform_preset() {
        // C-4 (2026-10-10): the agent-side nested venv is **discovered** in the domain (layout-driven, zero platform presets).
        // the official hermes install.sh builds the venv at {home}/hermes-agent/venv — the direct candidates
        // ({home}/venv, {home}/bin, {home}/.venv) all miss => nested discovery hits that venv.
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let nested = home.join("hermes-agent").join("venv").join("bin");
        std::fs::create_dir_all(&nested).unwrap();
        let py = nested.join("python3");
        std::fs::write(&py, b"#!/bin/sh\n").unwrap();

        let _g = ENV_LOCK.lock().unwrap();
        let _bg = BoundGuard::new();
        // clear AIMAIL_PYTHON (the test process may inherit the dev machine's env and short-circuit into the override branch)
        let saved = std::env::var("AIMAIL_PYTHON").ok();
        std::env::remove_var("AIMAIL_PYTHON");
        let got = probe_in_domain("", &home.to_string_lossy());
        if let Some(v) = saved {
            std::env::set_var("AIMAIL_PYTHON", v);
        }
        assert_eq!(
            got,
            py.to_string_lossy().to_string(),
            "the nested venv under {home:?} should be discovered, got {got}"
        );
    }

    // Build a uv-managed install store (hermes PM layout) and return (facts.json path, venv python path).
    // the environment in facts uses an **absolute** path (matching hermes's real on-disk write).
    fn mk_pm_install(home: &Path, key: &str) -> (PathBuf, PathBuf) {
        let keydir = home.join("installs").join(key);
        let env = keydir.join("environments").join(key).join("venv");
        let bin = env.join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let py = bin.join("python3");
        std::fs::write(&py, b"#!/bin/sh\n").unwrap();
        std::fs::write(env.join("pyvenv.cfg"), b"home = /x\n").unwrap();
        let facts = keydir.join("facts.json");
        std::fs::write(
            &facts,
            format!(
                r#"{{"packages":{{"venv":{{"environment":"{}"}}}}, "schema": 1}}"#,
                env.to_string_lossy()
            ),
        )
        .unwrap();
        (facts, py)
    }

    fn probe_with_lock(home: &str) -> String {
        let _g = ENV_LOCK.lock().unwrap();
        let _bg = BoundGuard::new();
        let saved = std::env::var("AIMAIL_PYTHON").ok();
        std::env::remove_var("AIMAIL_PYTHON");
        let got = probe_in_domain("", home);
        match saved {
            Some(v) => std::env::set_var("AIMAIL_PYTHON", v),
            None => std::env::remove_var("AIMAIL_PYTHON"),
        }
        got
    }

    // the criterion depends on the unix executable bit (the tools toolchain "can actually run" = 0755) => unix only
    #[cfg(unix)]
    #[test]
    fn uv_managed_env_venv_preferred_over_tools_toolchain() {
        // L3 2026-10-15 (full local reproduction): the official hermes host launcher = tools/python `-I`
        // (isolated mode, its own site-packages not on sys.path) + the site-packages of the uv-managed venv =>
        // the SDK must be installed into `installs/<key>/environments/<key>/venv`; facts.json is the only authoritative record.
        // This case locks in: the facts.json entry takes **precedence over** the tools toolchain entry (picking the latter = installing for nothing).
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let (_, uvpy) = mk_pm_install(home, "8a4017c4cabfe15f");
        // Also place a runnable tools toolchain (the old-layout candidate) to prove the new entry wins.
        let td = home
            .join("tools")
            .join("python-3.14.7+202****0101-linux-x64")
            .join("bin");
        std::fs::create_dir_all(&td).unwrap();
        let toolpy = td.join("python3");
        std::fs::write(&toolpy, "#!/bin/sh\nexit 0\n").unwrap();
        std::fs::set_permissions(&toolpy, std::fs::Permissions::from_mode(0o755)).unwrap();
        let got = probe_with_lock(&home.to_string_lossy());
        assert_eq!(
            got,
            uvpy.to_string_lossy().to_string(),
            "the uv venv recorded by facts.json should take precedence over the tools toolchain, got {got}"
        );
    }

    #[test]
    fn uv_managed_env_fresh_facts_wins_over_stale() {
        // multiple install stores: take the **newest** facts.json (the hermes PM writes facts last = completion marker =>
        // the newest = the currently selected environment). An old install's venv must not be picked.
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let (_old_facts, old_py) = mk_pm_install(home, "aaaa000000000000");
        let (new_facts, new_py) = mk_pm_install(home, "bbbb111111111111");
        // make new strictly newer (second precision suffices: when rewinding old via touch is not feasible, rely on creation order + an explicit sleep 1).
        std::thread::sleep(std::time::Duration::from_secs(1));
        std::fs::write(&new_facts, std::fs::read_to_string(&new_facts).unwrap()).unwrap();
        let got = probe_with_lock(&home.to_string_lossy());
        assert_eq!(
            got,
            new_py.to_string_lossy().to_string(),
            "the install with the newest facts.json should be taken, got {got} (old={})",
            old_py.to_string_lossy()
        );
    }

    #[test]
    fn windows_hermes_pm_venv_scripts_layout_is_discovered() {
        // L3 2026-10-17 windows/hermes (proven run 39001152324): on Windows the hermes PM's
        // uv venv is a `Scripts/` layout ("Activate with: venv\Scripts\activate" +
        // "Using Python 3.14.7 environment at: venv"). the facts.json entry only recognizes `bin/` =>
        // everything misses and it falls back to bare PATH python3 => the SDK is installed into an interpreter the host process cannot import from =>
        // the preprocessor is silently unregistered (`except ImportError: pass`) => welcome does not close the loop within 120s.
        // criterion = layout (facts.json + pyvenv.cfg + interpreter present), no platform executable-bit dependency => runnable on both platforms.
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let keydir = home.join("installs").join("3a8b74792452ca04");
        let env = keydir
            .join("environments")
            .join("3a8b74792452ca04")
            .join("venv");
        let scripts = env.join("Scripts");
        std::fs::create_dir_all(&scripts).unwrap();
        let py = scripts.join("python.exe");
        std::fs::write(&py, b"MZ").unwrap();
        std::fs::write(env.join("pyvenv.cfg"), b"home = /x\n").unwrap();
        std::fs::write(
            keydir.join("facts.json"),
            format!(
                r#"{{"packages":{{"venv":{{"environment":"{}"}}}}, "schema": 1}}"#,
                env.to_string_lossy()
            ),
        )
        .unwrap();
        let got = probe_with_lock(&home.to_string_lossy());
        assert_eq!(
            got,
            py.to_string_lossy().to_string(),
            "the uv venv recorded by facts.json should hit via the Scripts layout, got {got}"
        );
    }

    // same as above: the tools toolchain landing criterion depends on the unix executable bit => unix only
    #[cfg(unix)]
    #[test]
    fn uv_managed_env_rejects_env_outside_environments_or_without_pyvenv() {
        // mirroring the hermes `_recorded_venv` validation: the env is not under this install's environments/ => reject;
        // pyvenv.cfg missing => reject; all rejected => fall back to the tools toolchain entry.
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        // bad facts: the env points outside the install (a forged/corrupt record must not be picked)
        let keydir = home.join("installs").join("cccc222222222222");
        std::fs::create_dir_all(&keydir).unwrap();
        std::fs::write(
            keydir.join("facts.json"),
            r#"{"packages":{"venv":{"environment":"/etc/foreign/venv"}},"schema":1}"#,
        )
        .unwrap();
        // bad venv: under environments/ but missing pyvenv.cfg
        let env2 = home
            .join("installs")
            .join("dddd333333333333")
            .join("environments")
            .join("dddd333333333333")
            .join("venv")
            .join("bin");
        std::fs::create_dir_all(&env2).unwrap();
        std::fs::write(env2.join("python3"), b"#!/bin/sh\n").unwrap(); // no pyvenv.cfg
        std::fs::write(
            home.join("installs")
                .join("dddd333333333333")
                .join("facts.json"),
            format!(
                r#"{{"packages":{{"venv":{{"environment":"{}"}}}}}}"#,
                env2.parent().unwrap().to_string_lossy()
            ),
        )
        .unwrap();
        // the landing point = the tools toolchain entry (the new entry must miss as a whole)
        let td = home
            .join("tools")
            .join("python-3.13.0-linux-x64")
            .join("bin");
        std::fs::create_dir_all(&td).unwrap();
        let toolpy = td.join("python3");
        std::fs::write(&toolpy, "#!/bin/sh\nexit 0\n").unwrap();
        std::fs::set_permissions(&toolpy, std::fs::Permissions::from_mode(0o755)).unwrap();
        let got = probe_with_lock(&home.to_string_lossy());
        assert_eq!(
            got,
            toolpy.to_string_lossy().to_string(),
            "an illegal uv record must be rejected and fall back to the tools entry, got {got}"
        );
    }

    // same as above: the tools toolchain "can actually run" criterion depends on the unix executable bit => unix only
    #[cfg(unix)]
    #[test]
    fn tools_toolchain_python_discovered_for_official_host_layout() {
        // L3 2026-10-11 (run 38139097257): the official host (hermes v0.21.6) places the uv-managed CPython at
        // `{home}/tools/python-<ver>-*/bin/python3`, **without building a venv** => all three existing candidates miss,
        // the SDK is installed into the system python3 while the host interpreter has no SDK (welcome does not close the loop). This case locks in that the candidate table must include
        // `tools/*/bin/python3`, and that **a toolchain that cannot run is not picked** (criterion = can actually run, not path existence).
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();

        let mk = |rel: &str, rc: u8| -> std::path::PathBuf {
            let d = home.join("tools").join(rel).join("bin");
            std::fs::create_dir_all(&d).unwrap();
            let py = d.join("python3");
            std::fs::write(&py, format!("#!/bin/sh\nexit {rc}\n")).unwrap();
            std::fs::set_permissions(&py, std::fs::Permissions::from_mode(0o755)).unwrap();
            py
        };
        let broken = mk("python-9.9-broken", 1); // last in sort order (= "newest") but cannot run
        let good = mk("python-3.14.7+20260101-linux-x64", 0);
        assert!(
            broken.to_string_lossy() > good.to_string_lossy(),
            "precondition: the broken toolchain directory sorts last"
        );

        let _g = ENV_LOCK.lock().unwrap();
        let _bg = BoundGuard::new();
        let saved = std::env::var("AIMAIL_PYTHON").ok();
        std::env::remove_var("AIMAIL_PYTHON");
        let got = probe_in_domain("", &home.to_string_lossy());
        if let Some(v) = saved {
            std::env::set_var("AIMAIL_PYTHON", v);
        }
        assert_eq!(
            got,
            good.to_string_lossy().to_string(),
            "the runnable toolchain interpreter should be picked (the broken directory must be skipped)"
        );
    }
}
