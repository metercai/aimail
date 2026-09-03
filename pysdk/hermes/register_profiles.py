"""Register existing Hermes profiles as amail addresses in the current system.

Hermes 平台安装链入口的库形态(cli/hermes/register_profiles.py 迁移):由新的
安装入口 import 本模块后调用 register_emails()。不再依赖 cli/runtime_core 的
load_core()/load_adapter('hermes')——顶部双形态自举 + 同目录兄弟模块导入
(aimail.hermes 包导入优先,回退本目录裸 import)取代旧 _SCRIPTS_DIR hack 与
importlib.util 动态加载 ensure_webhook_config。
"""

import sys, os, json
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

# 同目录兄弟模块统一导入: aimail.hermes 包导入优先(pip 形态 / repo 已装
# aimail),失败则把本目录(hermes/)插 sys.path 后裸 import(repo namespace 形态)。
try:
    from aimail.hermes import aimail_hermes, ensure_config  # noqa: E402,F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import aimail_hermes  # noqa: E402,F401
    import ensure_config  # noqa: E402,F401

def load_gateway_config():
    # Use SYSTEM_ID env var to locate config directly (home via
    # aimail_base.aimail_home() — AIMAIL_HOME aware)
    import aimail_base  # noqa: E402  (自举已把 core 目录加 sys.path)
    sid = os.environ.get("SYSTEM_ID", "")
    if sid:
        sub = os.path.join(aimail_base.aimail_home(), sid, "agentmail_gateway.json")
        if os.path.isfile(sub):
            try:
                with open(sub) as f:
                    return json.load(f)
            except Exception:
                pass
    return None
def register_emails():
    config = load_gateway_config()
    if not config or not config.get("admin_key"):
        print("no_config")
        return

    # 配置补全(幂等):platform_toolsets.webhook/cli 加 aimail +
    # platforms.webhook enabled。断链根因曾多次出现:安装链从未写这些
    # 配置,全靠手工补——缺 webhook 段 → webhook 会话无 send_mail
    # ("收得到回不出");缺 cli 段 → CLI 会话无邮件工具。路由
    # (aimail-inbound)由 _auto_register_email → _ensure_webhook_route
    # 创建,**不需要第二个 amail-inbound**(bridge 全 URL 路由直接指
    # /webhooks/aimail-inbound)。

    system_id = config.get("system_id", "")
    home = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
    profiles_dir = os.path.expanduser(os.environ.get("HERMES_PROFILES_DIR", os.path.join(home, "profiles")))

    count = 0

    # Default profile (root ~/.hermes/)
    # Use .agentmail pointer as registration marker
    default_pointer = os.path.join(home, ".agentmail")
    if os.path.isfile(default_pointer):
        try:
            pd = json.load(open(default_pointer))
            if pd.get("system_id") == system_id:
                pass  # already registered, skip
        except:
            pass
    else:
        # Register default profile
        try:
            ensure_config.ensure_profile_config(Path(home))
            aimail_hermes._auto_register_email("default", home, config)
            count += 1
        except Exception as e:
            print(f"failed:default:{e}")

    # Named profiles
    if os.path.isdir(profiles_dir):
        for name in sorted(os.listdir(profiles_dir)):
            profile_dir = os.path.join(profiles_dir, name)
            if not os.path.isdir(profile_dir):
                continue
            # Use .agentmail pointer as registration marker
            named_pointer = os.path.join(profile_dir, ".agentmail")
            if os.path.isfile(named_pointer):
                try:
                    pd = json.load(open(named_pointer))
                    if pd.get("system_id") == system_id:
                        continue  # same system, skip
                    print(f"  Re-registering {name} (system changed)", file=sys.stderr)
                except:
                    continue
            try:
                ensure_config.ensure_profile_config(Path(profile_dir))
                aimail_hermes._auto_register_email(name, profile_dir, config)
                count += 1
            except Exception as e:
                print(f"failed:{name}:{e}")

    print(f"registered:{count}")

if __name__ == "__main__":
    register_emails()
