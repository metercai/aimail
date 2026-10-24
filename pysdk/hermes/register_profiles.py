"""Register existing Hermes profiles as AIMail addresses in the current system.

Library form of the Hermes platform install chain entry (migrated from cli/hermes/register_profiles.py): by the new
The installation entry imports this module and then calls register_emails(). No longer depends on cli/runtime_core's
load_core()/load_adapter('hermes')——top-level dual-form bootstrap + sibling module import in the same directory
|(aimail.hermes package import takes priority, falling back to bare import in this directory) replaces the old _SCRIPTS_DIR hack and
importlib.util dynamic loading of ensure_webhook_config.
"""

import sys, os, json
from pathlib import Path

# ── Dual-form bootstrap (repo pysdk/ ↔ pip site-packages/aimail/) ──
# repo form: dirname(dirname(__file__)) = pysdk/(containing flat core like aimail_base.py,
# bare import available) → add to sys.path. pip form: that directory = site-packages/ (no flat core),
# must first import aimail to trigger aimail/__init__.py glue (inserts aimail/ directory into sys.path,
# making flat core bare import available).
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_CORE_DIR, "aimail_base.py")):
    if _CORE_DIR not in sys.path:
        sys.path.insert(0, _CORE_DIR)
else:
    try:
        import aimail  # noqa: F401  (pip form glue)
    except Exception:
        pass

# Unified import of same-directory sibling modules: aimail.hermes package import preferred (pip form / repo with
# aimail installed), on failure insert this directory (hermes/) into sys.path then bare import (repo namespace form).
try:
    from aimail.hermes import aimail_hermes, ensure_config  # noqa: E402,F401
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import aimail_hermes  # noqa: E402,F401
    import ensure_config  # noqa: E402,F401

def load_gateway_config():
    # Direct location via AIMAIL_SYSTEM_ID env (via shared gateway_config_path——previously manually concatenated
    # aimail_home()/{sid}/aimail_gateway.json missing systems/ segment + used wrong
    # SYSTEM_ID env, always None → installation chain registration silently no-ops; AUDIT-1 P1-3).
    sid = os.environ.get("AIMAIL_SYSTEM_ID", "") or os.environ.get("SYSTEM_ID", "")
    if sid:
        try:
            from gateway_api import gateway_config_path
            p = gateway_config_path(sid)
            if os.path.isfile(p):
                with open(p) as f:
                    return json.load(f)
        except Exception:
            pass
    return None
def register_emails():
    config = load_gateway_config()
    if not config or not config.get("admin_key"):
        print("no_config")
        return

    # Config completion (idempotent): add agentmail (internal identifier) to platform_toolsets.webhook/cli +
    # platforms.webhook enabled. Root cause of broken chain has occurred multiple times: installation chain never wrote these
    # configs, relying entirely on manual completion——missing webhook section → webhook session has no send_mail
    # ("can receive but not send"); missing cli section → CLI session has no email tools. Routing
    # (aimail-inbound) is created by _auto_register_email → _ensure_webhook_route
    # created, **no second inbound route needed** (bridge full URL routing directly points
    # /webhooks/aimail-inbound)。

    system_id = config.get("system_id", "")
    home = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
    profiles_dir = os.path.expanduser(os.environ.get("HERMES_PROFILES_DIR", os.path.join(home, "profiles")))

    count = 0
    failures: list = []

    def _fail(msg: str) -> None:
        """Record + print; register_emails() then reports a non-zero exit code.

        C-13/C-5 class (2026-10-11): a swallowed failure used to leave the
        install step reporting success while nothing had been wired (webhook /
        toolsets missing ⇒ agent receives mail but cannot reply).
        """
        failures.append(msg)
        print(msg)

    # ── Decouple config completion from registration marker (2026-09-30 J4e root cause) ─────────────────────
    # Old logic placed ensure_profile_config after ".agentmail pointer matched ⇒ skip":  contract-allowed: comment text (non-code) references contract pointer filename
    # When pointer already exists (same system_id), the entire block is skipped, install self-reports registered:0, thus
    # platform_toolsets.webhook/cli is never completed ⇒ webhook session falls back to default toolset,
    # agent writes reply but cannot send it (iso16 log line 86 registered:0 → J5 reports toolset
    # MISSING → J4e reply=0). ensure itself is idempotent (only fills missing items, secret untouched), therefore
    # **executed unconditionally**; registration still skips idempotently by pointer (registered count semantics unchanged).
    # Five platforms go through the same install/ensure code, no platform-specific checks added.
    try:
        ensure_config.ensure_profile_config(Path(home))
    except Exception as e:
        _fail(f"failed:ensure:default:{e}")

    # Default profile (root ~/.hermes/)
    # Use .agentmail pointer as registration marker
    default_pointer = os.path.join(home, ".agentmail")
    _reregister_default = True
    if os.path.isfile(default_pointer):
        try:
            pd = json.load(open(default_pointer))
            if pd.get("system_id") == system_id:
                _reregister_default = False  # same system, skip
            else:
                print("  Re-registering default (system changed)", file=sys.stderr)
        except Exception:
            pass
    else:
        print("  No default pointer — registering default profile", file=sys.stderr)
    if _reregister_default:
        try:
            os.environ["HERMES_PROFILE_DIR"] = home
            aimail_hermes._auto_register_email("default", home, config)
            count += 1
        except Exception as e:
            _fail(f"failed:default:{e}")
        finally:
            os.environ.pop("HERMES_PROFILE_DIR", None)

    # Named profiles
    if os.path.isdir(profiles_dir):
        for name in sorted(os.listdir(profiles_dir)):
            profile_dir = os.path.join(profiles_dir, name)
            if not os.path.isdir(profile_dir):
                continue
            # Config completion is also decoupled from the pointer (see above): the pointer only means "registered", not
            # "config complete" —— idempotent ensure runs every time.
            try:
                ensure_config.ensure_profile_config(Path(profile_dir))
            except Exception as e:
                _fail(f"failed:ensure:{name}:{e}")
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
                # webhook reuse/route creation both resolve "current profile"——batch loop must
                # point HERMES_PROFILE_DIR to this profile, otherwise _load_profile_config
                # falls back to default config (webhook chaining + route creation missed)
                os.environ["HERMES_PROFILE_DIR"] = profile_dir
                aimail_hermes._auto_register_email(name, profile_dir, config)
                count += 1
            except Exception as e:
                _fail(f"failed:{name}:{e}")
            finally:
                os.environ.pop("HERMES_PROFILE_DIR", None)

    print(f"registered:{count}")
    if failures:
        # Failure must change exit code (otherwise registry step will report ok_text success).
        print(f"failed-total:{len(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(register_emails() or 0)
