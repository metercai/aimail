//! **SDK 根定位** —— CLI 里**唯一**知道"SDK 在哪、什么形态"的地方（C 的统一点）。
//!
//! 口径（owner 2026-10-04）：**rust CLI = 纯二进制命令行，自身零环境依赖**；一切"对环境有依赖"
//! 的代码归 SDK（pysdk→python / tssdk→node）。因此 CLI 只做两件事：
//! ①把**逻辑入口名**（注册表里 `hermes/register_profiles.py` 这类 SDK 根相对路径）落成实际调用；
//! ②起一个 SDK 执行进程。**布局知识不许散落在别处**——其余模块一律问本模块。
//!
//! **唯一形态**（owner 2026-10-06 定稿）：`pip` = 已安装的 `aimail` 包目录
//! （`python -c "import aimail;print(dirname(aimail.__file__))"`）—— **不存在** `aimail-src/`
//! 仓库快照态，也不存在"仓库态优先"的隐式或显式分支 ✗；资源一律取自**安装包** ✓。

use std::path::{Path, PathBuf};

/// P8(2026-10-17, owner 裁决):解释器是 **CLI 自动检测出的目标 agent 变量**,不是外部输入
/// ⇒ 进程环境 `AIMAIL_PYTHON` 写(`set_var`)删除;绑定改存**进程本地态**
/// (`bind_domain` 唯一写入点,"单真源"语义不变)。子进程(门/步骤/注册器/检查)
/// 经 `bound_python_env` 显式注入(给子进程设 env 不算污染,owner 裁决 2026-10-16),
/// 不再依赖进程环境继承。
static BOUND_PYTHON: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

/// 读绑定(None = 未绑定)。
fn bound_python() -> Option<String> {
    BOUND_PYTHON.lock().ok().and_then(|g| g.clone())
}

/// 绑定解释器的显式子进程 env 条目(未绑定 ⇒ None)。
pub(crate) fn bound_python_env() -> Option<(String, String)> {
    bound_python()
        .filter(|v| !v.trim().is_empty())
        .map(|v| ("AIMAIL_PYTHON".to_string(), v))
}

/// 已定位的 SDK 根。`kind` ∈ `repo` | `pip`（与 Python 侧 `resolve_source_root` 同词表）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SdkRoot {
    pub path: PathBuf,
    pub kind: &'static str,
}

/// 判定一个目录是否为"SDK 根"（标志物：`aimail_base.py`，两种形态都直接放在根下）。
pub fn is_sdk_root(dir: &Path) -> bool {
    dir.join("aimail_base.py").is_file()
}

/// 显式给定的根（`--source-root` / 调用方指定）：必须真的含 `aimail_base.py`，否则报错。
pub fn at(explicit: &str) -> Result<SdkRoot, String> {
    let root = crate::core::home::abs_path(&crate::core::home::expand_user(explicit));
    if !is_sdk_root(&root) {
        return Err(format!(
            "ERROR: --source-root 无效(无 aimail_base.py): {}",
            root.to_string_lossy()
        ));
    }
    Ok(SdkRoot {
        path: root,
        // owner 2026-10-06：只有一种形态（pip 已装包）⇒ `kind` 恒为 "pip"
        kind: "pip",
    })
}

