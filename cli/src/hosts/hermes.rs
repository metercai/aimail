//! hermes host (python / aimail_hermes adapter layer + gateway routing).
//!
//! This file is the **entire data** for this host: identity features, pointer shapes, registration/assembly steps, health check items.
//! Engine is in `hosts/{mod,identity,pointer,runtime}.rs`; adding a host = adding a file in this directory,
//! Then add a line in `ALL` in `mod.rs`.

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
    install_steps: &[Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: Some("{home}/hermes-agent"), command_exists: None, runtime: None, runtime_not: Some("docker") }), argv: &["sh", "-c", "P='{python}'; if command -v uv >/dev/null 2>&1; then echo 'pkgmgr=uv'; uv pip install --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" agent-aimail; else echo 'pkgmgr=pip'; \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true; \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" agent-aimail; fi"], env: None, skip_if: None, on_missing: None, fail_hint: None, on_error: Some("warn"), warn_hint: Some("agent-aimail installation failed (host package manager self-adaptation uv->venv pip->ensurepip all three paths unavailable) exit {rc} (webhook adaptation runtime missing?)"), ok_text: Some("agent-aimail installed via uv|pip into {python}"), skipped_text: None }, Step::Spawn { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), argv: &["docker", "exec", "{container}", "sh", "-c", "cd /; unset UV_EXCLUDE_NEWER UV_INDEX_URL UV_INDEX PIP_INDEX_URL PIP_EXTRA_INDEX_URL 2>/dev/null; P=/opt/hermes/.venv/bin/python; [ -x \"$P\" ] || P=\"{container_home}/.venv/bin/python\"; ok=0\nif command -v uv >/dev/null 2>&1; then\n  [ -x \"$P\" ] || uv venv \"$P\" >/dev/null 2>&1 || true\n  for i in 1 2 3; do uv pip install --no-cache --upgrade --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" agent-aimail && { ok=1; break; }; sleep 8; done\nfi\nif [ \"$ok\" = 0 ]; then\n  echo pkgmgr=pip\n  \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true\n  for j in 1 2 3; do \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" agent-aimail && { ok=1; break; }; sleep 8; done\nfi\n\"$P\" -c \"import aimail\" >/dev/null 2>&1 || ok=0\n[ \"$ok\" = 1 ] || { echo '✗ agent-aimail installation failed (both uv and venv pip paths failed)'; exit 1; }\necho \"agent-aimail=$(\"$P\" -c 'import importlib.metadata as m;print(m.version(\"agent-aimail\"))' 2>/dev/null)\"\n"], env: None, skip_if: None, on_missing: Some("fail"), fail_hint: Some("docker CLI not found — --container mode requires docker on the host PATH"), on_error: Some("warn"), warn_hint: Some("agent-aimail installation failed (container internal package manager self-adaptation uv->venv pip->ensurepip three paths) exit {rc} — register/sdk file steps have been written to disk by sdk_install, can manually docker exec <c> <home>/.venv/bin/python -m pip install agent-aimail then re-run install"), ok_text: Some("agent-aimail installed inside container {container} (uv|pip adapted)"), skipped_text: None }, Step::SdkInstall { target: "hermes", fn_name: "install_hermes", ok_text: Some("hermes SDK install done (restart hermes gateway to take effect)"), fail_hint: None, on_error: None }, Step::RegisterDefault { when: None, on_error: None, warn_hint: Some("hermes main agent registration failed (see above) — retry with reset later") }, Step::RegisterAll { ok_text: Some("hermes --all-agents full registration complete"), warn_hint: Some("hermes full registration failed (can retry with reset --all-agents)") }, Step::Print { when: Some(When { cfg_complete: None, sid_set: None, path_exists: None, command_exists: None, runtime: Some("docker"), runtime_not: None }), text: "  docker: restart host = `docker restart {container}`; after container rebuild must re-run aimail install -H <home> … --container {container} (idempotent)" }],
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
                argv: &["{python}", "-m", "pip", "uninstall", "-y", "agent-aimail"],
                env: None,
                skip_if: None,
                on_missing: None,
                fail_hint: None,
                on_error: Some("warn"),
                warn_hint: Some("pip uninstall agent-aimail exit {rc} (webhook adapter runtime residue)"),
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
    register_all: Some(RegisterAll::PythonScript {
            python: "hermes/register_profiles.py",
            args: &[],
            env: EnvPairs(&[("AIMAIL_SYSTEM_ID", "{sid}")]),
            fail_hint: "register_profiles execution failed (see output above)",
        }),
    agent_check_remote: false,
    uses_agent_home: true,
    repair_hints: &["profiles"],
    agents_container: "profiles",
};
