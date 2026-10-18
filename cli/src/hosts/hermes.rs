//! hermes 宿主（python / aimail_hermes 适配层 + gateway 路由）。
//!
//! 本文件是该宿主的**全部数据**：身份特征、指针形态、注册/装配步骤、体检项。
//! 引擎在 `hosts/{mod,identity,pointer,runtime}.rs`；新增宿主 = 新增本目录一个文件，
//! 再在 `mod.rs` 的 `ALL` 里加一行。

use crate::hosts::exec::{EnvPairs, Profile, Register, RegisterAll, Step, When};
use crate::hosts::health::{Health, HealthSpec};
use crate::hosts::identity::{Detect, PointerKind};
use crate::hosts::HostDef;

pub static HERMES: HostDef = HostDef {
    id: "hermes",
    detect: Detect {
            dir_name: None,
            markers: &["hermes-agent"],
        },
    pointer: PointerKind::RootOrProfiles,
    register: Register::PythonModule {
            module: "aimail_hermes",
            fn_name: "_auto_register_email",
            profile: Profile {
                kind: "root_or_named",
                root_when: &["default", "{home_name}"],
            },
            pythonpath: &["hermes"],
        },
    home_dir: ".hermes",
    install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: Some("{home}/hermes-agent"), command_exists: None, runtime: None, runtime_not: Some("docker") }), argv: &["sh", "-c", "P='{python}'; if command -v uv >/dev/null 2>&1; then echo 'pkgmgr=uv'; uv pip install --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; else echo 'pkgmgr=pip'; \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true; \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; fi"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(宿主包管理器自适配 uv→venv pip→ensurepip 三径均不可用) exit {rc}(webhook 适配运行时缺失?)"), ok_text: Some("aimailsdk installed via uv|pip into {python}"), skipped_text: None }, Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), argv: &["docker", "exec", "{container}", "sh", "-c", "cd /; unset UV_EXCLUDE_NEWER UV_INDEX_URL UV_INDEX PIP_INDEX_URL PIP_EXTRA_INDEX_URL 2>/dev/null; P=/opt/hermes/.venv/bin/python; [ -x \"$P\" ] || P=\"{container_home}/.venv/bin/python\"; ok=0\nif command -v uv >/dev/null 2>&1; then\n  [ -x \"$P\" ] || uv venv \"$P\" >/dev/null 2>&1 || true\n  for i in 1 2 3; do uv pip install --no-cache --upgrade --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk && { ok=1; break; }; sleep 8; done\nfi\nif [ \"$ok\" = 0 ]; then\n  echo pkgmgr=pip\n  \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true\n  for j in 1 2 3; do \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk && { ok=1; break; }; sleep 8; done\nfi\n\"$P\" -c \"import aimail\" >/dev/null 2>&1 || ok=0\n[ \"$ok\" = 1 ] || { echo '✗ aimailsdk 安装失败(uv 与 venv pip 两径均失败)'; exit 1; }\necho \"aimailsdk=$(\"$P\" -c 'import importlib.metadata as m;print(m.version(\"aimailsdk\"))' 2>/dev/null)\"\n"], env: None, skip_if: None, on_missing: Some("fail"), fail_hint: Some("docker CLI not found — --container mode requires docker on the host PATH"), on_error: Some("warn"), warn_hint: Some("aimailsdk 安装失败(容器内包管理器自适配 uv→venv pip→ensurepip 三径) exit {rc} —— register/sdk 文件步骤已由 sdk_install 落盘, 可人工 docker exec <c> <home>/.venv/bin/python -m pip install aimailsdk 后重跑 install"), ok_text: Some("aimailsdk installed inside container {container} (uv|pip adapted)"), skipped_text: None }, Step::SdkInstall { target: "hermes", fn_name: "install_hermes", ok_text: Some("hermes SDK install done(重启 hermes gateway 生效)"), fail_hint: None, on_error: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("hermes 主 agent 注册失败(见上)——稍后 reset 重试") }, Step::RegisterAll { ok_text: Some("hermes --all-agents 全量注册完成"), warn_hint: Some("hermes 全量注册失败(可 reset --all-agents 重试)") }, Step::Print { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), text: "  docker: 重启宿主 = `docker restart {container}`; 容器重建后必须重跑 aimail install -H <home> … --container {container}(幂等)" }],
    uninstall_steps: &[
            Step::SdkUninstall {
                target: "hermes",
                fn_name: "uninstall_hermes",
            },
            Step::Spawn {
                when: Some(When {
                    cfg_complete: None,
                    sid_set: None,
                    path_exists: Some("{home}/hermes-agent"),
                    command_exists: None,
                    runtime: None,
                    runtime_not: None,
                }),
                argv: &["{python}", "-m", "pip", "uninstall", "-y", "aimailsdk"],
                env: None,
                skip_if: None,
                on_missing: None,
                fail_hint: None,
                on_error: Some("warn"),
                warn_hint: Some("pip uninstall aimailsdk exit {rc}(webhook 适配运行时残留)"),
                ok_text: None,
                skipped_text: None,
            },
        ],
    health_checks: &[
            Health {
                id: "patch-webhook",
                spec: HealthSpec::FileContains {
                    path: "{home}/hermes-agent/gateway/platforms/webhook.py",
                    marker: "PREPROCESS_REGISTRY",
                    ok_text: "webhook.py patched (PREPROCESS_REGISTRY present)",
                    fail_text: "webhook.py NOT patched or missing",
                },
            },
            Health {
                id: "patch-profiles",
                spec: HealthSpec::FileContainsAlt {
                    path: "{home}/hermes-agent/hermes_cli/profiles.py",
                    alt: "{home}/hermes-agent/cli/profiles.py",
                    marker: "AimailGateway",
                    ok_text: "profiles.py patched (AimailGateway hook present)",
                    fail_text: "profiles.py NOT patched or missing",
                },
            },
            Health {
                id: "skills",
                spec: HealthSpec::FileExistsAny {
                    paths: &[
                        "{home}/skills/agentmail/SKILL.md",
                        "{home}/profiles/*/skills/agentmail/SKILL.md",
                    ],
                    ok_text: "at least one profile has skills/agentmail/SKILL.md",
                    fail_text: "no profile has skills/agentmail/SKILL.md",
                },
            },
            Health {
                id: "toolsets",
                spec: HealthSpec::YamlToolsets {
                    configs: &["{home}/config.yaml", "{home}/profiles/*/config.yaml"],
                    ok_text: "platform_toolsets contains agentmail",
                    fail_text: "platform_toolsets lacks agentmail",
                },
            },
        ],
    payload_refs: &[],
    register_all: Some(RegisterAll::PythonScript {
            python: "hermes/register_profiles.py",
            args: &[],
            env: EnvPairs(&[("AIMAIL_SYSTEM_ID", "{sid}")]),
            fail_hint: "register_profiles 执行失败(见上输出)",
        }),
    agent_check_remote: false,
    uses_agent_home: true,
    repair_hints: &["profiles"],
    agents_container: "profiles",
};
