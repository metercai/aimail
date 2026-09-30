"""ensure_config.py — 幂等确保 Hermes profile 的 webhook 入站配置就位(库形态)。

安装断链根因(2026-08-16 多次): 安装链(install-tools.sh / configure.sh /
register_profiles.py)从未写以下配置,全靠手工补——缺任一项即断链:
  1. profile config.yaml `platform_toolsets.webhook` 缺 `agentmail`
     → webhook 会话回退默认工具集(hermes-webhook,无 send_mail),
     agent 物理上无法回邮件("收得到回不出")
  2. profile config.yaml `platform_toolsets.cli` 缺 `agentmail`
     → CLI 会话无邮件工具(用户定调 2026-08-16:"cli需要加")
  3. profile config.yaml `platforms.webhook.enabled` 缺失
     → 注册链 _ensure_profile_webhook 读不到,webhook_url 为空

⚠ 工具集键 = 内部标识恒为 `agentmail`(对外品牌 aimail 不动内部名;
platform_toolsets / skills 目录 / agentmail.json 等 Agent 内语义一律 agentmail)。

路由(webhook_subscriptions.json)由注册链 _auto_register_email →
_ensure_webhook_route 创建 hermes 的路由名(契约 HERMES_ROUTE_NAME =
`aimail-inbound`,入站路径 = 契约 HERMES_INBOUND_PATH =
`/webhooks/aimail-inbound`;skills 列表 = 内部工具/技能名 = 契约
AGENT_SKILL_NAME = `agentmail`)——**不需要第二条 inbound 路由**:bridge 转发
路径取自路由表全 URL;注册一条即契约路由名即可。
(路径/名字一律取 aimail_contract 常量, 别再写第二份字面量:真源 =
仓根 contract/aimail-contract.json, 门禁 tests/contract/check-contract-single-source.py)

本模块幂等: 已存在的配置项保留(尤其 secret——变更会致 bridge 转发
HMAC 401);只补缺失项。由 hermes/register_profiles.py(安装链 per-profile
落实)调用;原 CLI main() 已移除(argparse 入口不再需要)。

从 cli/hermes/ensure_webhook_config.py 迁移:函数体逐字保留,argparse
main 删除。公开 API: ensure_profile_config(profile_dir) -> list、
"""

import os
import secrets
import sys
from pathlib import Path

# ── 双形态自举(repo pysdk/ ↔ pip site-packages/aimail/)──
# repo 形态: dirname(dirname(__file__)) = pysdk/(含 aimail_base.py 等 flat core,
# 裸 import 可用)→ 加入 sys.path。pip 形态: 该目录 = site-packages/(无 flat core),
# 需先 import aimail 触发 aimail/__init__.py glue(把 aimail/ 目录插 sys.path,
# flat core 裸 import 才可用)。
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_CORE_DIR, "aimail_base.py")):
    if _CORE_DIR not in sys.path:
        sys.path.insert(0, _CORE_DIR)
else:
    try:
        import aimail  # noqa: F401  (pip 形态 glue)
    except Exception:
        pass

# agent 侧契约常量(真源 = 仓根 contract/aimail-contract.json)
import aimail_contract as _contract  # noqa: E402

# webhook 会话默认工具集(用户批准);仅确保契约 toolset 名存在,其余不覆盖
WEBHOOK_TOOLSET = [_contract.AGENT_TOOLSET_NAME, "web", "file", "terminal", "search", "delegation"]


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _dump_yaml(path: Path, data: dict) -> None:
    import yaml
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    tmp.replace(path)


def ensure_profile_config(profile_dir: Path) -> list:
    """确保 platforms.webhook.enabled + platform_toolsets.webhook/cli 含 agentmail。"""
    changes = []
    cfg_path = profile_dir / "config.yaml"
    if cfg_path.exists():
        cfg = _load_yaml(cfg_path)
    else:
        # J4e 根因(2026-09-30 定死, 证据两行):
        #   iso16 日志 557 行: ensure_profile_config: ['config.yaml missing
        #   (/opt/data/config.yaml) — skipped'] —— 首次 install 时 hermes 还没生成
        #   config.yaml, 旧逻辑直接 return ⇒ platform_toolsets 永远补不上;
        #   等 hermes 建好 config 后, 后续 install 又被 .agentmail 指针短路(iso16  contract-allowed: 注释文字(非代码)引用契约指针文件名
        #   86 行 registered:0)不再走到这里 ⇒ webhook 会话拿不到 agentmail 工具集。  contract-allowed: 注释文字(非代码)引用契约工具集名
        # 修法: 缺文件 ⇒ 幂等创建(只写本模块负责的 platforms.webhook +
        # platform_toolsets 两组键, 其余键交给 hermes 自己的默认值/后续写入,
        # 已存在键一律保留 —— 与下方"只补缺失项"同一语义)。
        cfg = {}
        changes.append(f"config.yaml created ({cfg_path})")

    dirty = False

    # 1) platforms.webhook.enabled(注册链 _ensure_profile_webhook 依赖)
    platforms = cfg.get("platforms") or {}
    wh = platforms.get("webhook") or {}
    if not wh.get("enabled"):
        # 复用已有端口/secret,缺则生成——与 _ensure_profile_webhook 同构
        port = wh.get("port") or wh.get("extra", {}).get("port") or 8644
        secret = wh.get("extra", {}).get("secret") or secrets.token_hex(32)
        platforms["webhook"] = {
            "enabled": True,
            "host": "0.0.0.0",
            "port": port,
            "extra": {"port": port, "secret": secret},
        }
        cfg["platforms"] = platforms
        changes.append(f"platforms.webhook enabled (port={port})")
        dirty = True

    # 2) platform_toolsets.webhook 含契约 toolset 名(webhook 会话工具能力)
    pt = cfg.get("platform_toolsets") or {}
    wh_tools = pt.get("webhook") or []
    if not isinstance(wh_tools, list):
        wh_tools = []
    if _contract.AGENT_TOOLSET_NAME not in wh_tools:
        if not wh_tools:
            wh_tools = list(WEBHOOK_TOOLSET)
        else:
            wh_tools.append(_contract.AGENT_TOOLSET_NAME)
        pt["webhook"] = wh_tools
        cfg["platform_toolsets"] = pt
        changes.append(f"platform_toolsets.webhook -> {wh_tools}")
        dirty = True

    # 3) platform_toolsets.cli 含契约 toolset 名(用户定调 cli 也要加)
    cli_tools = pt.get("cli") or []
    if not isinstance(cli_tools, list):
        cli_tools = []
    if _contract.AGENT_TOOLSET_NAME not in cli_tools:
        cli_tools.append(_contract.AGENT_TOOLSET_NAME)
        pt["cli"] = cli_tools
        cfg["platform_toolsets"] = pt
        changes.append(f"platform_toolsets.cli -> {cli_tools}")
        dirty = True

    if dirty:
        _dump_yaml(cfg_path, cfg)
    return changes


