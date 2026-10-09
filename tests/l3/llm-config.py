#!/usr/bin/env python3
"""Per-platform LLM configuration writers for the journey's step 3 (plan P2, §3.4).

Why this exists: the journey's welcome closed loop (J4) needs a LIVE agent that can
answer, which means each host image must be pointed at the local llama-server from
inside its own configuration. Writing those files by hand per platform is exactly the
kind of thing that rots; here each shape is one function, and `verify` re-reads the
result independently so a writer that silently no-ops cannot pass.

Schemas come from authoritative sources only (a real, working config per platform):
  * openclaw  — the LLM-configured package built on 2026-09-26
                (models.providers.<id>.{baseUrl,api,apiKey,models[]} +
                 agents.defaults.model.primary);
  * pi        — this machine's ~/.pi/agent/models.json;
  * deer-flow — this machine's ~/deer-flow/config.yaml `models:` entry;
  * hermes    — this machine's ~/.hermes/config.yaml `model:` block
                (`hermes config path` resolves to <root>/profiles/<profile>/config.yaml,
                 so the base file is <home>/.hermes/config.yaml; when HERMES_HOME is set
                 the image's own data dir gets the same block — the container run, not
                 this writer, is what proves which file that agent reads).
  * dsh       — the profile's id-targeted patch layer `<home>/profiles/<p>/cordis.patch.yml`
                (`- id: llm-deepseek` + config.baseURL/apiKeyEnv). Authoritative shape: the
                product's own fixtures `~/deepseek-harness/apps/cli/tests/profiles/headless/
                tests/fixtures/deepseek-defaults.patch.yml:8-10` and `.../acp/tests/fixtures/
                image-offload.cordis.yml:4-8`, over the shipped row `packages/bundle/base/
                cordis.patch.yml:524-525`; names/keys from `packages/llm/llm-deepseek/src/
                config.ts` (schema: apiKeyEnv default DEEPSEEK_API_KEY, baseURL z.string()).
                Two consequences the caller must own: (1) a patch replaces the target row's
                WHOLE `config` (bundle header), so other keys are dropped-but-defaulted;
                (2) a credential is NOT a file concern — apiKeyEnv only NAMES the env var
                (`config.ts` apiKeyEnv is a credential ref), so the live key still has to be
                in the launched process's environment (J4c: DEEPSEEK_API_KEY from the repo's
                own usage, tests/SDK/docker-regression/hosts/agent-turn-dsh.sh:187).
                `--model` is deliberately not written: the provider endpoint is what has to
                move, and the local endpoint answers any model id (measured 2026-09-29).

Exit vocabulary: 0 = written/verified, 1 = verification mismatch (a real red),
2 = cannot judge (platform has no writer / config unreadable).

Usage:
  llm-config.py implemented
  llm-config.py write  --platform P --home H --base-url U --model M [--api-key K]
                       [--extra-home PATH]  # explicit, repeatable (hermes only today)
  llm-config.py verify --platform P --home H --base-url U --model M

Write set rule (incident 2026-09-27): only paths the CALLER names (--home, and
--extra-home when given) are ever written. No environment variable may widen it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

DEFAULT_API_KEY = "local-qwen-dummy"


def _provider_id(base_url: str) -> str:
    """openclaw/pi provider id derived from the endpoint (matches the working package:
    http://127.0.0.1:8000/v1 -> custom-127-0-0-1-8000)."""
    host = base_url.split("//")[-1].split("/")[0]
    return "custom-" + "".join(c if c.isalnum() else "-" for c in host)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text() or "{}")
    except Exception:  # noqa: BLE001 - a corrupt file must be surfaced, not silently replaced
        raise RuntimeError(f"existing {path} is not valid JSON")
    return data if isinstance(data, dict) else {}


def _load_yaml(path: Path) -> dict:
    import yaml
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text() or "{}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"existing {path} is not valid YAML: {e}")
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict, merged: bool) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    # A merge rewrites a config the image shipped (comments are lost by the round-trip),
    # so keep one copy of the original next to it for the failure post-mortem.
    if merged and path.is_file():
        bak = path.with_suffix(path.suffix + ".pre-llm.bak")
        if not bak.exists():
            shutil.copy2(path, bak)
    assert isinstance(data, dict), "this writer only emits top-level mappings"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".pre-llm.bak")
        if not bak.exists():
            shutil.copy2(path, bak)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── writers: each returns the list of files it wrote ──────────────────────────
