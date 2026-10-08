"""族3 (owner ruling 2026-10-02 → 0.1.34): dsh/pi register templates carry the
{name} token.

F13 root-fix pin (cli-in-host) judges "name-aware registrar" by the template
JSON containing {email}/{name} (or a python_module kind). Before this ruling
dsh/pi were node_entry templates without any name token → the register chain
registered the platform-default name while the plan recorded the target
(two-sided inconsistency: gateway default vs local target pointer). 0.1.34
adds register-cli --name and the template token, so:

  1. plan_address_name sees a name-aware registrar → direct registration
     (needs_rename=False, reg_as == target);
  2. the executor passes the planned base through --name {name};
  3. the package's resolveRegisterEmail derives the identical address
     (empty default-alias set == Python plan_address_name derivation).
"""
import json
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[2]
_REG = json.loads((_REPO / "cli" / "platforms.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(_REPO / "pysdk"))

from aimail_base import plan_address_name  # noqa: E402  (SDK single source)

_PLATFORMS = ("dsh", "pi")


def test_dsh_pi_register_templates_carry_name_token():
    for plat in _PLATFORMS:
        r = _REG["platforms"][plat]["register"]
        assert r["kind"] == "node_entry", plat
        assert "{name}" in json.dumps(r), (
            f"{plat} register template lost the {{name}} token — F13 pin would "
            "revert to the platform-default registration"
        )


def test_dsh_pi_custom_name_registers_directly_no_rename():
    for plat in _PLATFORMS:
        pdef = _REG["platforms"][plat]
        r = pdef["register"]
        argv = (r.get("argv") or []) + (r.get("args") or [])
        plan = plan_address_name(
            "foo",
            agent_id="agent",
            domain="x.example",
            system_name="",
            aliases=pdef.get("aliases", []),
            register_argv=argv,
        )
        assert plan["needs_rename"] is False, plat
        assert plan["reg_as"] == "foo", plan
        assert plan["email"] == plan["target_email"] == "foo@x.example", plan


def test_dsh_pi_default_flow_unchanged_direct():
    # reset/install register the default name: direct + legacy address preserved
    for plat in _PLATFORMS:
        pdef = _REG["platforms"][plat]
        r = pdef["register"]
        argv = (r.get("argv") or []) + (r.get("args") or [])
        plan = plan_address_name(
            "agent",
            agent_id="agent",
            domain="x.example",
            system_name="",
            aliases=pdef.get("aliases", []),
            register_argv=argv,
        )
        assert plan["needs_rename"] is False, plat
        assert plan["reg_as"] == plan["target_name"] == "agent", plan
        assert plan["email"] == plan["target_email"], plan