/// 探测本机正在运行的 agent 所用解释器（要求该 venv 内确有 aimail ⇒ 不误采无关 venv）
pub fn probe_agent_python() -> Option<String> {
    #[cfg(windows)]
    {
        // Windows 镜像: 进程表枚举 python 族 + venv 内 aimail 判据(单真源 = procwin)
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

/// 域内探测：容器 ⇒ docker exec；宿主 ⇒ 候选逐个试 "能 import aimail"
pub fn probe_in_domain(container: &str, home: &str) -> String {
    // 单真源入口：外部覆盖(只读) → 进程本地绑定(与 `python_bin()` 链一致)
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
    // uv 托管依赖环境（hermes PM 布局，L3 hermes 格 2026-10-15 本地全量复现实证）：
    // 宿主 launcher = `tools/python -I`（隔离模式：**自身 site-packages 不在 sys.path**）
    // + `sys.path = <tree> + {home}/installs/<key>/environments/<key>/venv/site-packages`。
    // "运行中的进程从哪个 venv import" 的唯一记录 = 该 install 的 `facts.json`
    // （`packages.venv.environment`）——hermes PM 最后写入（完成标记）且
    // `pm/environments.py::_recorded_venv()` 就按它解析 ⇒ 读记录、不猜布局。
    // 校验镜像 `_recorded_venv`：env 必须在本 install 的 `environments/` 下 + `pyvenv.cfg`
    // 在场（hermes 自己的 sanity check）+ 解释器在场。多 install 取 facts.json 最新
    // （最后写入者 = 当前选中环境）。必须排在嵌套 venv / tools 工具链**之前**：
    // tools 工具链档的历史断言"该布局不建 venv"对新 hermes 已过时（实测宿主进程
    // import 的是 uv 托管 venv），采了它 = 装进 tools-python 的 site-packages = 宿主
    // 进程 import aimail 必失败（welcome 不闭环的根因）。
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
            // facts.json 里的 env 是**绝对**路径;home 给相对路径时 key 也相对 ⇒
            // 两侧 canonicalize 后比对,失败再退回字面比对。
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
            for bin in ["bin/python3", "bin/python"] {
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
    // agent 侧嵌套 venv 发现（布局驱动、零平台预设）：home 子目录下的 venv
    // （hermes 官方 install.sh 把 venv 建在 `{home}/hermes-agent/venv`，官方镜像为
    // `.venv`；gateway 进程 sys.path 指向该 venv 的 site-packages，SDK 必须装进这里）。
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
    // 官方宿主工具链兜底：`{home}/tools/python-<ver>-*/bin/python3`（无 installs/ 存储的
    // 旧版 hermes：宿主进程直接跑该解释器，见 L3 run 38139097257 的解释器矩阵）。
    // 注意：新版 hermes（PM/uv 托管布局）宿主进程 import 的是 installs 下的 uv venv ⇒
    // 上面 facts.json 档优先；本档只兜"没有 installs 存储"的旧布局，不再宣称"不建 venv"。
    // 判据 = **能实跑** `-c "import sys"`，多个候选取排序最后(版本最新)：开发机上残留的坏 toolchain
    // 目录不许被采到(与 tests/agent-env/verify.sh 同一规则 —— 该文件早已含此候选，CLI 缺它才不一致)。
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

/// 解释器**读取点**（起 SDK 执行进程时用）：`$AIMAIL_PYTHON` → 活进程探测 → PATH `python3`。
/// **写入点只有一个** = [`bind_domain`]（契约 §4.1(2) 单真源）—— 命令入口必须先绑定，
/// 否则这里退化成 CLI 自己的 `python3`（= "与目标系统不匹配"，deerflow 实测）。
pub fn python_bin() -> String {
    // 契约 v1.0 §4.1(2) 的**定位链**（单真源，三处消费：本模块解析、`sdkcall` 按名调用、`sdk` 门）：
    // `$AIMAIL_PYTHON`(只读覆盖) → **进程本地绑定**(bind_domain 设置) → 宿主 venv 探测
    // （运行中且能 `import aimail` 的 agent 解释器）→ PATH `python3`。
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

/// **域绑定**（唯一写入点，契约 §4.1(2) 单真源）：解析目标系统域内的宿主解释器并存
/// **进程本地态**（P8(2026-10-17)：进程环境 `AIMAIL_PYTHON` 写删除；门/步骤/注册器/检查
/// 子进程经 `bound_python_env` 显式注入）⇒ 一次绑定全程有效。
///
/// 容器场景（`docker exec`）返回的是**容器内路径** ⇒ 只返回、不绑定（当本机解释器用是错的）。
/// `$AIMAIL_PYTHON` 已在环境里时 `probe_in_domain` 直接返回它 ⇒ 显式覆盖优先（链首，语义不变）。
pub fn bind_domain(container: &str, home: &str) -> String {
    let py = probe_in_domain(container, home);
    if container.is_empty() && !py.trim().is_empty() {
        if let Ok(mut g) = BOUND_PYTHON.lock() {
            *g = Some(py.clone());
        }
    }
    py
}

/// 同上，域信息取自系统级 cfg（`system_home` + `container`）—— 命令入口没有 `-H/--container` 时
/// 的统一入口（`address` / `prompt` / `repair` 等：它们只有 `--system-id`）。
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

/// SDK 根定位：**只有 pip 已装包一种形态**（owner 2026-10-06：坚决彻底 —— 不存在 `aimail-src/`、
/// 不存在"仓库态"✗；资源一律取自**安装包**）。隐式与显式都不例外。
pub fn resolve() -> Result<SdkRoot, String> {
    resolve_with(&python_bin())
}

pub fn resolve_with(python: &str) -> Result<SdkRoot, String> {
    let out = std::process::Command::new(python)
        .args([
            "-c",
            // 只定位不执行(owner 2026-10-12):定位 SDK 根**不得靠运行 aimail 包**。
            // `find_spec` 不导入模块体；`origin` 为空 = 命名空间包 ⇒ 视为未装。
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
        "ERROR: 未找到已安装的 aimailsdk(解释器 {python} 下未定位到 aimail 包)"
    ))
}

/// 注册表"SDK 根相对入口" → 绝对路径。**唯一**把逻辑名落成路径的地方。
pub fn entry(root: &SdkRoot, rel: &str) -> PathBuf {
    root.path.join(rel)
}

/// 同上，`&Path` 形态（已拿到根路径的调用方复用，避免各处再拼）。
pub fn entry_path(root: &Path, rel: &str) -> PathBuf {
    root.join(rel)
}

/// 便利：解析失败时的**可播报**形态（只用于日志/`{sdk}` 上下文，**不要**用它决定"能不能跑"）。
/// owner 2026-10-06：不再退化到任何 `aimail-src/` 快照路径 ✗（该目录不存在也不应存在）。
pub fn resolve_or_placeholder() -> SdkRoot {
    match resolve() {
        Ok(r) => r,
        Err(e) => {
            // owner 2026-10-06：**不存在**仓库态/自包含载荷 ⇒ 解析不到 = "SDK 未安装" ⇒ 必须**响亮失败** ✗
            eprintln!("✗ SDK 未安装或不可用：{e}");
            eprintln!("  请先安装最新已发布版：pip install --index-url https://pypi.org/simple/ aimailsdk");
            std::process::exit(2);
        }
    }
}

/// 对探测所得解释器**实测**包管理器：uv 可用 ⇒ uv；否则该解释器的 pip 可用 ⇒ pip；否则空
pub fn probe_pkgmgr(container: &str, python: &str) -> String {
    #[cfg(windows)]
    {
        // 宿主域 = 直接试 python 自身(windows 无 sh -c 语义; 容器域在 windows 不成立)
        let py_ok = std::process::Command::new(python)
            .args(["-m", "pip", "--version"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
        if py_ok {
            return "pip".to_string();
        }
        let _ = container; // windows 宿主域不区分容器, 参数仅为与 unix 签名一致
        return String::new();
    }
    #[cfg(unix)]
    {
        let run = |cmd: &str| -> bool {
            let mut c = if container.is_empty() {
                std::process::Command::new("sh")
            } else {
                let mut d = std::process::Command::new("docker");
                d.args(["exec", container, "sh"]);
                d
            };
            c.args(["-c", cmd])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false)
        };
        if run(&format!("command -v uv >/dev/null 2>&1 && uv pip --version >/dev/null 2>&1 || uv pip install --python {} --dry-run aimailsdk >/dev/null 2>&1", python)) {
        return "uv".to_string();
    }
        if run(&format!("{} -m pip --version >/dev/null 2>&1", python)) {
            return "pip".to_string();
        }
        String::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // `AIMAIL_PYTHON`/进程本地绑定是进程级全局态：跨文件用例(l0/repair 读 python_bin)
    // 各改各的会互踩(P8 同类) ⇒ 共用 testutil 进程级锁串行化。
    use crate::core::testutil::ENV_LOCK;

    // P8(2026-10-17): 进程本地绑定(BOUND_PYTHON)也是进程级全局态 —— RAII 保存/清空/恢复,
    // 与 ENV_LOCK + env 存/还原同一模式。
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
        // 仓库布局（本仓）：SDK 根 = <prog>/aimail-src/pysdk ⇒ 两个 SDK 根相对入口直接可落
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("repo root")
            .join("pysdk");
        assert!(is_sdk_root(&repo), "仓库 pysdk 应含 aimail_base.py");
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
                "SDK 根相对入口应可直接落成文件: {}",
                p.display()
            );
            // 关键：条目**不含**仓库前缀 ⇒ 与 pip 形态（aimail/<rel>）同构
            assert!(
                !rel.starts_with("pysdk/"),
                "注册表条目不许带仓库前缀: {rel}"
            );
        }
    }

    #[test]
    fn explicit_root_must_be_a_sdk_root() {
        let tmp = tempfile::tempdir().unwrap();
        let err = at(&tmp.path().to_string_lossy()).unwrap_err();
        assert!(
            err.contains("--source-root 无效(无 aimail_base.py)"),
            "{err}"
        );
    }

    #[test]
    fn host_venv_without_python3_is_found_before_sdk_is_installed() {
        // 契约 §4.1(2)：宿主解释器 = **宿主自己配置的环境**。首次安装前 SDK 必然还没装 ⇒
        // 判据必须是**布局**而不是"能 import aimail"（deerflow = `<home>/backend/.venv/bin/python`，
        // 且该 venv 不保证有 `python3`）。
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let bin = home.join("backend").join(".venv").join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let py = bin.join("python"); // 只有 `python`
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
            "应命中宿主 venv 的 bin/python（不依赖 SDK 已安装）"
        );
    }

    #[test]
    fn bind_domain_exports_the_host_interpreter_for_the_door() {
        // 绑定后所有消费点（python 门 / 步骤子进程 / 注册链）读到的都是**宿主**解释器。
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
            "bind_domain 应返回宿主解释器"
        );
        assert_eq!(seen, got, "绑定后 python_bin() 必须读到同一个宿主解释器");
    }

    #[test]
    fn nested_agent_venv_discovered_without_platform_preset() {
        // C-4(2026-10-10): agent 侧嵌套 venv 由域内**发现**(布局驱动, 零平台预设)。
        // hermes 官方 install.sh 把 venv 建在 {home}/hermes-agent/venv —— 直接候选
        // ({home}/venv, {home}/bin, {home}/.venv) 都不命中 ⇒ 嵌套发现命中该 venv。
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let nested = home.join("hermes-agent").join("venv").join("bin");
        std::fs::create_dir_all(&nested).unwrap();
        let py = nested.join("python3");
        std::fs::write(&py, b"#!/bin/sh\n").unwrap();

        let _g = ENV_LOCK.lock().unwrap();
        let _bg = BoundGuard::new();
        // 清 AIMAIL_PYTHON(测试进程可能继承开发机 env, 会短路到覆盖分支)
        let saved = std::env::var("AIMAIL_PYTHON").ok();
        std::env::remove_var("AIMAIL_PYTHON");
        let got = probe_in_domain("", &home.to_string_lossy());
        if let Some(v) = saved {
            std::env::set_var("AIMAIL_PYTHON", v);
        }
        assert_eq!(
            got,
            py.to_string_lossy().to_string(),
            "应发现 {home:?} 下的嵌套 venv, 实得 {got}"
        );
    }

    // 构造一个 uv 托管 install 存储(hermes PM 布局)并返回 (facts.json 路径, venv python 路径)。
    // facts 里的 environment 用**绝对**路径(与 hermes 真实写盘一致)。
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

    // 判据依赖 unix 可执行位(tools 工具链"能实跑"= 0755)⇒ 仅 unix
    #[cfg(unix)]
    #[test]
    fn uv_managed_env_venv_preferred_over_tools_toolchain() {
        // L3 2026-10-15(本地全量复现)：hermes 官方宿主 launcher = tools/python `-I`
        // （隔离模式，自身 site-packages 不在 sys.path）+ uv 托管 venv 的 site-packages ⇒
        // SDK 必须装进 `installs/<key>/environments/<key>/venv`，facts.json 是唯一权威记录。
        // 本用例锁住：facts.json 档**优先于** tools 工具链档（采了后者 = 装了等于没装）。
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let (_, uvpy) = mk_pm_install(home, "8a4017c4cabfe15f");
        // 同时摆一个能实跑的 tools 工具链（旧布局候选），证明新档抢先。
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
            "facts.json 记录的 uv venv 应优先于 tools 工具链, 实得 {got}"
        );
    }

    #[test]
    fn uv_managed_env_fresh_facts_wins_over_stale() {
        // 多 install 存储：取 facts.json **最新**的（hermes PM 最后写 facts = 完成标记 ⇒
        // 最新者 = 当前选中环境）。旧 install 的 venv 不许被采。
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        let (_old_facts, old_py) = mk_pm_install(home, "aaaa000000000000");
        let (new_facts, new_py) = mk_pm_install(home, "bbbb111111111111");
        // 让 new 严格更新（秒级精度足够：先 touch old 回拨不可行时依赖创建序 + 显式 sleep 1）。
        std::thread::sleep(std::time::Duration::from_secs(1));
        std::fs::write(&new_facts, std::fs::read_to_string(&new_facts).unwrap()).unwrap();
        let got = probe_with_lock(&home.to_string_lossy());
        assert_eq!(
            got,
            new_py.to_string_lossy().to_string(),
            "应取 facts.json 最新的 install, 实得 {got} (old={})",
            old_py.to_string_lossy()
        );
    }

    // 同上:tools 工具链落点判据依赖 unix 可执行位 ⇒ 仅 unix
    #[cfg(unix)]
    #[test]
    fn uv_managed_env_rejects_env_outside_environments_or_without_pyvenv() {
        // 校验镜像 hermes `_recorded_venv`：env 不在本 install 的 environments/ 下 ⇒ 拒；
        // pyvenv.cfg 缺失 ⇒ 拒；全拒 ⇒ 落回 tools 工具链档。
        use std::os::unix::fs::PermissionsExt;
        let tmp = tempfile::tempdir().unwrap();
        let home = tmp.path();
        // 坏 facts：env 指向 install 外（伪造/损坏记录不得被采）
        let keydir = home.join("installs").join("cccc222222222222");
        std::fs::create_dir_all(&keydir).unwrap();
        std::fs::write(
            keydir.join("facts.json"),
            r#"{"packages":{"venv":{"environment":"/etc/foreign/venv"}},"schema":1}"#,
        )
        .unwrap();
        // 坏 venv：在 environments/ 下但缺 pyvenv.cfg
        let env2 = home
            .join("installs")
            .join("dddd333333333333")
            .join("environments")
            .join("dddd333333333333")
            .join("venv")
            .join("bin");
        std::fs::create_dir_all(&env2).unwrap();
        std::fs::write(env2.join("python3"), b"#!/bin/sh\n").unwrap(); // 无 pyvenv.cfg
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
        // 落点 = tools 工具链档（新档必须整体不命中）
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
            "非法 uv 记录须被拒并落回 tools 档, 实得 {got}"
        );
    }

    // 同上:tools 工具链"能实跑"判据依赖 unix 可执行位 ⇒ 仅 unix
    #[cfg(unix)]
    #[test]
    fn tools_toolchain_python_discovered_for_official_host_layout() {
        // L3 2026-10-11(run 38139097257)：官方宿主(hermes v0.21.6)把 uv 托管的 CPython 放在
        // `{home}/tools/python-<ver>-*/bin/python3`, **不建 venv** ⇒ 既有三条候选全不命中,
        // SDK 被装进系统 python3、宿主解释器里没有 SDK(welcome 不闭环)。本用例锁住候选表必须含
        // `tools/*/bin/python3`, 且**跑不起来的 toolchain 不被采**(判据 = 能实跑, 非路径存在)。
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
        let broken = mk("python-9.9-broken", 1); // 排序最后(= "最新")但跑不起来
        let good = mk("python-3.14.7+20260101-linux-x64", 0);
        assert!(
            broken.to_string_lossy() > good.to_string_lossy(),
            "前提: 坏 toolchain 目录排序在后"
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
            "应采能实跑的 toolchain 解释器(坏目录须跳过)"
        );
    }
}