def write_openclaw(home: Path, base_url: str, model: str, api_key: str) -> list[Path]:
    p = home / "openclaw.json"
    cfg = _load_json(p)
    pid = _provider_id(base_url)
    models = cfg.setdefault("models", {})
    models["mode"] = "merge"
    # The model entry must carry the full shape openclaw's schema demands. Measured
    # 2026-09-27 in the journey: an entry with only `id` makes the gateway refuse to
    # start — `models.providers.<id>.models[0].name: expected string, received
    # undefined`. These fields are copied from the LLM-configured package that was
    # verified working on 2026-09-26, not invented.
    models.setdefault("providers", {})[pid] = {
        "baseUrl": base_url, "api": "openai-completions", "apiKey": api_key,
        "models": [{
            "id": model,
            "name": f"{model} (journey llama-server)",
            "contextWindow": 128000,
            "maxTokens": 4096,
            "input": ["text"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "reasoning": False,
        }],
    }
    defaults = cfg.setdefault("agents", {}).setdefault("defaults", {})
    defaults.setdefault("model", {})["primary"] = f"{pid}/{model}"
    defaults.setdefault("models", {})[f"{pid}/{model}"] = {}
    # `gateway.mode` must be PRESENT: openclaw refuses to start without it ("existing
    # config is missing gateway.mode … suspicious or clobbered config", measured
    # 2026-09-27 in the journey). An image whose config already carries a `gateway`
    # table would keep it empty if this only used setdefault on the whole key.
    gw = cfg.setdefault("gateway", {})
    if not isinstance(gw, dict):
        gw = cfg["gateway"] = {}
    gw.setdefault("mode", "local")
    gw.setdefault("bind", "loopback")
    gw.setdefault("port", 18789)
    _write_json(p, cfg)
    return [p]


def write_pi(home: Path, base_url: str, model: str, api_key: str) -> list[Path]:
    p = home / "agent" / "models.json"
    cfg = _load_json(p)
    pid = "qwen-local" if "127.0.0.1:8000" in base_url else _provider_id(base_url)
    cfg.setdefault("providers", {})[pid] = {
        "baseUrl": base_url, "api": "openai-completions", "apiKey": api_key,
        "models": [{"id": model}],
    }
    _write_json(p, cfg)
    return [p]


def write_deerflow(home: Path, base_url: str, model: str, api_key: str) -> list[Path]:
    p = home / "config.yaml"
    cfg = _load_yaml(p)
    # 最小骨架必须在(sandbox.use 必填): deer-flow 的 AppConfig 是整文件校验, 缺 sandbox
    # ⇒ create_app pydantic ValidationError ⇒ gateway 起不来。镜像里原本【没有】config.yaml
    # (探针按"文件不存在"自造含 sandbox 的骨架), 一旦预置/写入器先落了只有 models 的文件,
    # 探针的创建分支被跳过、gateway 全线起不来(r48 实测教训)。与 agent-turn-deerflow 的
    # 骨架同形, 缺则补种(sandbox.use 必填), 已有则原样保留。
    if not isinstance(cfg.get("sandbox"), dict) or not cfg["sandbox"].get("use"):
        sb = cfg.get("sandbox") if isinstance(cfg.get("sandbox"), dict) else {}
        sb.setdefault("use", "deerflow.sandbox.local:LocalSandboxProvider")
        sb.setdefault("allow_host_bash", False)
        cfg["sandbox"] = sb
    entry = {
        "name": model, "display_name": f"{model} (journey llama-server)", "use":
        "langchain_openai:ChatOpenAI", "model": model, "api_key": api_key,
        "base_url": base_url, "request_timeout": 600.0, "max_retries": 2,
        "supports_vision": False,
    }
    rows = cfg.get("models")
    if isinstance(rows, list):
        rows = [r for r in rows if not (isinstance(r, dict) and r.get("name") == model)]
        cfg["models"] = [entry] + rows
    else:
        cfg["models"] = [entry]
    _write_yaml(p, cfg, merged=True)
    return [p]


def write_hermes(home: Path, base_url: str, model: str, api_key: str,
                 extra_homes: list[Path] | None = None) -> list[Path]:
    """hermes: <home>/.hermes/config.yaml, plus any EXPLICIT --extra-home.

    Incident 2026-09-27: the first version also wrote $HERMES_HOME/config.yaml. Run
    host-side on a developer machine, HERMES_HOME points at the operator's LIVE
    profile, so a test writer silently rewrote a live config (the model block of
    ~/.hermes/profiles/agentmail/config.yaml). An environment variable must never be
    able to widen a test tool's write set: targets are what the caller names, nothing
    else. The container layout difference (e.g. HERMES_HOME=/opt/data) is the
    caller's business — it passes --extra-home explicitly in that case.

    Text-based (stdlib only, same reason as the dsh writer below): the macOS runner's
    system python3 has NO PyYAML (`python3 -c "import yaml"` → ModuleNotFoundError,
    measured 2026-10-09 in L3), so a yaml-import writer dies there. The file is the
    installer's cli-config.yaml.example (2329 lines, top-level `model:` block, 2-space
    indented active keys, commented examples); only the model block's active keys are
    touched, in place.
    """
    written = []
    for p in [home / ".hermes" / "config.yaml"] + [Path(e) / "config.yaml" for e in (extra_homes or [])]:
        # L3: a live endpoint (not the journey's keyless stub) needs the credential.
        # hermes seeds its credential pool from `model.api_key` when
        # model.provider == "custom" (credential_pool.py:3034-3056), so the key is
        # written inline; the dummy journey key is never written.
        keys = {"provider": "custom", "default": model, "base_url": base_url}
        if api_key and api_key != DEFAULT_API_KEY:
            keys["api_key"] = api_key
        if p.is_file():
            bak = p.with_suffix(p.suffix + ".pre-llm.bak")
            if not bak.exists():
                shutil.copy2(p, bak)
            lines = p.read_text().splitlines(keepends=True)
            start, end = _hermes_model_block(lines)
            if start is None:
                lines += ["model:\n"]
                start, end = len(lines) - 1, len(lines)
            assert start is not None and end is not None
            end = _hermes_upsert_model(lines, start, end, keys)
            p.write_text("".join(lines))
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            body = ["model:\n"] + [f'  {k}: "{v}"\n' for k, v in keys.items()]
            p.write_text("".join(body))
        written.append(p)
    return written


# ── hermes: the model block is edited as TEXT, on purpose (stdlib only) ─────────
_HERMES_MODEL_KEY_RE = re.compile(r"^(provider|default|base_url|api_key)\s*:\s*(.*?)\s*$")


def _hermes_model_block(lines: list[str]) -> tuple[int | None, int | None]:
    """(start, end) line indexes of the top-level `model:` block, or (None, None)."""
    for i, ln in enumerate(lines):
        if re.match(r"^model:\s*(#.*)?$", ln.rstrip("\n")):
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s and not s.startswith("#") and not lines[j].startswith(" "):
                    break
                j += 1
            return i, j
    return None, None


def _hermes_upsert_model(lines: list[str], start: int, end: int,
                         keys: dict[str, str]) -> int:
    """Upsert `key: "value"` pairs in the model block (active lines only —
    the installer template carries commented examples that must never match)."""
    for key, value in keys.items():
        new = f'  {key}: "{value}"\n'
        hit = None
        for i in range(start + 1, end):
            s = lines[i].strip()
            if s.startswith("#"):
                continue
            if re.match(rf"^\s+{re.escape(key)}\s*:", lines[i]):
                hit = i
                break
        if hit is not None:
            lines[hit] = new
        else:
            lines.insert(start + 1, new)
            end += 1
    return end


# ── dsh: the patch layer is edited as TEXT, on purpose ───────────────────────
# Measured 2026-09-29 in the journey: the dsh image ships python3 3.12 with NO PyYAML
# (`python3 -c "import yaml"` → ModuleNotFoundError), so a yaml-based writer cannot run
# there at all — the first version of this branch died with exactly that error, and J4b
# reported it honestly as a GAP. The file it touches is a small, well-specified YAML LIST
# of id-targeted patch rows, so it is read and rewritten line-wise with no third-party
# dependency. Anything not recognisable as that shape is refused (rc=2), never guessed.
_DSH_PLUGIN_ID = "llm-deepseek"
_DSH_API_KEY_ENV = "DEEPSEEK_API_KEY"
_DSH_ROW_RE = re.compile(rf"^- id:\s*{_DSH_PLUGIN_ID}\s*$")
_DSH_TOP_ROW_RE = re.compile(r"^-(\s|$)")


def _dsh_assert_list(path: Path, lines: list[str]) -> None:
    """A patch file must be a top-level SEQUENCE. `[]` and an all-comment file are empty
    lists; a first meaningful line that is not a row means the shape is not what this
    writer understands ⇒ refuse instead of rewriting a file we misread."""
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s == "[]":
            return
        if not _DSH_TOP_ROW_RE.match(ln):
            raise RuntimeError(f"{path} is not a top-level YAML list of patch rows")
        return


def _dsh_row_span(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indexes of the top-level `- id: llm-deepseek` row, or None."""
    for i, ln in enumerate(lines):
        if _DSH_ROW_RE.match(ln.rstrip("\n")):
            j = i + 1
            while j < len(lines) and not _DSH_TOP_ROW_RE.match(lines[j]):
                j += 1
            return i, j
    return None


def _dsh_upsert_config(lines: list[str], start: int, end: int,
                       keys: dict[str, str]) -> int:
    """Upsert `key: value` pairs under the row's `config:` table; returns the new row end.

    The loader replaces a targeted row's WHOLE `config` rather than merging into it, so a
    second row for the same id would discard the operator's other keys anyway; editing the
    one row in place keeps them and still wins (last write per row)."""
    def indent(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    cfg_i = next((i for i in range(start + 1, end)
                  if lines[i].strip() == "config:"), None)
    if cfg_i is None:
        lines.insert(start + 1, "  config:\n")
        cfg_i, end = start + 1, end + 1
    cfg_indent = indent(lines[cfg_i])
    child_indent = cfg_indent + 2
    for i in range(cfg_i + 1, end):
        s = lines[i]
        if s.strip() and not s.strip().startswith("#") and indent(s) > cfg_indent:
            child_indent = indent(s)
            break
    for key, value in keys.items():
        hits = [i for i in range(cfg_i + 1, end)
                if re.match(rf"^\s*{re.escape(key)}\s*:", lines[i]) and indent(lines[i]) > cfg_indent]
        for i in reversed(hits[1:]):
            del lines[i]
            end -= 1
        new = " " * child_indent + f"{key}: {value}\n"
        if hits:
            lines[hits[0]] = new
        else:
            j = cfg_i + 1
            while j < end:
                s = lines[j]
                if s.strip() and not s.strip().startswith("#") and indent(s) <= cfg_indent:
                    break
                j += 1
            lines.insert(j, new)
            end += 1
    return end


def _dsh_config_value(lines: list[str], span: tuple[int, int], key: str) -> str:
    for ln in lines[span[0] + 1:span[1]]:
        m = re.match(rf"^\s*{re.escape(key)}\s*:\s*(.*?)\s*$", ln)
        if m:
            v = m.group(1).strip().strip("'\"")
            if v:
                return v
    return ""


def _dsh_profiles(home: Path) -> list[Path]:
    prof_root = home / "profiles"
    return [d for d in sorted(prof_root.iterdir())
            if d.is_dir() and (d / "cordis.yml").is_file()] if prof_root.is_dir() else []


def write_dsh(home: Path, base_url: str, model: str, api_key: str) -> list[Path]:
    """dsh: the profile's id-targeted patch layer (cordis.patch.yml).

    Authoritative: the product ships `- id: llm-deepseek` in packages/bundle/base/cordis.patch.yml:524-525
    and its own profile fixtures override exactly that row's config
    (apps/cli/tests/profiles/headless/tests/fixtures/deepseek-defaults.patch.yml:8-10;
    apps/cli/tests/profiles/acp/tests/fixtures/image-offload.cordis.yml:4-10). The profile
    root is itself an empty patch list ("Edit cordis.patch.yml, not this file"), so this is
    the file a profile owner is told to edit. Key names/schema: packages/llm/llm-deepseek/src/config.ts.

    Write set: <home>/profiles/<p>/ for every profile that EXISTS under the caller-named
    --home. The profile set is a property of that home (a walk under a named path), not of
    the environment — no env var can widen it (incident 2026-09-27 rule).

    Credential, stated so nobody reads this as a closed loop: `apiKeyEnv` only NAMES an env
    var (config.ts models it as a credential reference), so the live key still has to be in
    the launched process's environment — J4c's job, not a file writer's.
    """
    profs = _dsh_profiles(home)
    if not profs:
        raise RuntimeError(f"no dsh profile under {home / 'profiles'} — nothing to patch, cannot judge")
    written = []
    for prof in profs:
        p = prof / "cordis.patch.yml"
        lines = p.read_text().splitlines(keepends=True) if p.is_file() else []
        _dsh_assert_list(p, lines)
        span = _dsh_row_span(lines)
        if span is None:
            # An empty profile layer is literally `[]` (the shipped default). Appending a
            # block row after a flow-empty line yields `[]` followed by `- id: …`, which is
            # NOT valid YAML — measured on the first text-based cut. Replace that line.
            block = [f"- id: {_DSH_PLUGIN_ID}\n", "  config:\n"]
            empty = next((i for i, ln in enumerate(lines) if ln.strip() == "[]"), None)
            if empty is not None:
                lines[empty:empty + 1] = block
                span = (empty, empty + len(block))
            else:
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines += block
                span = (len(lines) - len(block), len(lines))
        end = _dsh_upsert_config(lines, span[0], span[1],
                                 {"baseURL": base_url, "apiKeyEnv": "DEEPSEEK_API_KEY"})
        assert end >= span[1]
        if p.is_file():
            bak = p.with_suffix(p.suffix + ".pre-llm.bak")
            if not bak.exists():
                shutil.copy2(p, bak)
        p.write_text("".join(lines))
        written.append(p)
    return written


# Keyed by the REGISTRY platform id (cli/platforms.json): the journey passes
# `--platform $PLAT` (= "deerflow"), while these dicts historically used the
# "deer-flow" spelling -> `plat not in WRITERS` -> "CANNOT JUDGE: unknown platform
# 'deerflow'" rc=2 and two dead J4 gaps (2026-10-02). Alias table below keeps the old
# spelling accepted so any other caller keeps working.
PLATFORM_ALIASES = {
    "deer-flow": "deerflow",
    # L3 矩阵格名 ≠ 注册表平台 id: dsh 两条 README 公布路径各占一格,
    # LLM 配置形态相同 ⇒ 都归 dsh writer(2026-10-09 矩阵扩展时补)。
    "dsh-aimail": "dsh",
    "dsh-plugin": "dsh",
}

WRITERS = {
    "openclaw": write_openclaw,
    "pi": write_pi,
    "deerflow": write_deerflow,
    "hermes": write_hermes,
    "dsh": write_dsh,
}
UNIMPLEMENTED: dict[str, str] = {}


# ── verify: re-read the files and check the values independently ──────────────
def verify_openclaw(home: Path, base_url: str, model: str) -> list[str]:
    cfg = _load_json(home / "openclaw.json")
    pid = _provider_id(base_url)
    prov = (((cfg.get("models") or {}).get("providers") or {}).get(pid) or {})
    bad = []
    if prov.get("baseUrl") != base_url:
        bad.append(f"models.providers.{pid}.baseUrl={prov.get('baseUrl')!r} != {base_url!r}")
    ids = [m.get("id") for m in (prov.get("models") or []) if isinstance(m, dict)]
    if model not in ids:
        bad.append(f"models.providers.{pid}.models ids={ids} lacks {model!r}")
    # Fields openclaw's schema refuses to start without (measured 2026-09-27: a bare
    # `{id}` entry → "models[0].name: expected string, received undefined").
    for entry in prov.get("models") or []:
        if not isinstance(entry, dict) or entry.get("id") != model:
            continue
        for req in ("name", "contextWindow", "maxTokens", "input"):
            if not entry.get(req):
                bad.append(f"models.providers.{pid}.models[{model}].{req} is missing — "
                           f"openclaw refuses to start in that state")
    prim = ((cfg.get("agents") or {}).get("defaults") or {}).get("model", {}).get("primary")
    if prim != f"{pid}/{model}":
        bad.append(f"agents.defaults.model.primary={prim!r} != {f'{pid}/{model}'!r}")
    # openclaw will not start without gateway.mode — a writer that leaves it empty has
    # produced an unusable config, so verify it here (learned from the 2026-09-27 run).
    mode = (cfg.get("gateway") or {}).get("mode")
    if not mode:
        bad.append("gateway.mode is empty — openclaw refuses to start in that state")
    # 装前模式(owner 裁决 C, 2026-10-03): 预置镜像 = 纯原生, 适配器接线(hooks.token
    # ensureHooksToken)发生在门禁的安装步 ⇒ 构建期验收只判 LLM 配置本体, 接线项留给
    # 装后(旅程 J4b)的同一 verifier。LLM_CONFIG_PRE_INSTALL=1 时跳过接线类判项。
    if os.environ.get("LLM_CONFIG_PRE_INSTALL") != "1":
        if not ((cfg.get("hooks") or {}).get("token") or "").strip():
            bad.append("hooks.token missing — the openclaw ADAPTER must have wired it "
                       "(openclaw-aimail ensureHooksToken); fresh scratch homes fail this on purpose")
    return bad


def verify_pi(home: Path, base_url: str, model: str) -> list[str]:
    cfg = _load_json(home / "agent" / "models.json")
    bad = []
    hit = False
    for pid, prov in (cfg.get("providers") or {}).items():
        if not isinstance(prov, dict) or prov.get("baseUrl") != base_url:
            continue
        ids = [m.get("id") for m in (prov.get("models") or []) if isinstance(m, dict)]
        if model in ids:
            hit = True
        else:
            bad.append(f"providers.{pid}.models ids={ids} lacks {model!r}")
    if not hit and not bad:
        bad.append(f"no provider in .pi/agent/models.json points at {base_url!r}")
    return bad


def verify_deerflow(home: Path, base_url: str, model: str) -> list[str]:
    cfg = _load_yaml(home / "config.yaml")
    rows = cfg.get("models") or []
    bad = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and r.get("name") == model:
            if r.get("base_url") != base_url:
                bad.append(f"models[{model}].base_url={r.get('base_url')!r} != {base_url!r}")
            if "langchain" not in str(r.get("use", "")):
                bad.append(f"models[{model}].use={r.get('use')!r} is not a langchain adapter")
            return bad
    return [f"config.yaml models[] has no entry named {model!r}"]


def verify_hermes(home: Path, base_url: str, model: str, extra_homes: list[str] | None = None) -> list[str]:
    # 双写布局: 宿主进程读 extra-homes 的 config.yaml(真 HERMES_HOME),
    # scratch 是 verify 专用副本 —— 两个位置都要对, 宿主那份才是真正生效的。
    # 文本读取(model 块内活跃行), 与 write_hermes 同形; 纯标准库(macOS 无 PyYAML)。
    bad: list[str] = []
    checked = [home / ".hermes" / "config.yaml"]
    for e in (extra_homes or []):
        checked.append(Path(e) / "config.yaml")
    for p in checked:
        if not p.is_file():
            bad.append(f"{p} does not exist")
            continue
        lines = p.read_text().splitlines(keepends=True)
        start, end = _hermes_model_block(lines)
        if start is None:
            bad.append(f"{p} has no top-level model: block")
            continue
        span = (start, end or 0)
        vals: dict[str, str] = {}
        for ln in lines[span[0] + 1:span[1]]:
            s = ln.strip()
            if s.startswith("#"):
                continue
            m = re.match(r"^\s+(provider|default|base_url|api_key)\s*:\s*(.*?)\s*$", ln)
            if m:
                vals[m.group(1)] = m.group(2).strip().strip('"')
        if vals.get("base_url") != base_url:
            bad.append(f"{p} model.base_url={vals.get('base_url')!r} != {base_url!r}")
        if vals.get("default") != model:
            bad.append(f"{p} model.default={vals.get('default')!r} != {model!r}")
    return bad


def verify_dsh(home: Path, base_url: str, model: str) -> list[str]:
    """Re-read every profile's patch layer and check the llm-deepseek row independently.

    Scope, stated honestly: this verifies the FILE the host will compose from — it is not a
    credential check (`apiKeyEnv` only names an environment variable), so a green here does
    NOT prove the agent can authenticate. That is what J4c's live turn is for.
    """
    profs = _dsh_profiles(home)
    if not profs:
        raise RuntimeError(f"no dsh profile under {home / 'profiles'} — nothing to verify")
    bad = []
    for prof in profs:
        p = prof / "cordis.patch.yml"
        if not p.is_file():
            bad.append(f"{p} does not exist")
            continue
        lines = p.read_text().splitlines(keepends=True)
        span = _dsh_row_span(lines)
        if span is None:
            bad.append(f"{p} has no `- id: {_DSH_PLUGIN_ID}` patch row")
            continue
        got = _dsh_config_value(lines, span, "baseURL")
        if got != base_url:
            bad.append(f"{p} {_DSH_PLUGIN_ID}.config.baseURL={got!r} != {base_url!r}")
        ref = _dsh_config_value(lines, span, "apiKeyEnv")
        if ref != _DSH_API_KEY_ENV:
            bad.append(f"{p} {_DSH_PLUGIN_ID}.config.apiKeyEnv={ref!r} != {_DSH_API_KEY_ENV!r} — "
                       f"the launcher exports that name, so a different reference means the "
                       f"live turn would request without a key (the writer pins it on purpose)")
    return bad


VERIFIERS = {
    "openclaw": verify_openclaw,
    "pi": verify_pi,
    "deerflow": verify_deerflow,
    "hermes": verify_hermes,
    "dsh": verify_dsh,
}


def _parse(args: list[str]) -> dict:
    opts: dict = {"api_key": DEFAULT_API_KEY, "extra_homes": []}
    i = 0
    while i < len(args):
        if args[i] in ("--platform", "--home", "--base-url", "--model", "--api-key", "--extra-home") and i + 1 < len(args):
            key = args[i][2:].replace("-", "_")
            if key == "extra_home":
                opts["extra_homes"].append(args[i + 1])
            else:
                opts[key] = args[i + 1]
            i += 2
        else:
            print(f"unknown argument {args[i]}", file=sys.stderr)
            sys.exit(2)
    return opts


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "implemented"
    if cmd == "implemented":
        for p in sorted(WRITERS):
            print(p)
        for p, why in sorted(UNIMPLEMENTED.items()):
            print(f"# {p}: NOT implemented — {why}")
        return 0

    opts = _parse(argv[2:])
    plat = PLATFORM_ALIASES.get(opts.get("platform", ""), opts.get("platform", ""))
    home = Path(opts.get("home", ""))
    base_url = opts.get("base_url", "")
    model = opts.get("model", "")
    if not (plat and str(home) and base_url and model):
        print("usage: llm-config.py write|verify --platform P --home H --base-url U --model M",
              file=sys.stderr)
        return 2
    if plat in UNIMPLEMENTED:
        print(f"CANNOT JUDGE: no LLM writer for {plat} — {UNIMPLEMENTED[plat]}", file=sys.stderr)
        return 2
    if plat not in WRITERS:
        print(f"CANNOT JUDGE: unknown platform {plat!r}", file=sys.stderr)
        return 2

    if cmd == "write":
        home.mkdir(parents=True, exist_ok=True)
        try:
            if plat == "hermes":
                paths = WRITERS[plat](home, base_url, model, opts["api_key"],
                                      [Path(e) for e in opts["extra_homes"]])
            else:
                paths = WRITERS[plat](home, base_url, model, opts["api_key"])
        except RuntimeError as e:
            print(f"write failed: {e}", file=sys.stderr)
            return 2
        print(f"llm-config[{plat}]: wrote {', '.join(str(p) for p in paths)} "
              f"(base_url={base_url} model={model})")
        return 0
    if cmd == "verify":
        try:
            if plat == "hermes":
                bad = VERIFIERS[plat](home, base_url, model, opts.get("extra_homes"))
            else:
                bad = VERIFIERS[plat](home, base_url, model)
        except RuntimeError as e:
            print(f"CANNOT JUDGE: {e}", file=sys.stderr)
            return 2
        if bad:
            for b in bad:
                print(f"  ✗ {b}")
            print(f"llm-config verify[{plat}]: 1 FAIL, 0 PASS")
            return 1
        print(f"llm-config verify[{plat}]: ok ({base_url} / {model})")
        print(f"llm-config verify[{plat}]: 1 PASS, 0 FAIL")
        return 0
    print(f"unknown command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
