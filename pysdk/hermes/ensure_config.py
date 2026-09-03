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

⚠ 工具集键 = 内部标识 `agentmail`(2026-09-03 d6d035d internal-name
ruling: 改名只动外部品牌 aimail,platform_toolsets / skills 目录 /
agentmail.json 等 Agent 内语义一律保留 agentmail)。

路由(webhook_subscriptions.json)由注册链 _auto_register_email →
_ensure_webhook_route 创建 `aimail-inbound`(skills=['agentmail'],路由名
= 外部入站路径用 aimail-inbound;skills 列表 = 内部工具/技能名 agentmail)
——**不需要第二个 amail-inbound 路由**:bridge 转发路径是路由表全 URL
(http://127.0.0.1:8646/webhooks/aimail-inbound),不是旧版硬编码
拼接 /webhooks/amail-inbound;注册一条 aimail-inbound 即可。

本模块幂等: 已存在的配置项保留(尤其 secret——变更会致 bridge 转发
HMAC 401);只补缺失项。由 hermes/register_profiles.py(安装链 per-profile
落实)调用;原 CLI main() 已移除(argparse 入口不再需要)。

从 cli/hermes/ensure_webhook_config.py 迁移:函数体逐字保留,argparse
main 删除。公开 API: ensure_profile_config(profile_dir) -> list、
is_amail_profile(profile_dir) -> bool。
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

# webhook 会话默认工具集(用户批准);仅确保 agentmail 存在,其余不覆盖
WEBHOOK_TOOLSET = ["agentmail", "web", "file", "terminal", "search", "delegation"]


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
    if not cfg_path.exists():
        changes.append(f"config.yaml missing ({cfg_path}) — skipped")
        return changes

    cfg = _load_yaml(cfg_path)
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

    # 2) platform_toolsets.webhook 含 agentmail(webhook 会话工具能力)
    pt = cfg.get("platform_toolsets") or {}
    wh_tools = pt.get("webhook") or []
    if not isinstance(wh_tools, list):
        wh_tools = []
    if "agentmail" not in wh_tools:
        if not wh_tools:
            wh_tools = list(WEBHOOK_TOOLSET)
        else:
            wh_tools.append("agentmail")
        pt["webhook"] = wh_tools
        cfg["platform_toolsets"] = pt
        changes.append(f"platform_toolsets.webhook -> {wh_tools}")
        dirty = True

    # 3) platform_toolsets.cli 含 agentmail(用户定调 cli 也要加)
    cli_tools = pt.get("cli") or []
    if not isinstance(cli_tools, list):
        cli_tools = []
    if "agentmail" not in cli_tools:
        cli_tools.append("agentmail")
        pt["cli"] = cli_tools
        cfg["platform_toolsets"] = pt
        changes.append(f"platform_toolsets.cli -> {cli_tools}")
        dirty = True

    if dirty:
        _dump_yaml(cfg_path, cfg)
    return changes


def is_amail_profile(profile_dir: Path) -> bool:
    """判断 profile 是否 amail 相关:有 .agentmail 指针,或已有
    amail 配置痕迹(platform_toolsets 含 agentmail / platforms.webhook
    有 secret)。无关 profile(erp/qlbio 等)绝不写入——避免污染非
    amail 目录(2026-08-16 实测污染后清理)。"""
    if (profile_dir / ".agentmail").is_file():
        return True
    cfg = _load_yaml(profile_dir / "config.yaml")
    pt = cfg.get("platform_toolsets") or {}
    for seg in ("webhook", "cli"):
        tools = pt.get(seg) or []
        if "agentmail" in (tools if isinstance(tools, list) else []):
            return True
    wh = (cfg.get("platforms") or {}).get("webhook") or {}
    if wh.get("extra", {}).get("secret"):
        return True
    return False
