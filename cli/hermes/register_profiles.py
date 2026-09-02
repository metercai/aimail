#!/usr/bin/env python3
"""Register existing Hermes profiles as amail addresses in the current system."""
import sys, os, json
from pathlib import Path
# 运行时核心(repo pysdk/ 优先 > pip aimail 兜底)+ cli/(gateway_api)
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from runtime_core import load_core, load_adapter  # noqa: E402
load_core()
load_adapter("hermes")

def load_gateway_config():
    # Use SYSTEM_ID env var to locate config directly
    sid = os.environ.get("SYSTEM_ID", "")
    if sid:
        sub = os.path.join(os.path.expanduser("~/.agentmail/systems"), sid, "agentmail_gateway.json")
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

    from aimail_hermes import _auto_register_email
    # 配置补全(幂等):platform_toolsets.webhook/cli 加 agentmail +
    # platforms.webhook enabled。断链根因曾多次出现:安装链从未写这些
    # 配置,全靠手工补——缺 webhook 段 → webhook 会话无 send_mail
    # ("收得到回不出");缺 cli 段 → CLI 会话无邮件工具。路由
    # (agentmail-inbound)由 _auto_register_email → _ensure_webhook_route
    # 创建,**不需要第二个 amail-inbound**(bridge 全 URL 路由直接指
    # /webhooks/agentmail-inbound)。
    import importlib.util
    _ensure_spec = importlib.util.spec_from_file_location(
        "ensure_webhook_config",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "ensure_webhook_config.py"))
    _ensure_mod = importlib.util.module_from_spec(_ensure_spec)
    _ensure_spec.loader.exec_module(_ensure_mod)

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
            _ensure_mod.ensure_profile_config(Path(home))
            _auto_register_email("default", home, config)
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
                _ensure_mod.ensure_profile_config(Path(profile_dir))
                _auto_register_email(name, profile_dir, config)
                count += 1
            except Exception as e:
                print(f"failed:{name}:{e}")

    print(f"registered:{count}")

if __name__ == "__main__":
    register_emails()
