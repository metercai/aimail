"""deerflow inbound routing: actual mount path must == contract path (deliverability baseline).

2026-09-27 real-world defect (product side, fixed): `pysdk/deer-flow/aimail_inbound.py` used to split the contract path
(`aimail_contract.INBOUND_PATH`) using `rpartition("/")` into `APIRouter(prefix=prefix)` +
`@router.post(leaf)`. FastAPI mounting is **pure string concatenation**
(`fastapi/routing.py`: `self.prefix + path`) ⇒ actual mount `/aimailinbound` ——
the `/` in the contract was lost. And the registration chain (`pysdk/deer-flow/manage.py`:
`local_webhook_url = inbound_base.rstrip("/") + _contract.INBOUND_PATH`)/binding/bridge
all deliver by **contract path** ⇒ `POST <contract path>` 404, inbound emails never reach the adapter
(not just the gate, production uses the same chain).

This file has two layers of assertions (both falsifiable: revert the fix to prefix + leaf without leading slash, both layers must fail):

  ① Static layer (AST reconstruction, **zero third-party dependencies**, runs in CI without fastapi): reconstructs the actual mount path from module source code according to
     FastAPI's concatenation rule `prefix + declared path` —— it can faithfully reconstruct
     the defect shape (`A, _, B = PATH.rpartition("/")`), therefore "must contain contract path, must not contain
     concatenation artifact `/aimailinbound`" is a real assertion, not a no-op;
  ② Runtime layer (when fastapi is installed): the real Router's `route.path` and the real FastAPI app's
     openapi `paths` must be character-for-character equal to the contract path —— isomorphic to the host's
     `app.include_router(aimail_inbound.router)` (manage.py:562).
"""
import ast
import os
import sys

import aimail_contract as contract

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODULE = os.path.join(_REPO, "pysdk", "deer-flow", "aimail_inbound.py")

 # the "concatenation artifact" after stripping the last `/` from the contract path (= the FastAPI prefix+leaf concatenation result):
# <contract path> -> <prefix><leaf>. The literal is not repeated here (contract constant is the single source of truth).
_SLASH = contract.INBOUND_PATH.rfind("/")
ARTIFACT = contract.INBOUND_PATH[:_SLASH] + contract.INBOUND_PATH[_SLASH + 1:]


def _mounted_paths_from_source(module_path: str | None = None) -> list[str]:
    """Reconstruct the mount path declared by the module according to FastAPI's concatenation rule (self.prefix + path).

    Only parses the shapes used here: module-level simple assignment, `A, _, B = <expr>.rpartition(sep)`
    unpacking, `_contract.<NAME>` attribute, string literals, and `router.<method>(<path>)`
    decorators. Expressions that cannot be parsed **explicitly raise an error** (do not silently skip —— silent = false green).
    """
    module_path = module_path or _MODULE
    with open(module_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=module_path)

    # name -> ast expression | ("rpartition", <whole expression>, <separator>, "head"|"tail")
    assigns: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assigns[node.target.id] = node.value
            continue
        if not isinstance(node, ast.Assign):
            continue
        value, targets = node.value, node.targets
        if len(targets) == 1:
            only = targets[0]
            if isinstance(only, ast.Name):
                assigns[only.id] = value
                continue
            # `a, b, c = expr` is a **single-target Tuple** in the AST, not three targets
            if isinstance(only, ast.Tuple):
                targets = only.elts
        # Defect shape: three-way unpacking from .rpartition(sep) (pre-fix aimail_inbound.py:67)
        if (len(targets) == 3 and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute) and value.func.attr == "rpartition"
                and value.args and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)):
            for tgt, kind in zip(targets, ("head", "mid", "tail")):
                if isinstance(tgt, ast.Name):
                    assigns[tgt.id] = ("rpartition", value.func.value, value.args[0].value, kind)

    def resolve(node: object) -> str:
        if isinstance(node, str):
            raise AssertionError(f"Static layer internal marker misalignment: {node}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
            raise AssertionError(f"Path expression is not a string: {ast.dump(node)[:100]}")
        if isinstance(node, ast.Name):
            assert node.id in assigns, f"{module_path} has no statically resolvable module-level assignment for {node.id}"
            return resolve(assigns[node.id])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return resolve(node.left) + resolve(node.right)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "_contract":
            return getattr(contract, node.attr)
        if isinstance(node, tuple) and len(node) == 4 and node[0] == "rpartition":
            _, whole, sep, kind = node
            head, _, tail = resolve(whole).rpartition(sep)
            return head if kind == "head" else (tail if kind == "tail" else "")
        raise AssertionError(f"Static layer cannot parse path expression: {str(node)[:140]}")

    # APIRouter(prefix=…) —— missing / empty ⇒ FastAPI treats as ""
    prefix = ""
    router_seen = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "APIRouter":
            router_seen = True
            for kw in node.keywords:
                if kw.arg == "prefix" and kw.value is not None:
                    prefix = resolve(kw.value)
    assert router_seen, f"{module_path} does not contain APIRouter(...) — static layer shape assumption failed, must recheck"

    paths = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "router"
                and node.args):
            paths.append(prefix + resolve(node.args[0]))  # fastapi: self.prefix + path
    return paths


# ── ① Static layer (zero third-party dependencies)─────────────────────────────────────────

def test_static_layer_reconstructs_the_fastapi_join_rule(tmp_path):
    """The static layer itself is not a no-op: feed it the defect shape, it must calculate the concatenation artifact.

    This is the proof of falsifiability for the main assertion —— when the fix is reverted to `prefix + leaf without leading slash`, the static layer
    calculates exactly ARTIFACT (not the contract path), so `test_source_...` must fail.
    """
    assert ARTIFACT != contract.INBOUND_PATH, "artifact == contract path ⇒ the predicate in this file has degenerated, must re-verify"
    probe = tmp_path / "neg_probe.py"
    probe.write_text(
        # The input for the negative case probe must remain in "literal form" (concatenation artifacts cannot be constructed with constants ⇒ static layer assertions spin idle),
        # so this line is the only place in this file that retains the contract literal, registering the reason inline per gate rules:
        "INBOUND_PATH = '/aimail/inbound'\n"  # contract-allowed: negative case probe input must remain in literal form
        "from fastapi import APIRouter\n"
        "_P, _, _R = INBOUND_PATH.rpartition('/')\n"
        "router = APIRouter(prefix=_P)\n"
        "@router.post(_R)\n"
        "async def h(): ...\n",
        encoding="utf-8",
    )
    got = _mounted_paths_from_source(str(probe))
    assert got == [ARTIFACT], (
        f"Static layer failed to reconstruct FastAPI's concatenation artifact (measured {got}) ⇒ assertion is a no-op, must fix static layer")


def test_source_declares_mounted_path_equal_to_contract():
    paths = _mounted_paths_from_source()
    assert paths, "Module did not declare any router routes — assertion will be a no-op, must recheck"
    assert ARTIFACT not in paths, (
        f"Inbound route mounted as concatenation artifact {ARTIFACT!r} (the '/' in the contract is missing): {paths}"
    )
    assert paths == [contract.INBOUND_PATH], (
        f"Inbound route actual mount path {paths} != [{contract.INBOUND_PATH}]; "
        "Contract path is the sole delivery target for registration chain/binding/bridge (manage.py uses INBOUND_PATH to build webhook_url)"
    )


# ── ② Runtime layer (real FastAPI) ─────────────────────────────────────────

def test_router_and_openapi_mount_the_contract_path():
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as e:  # pragma: no cover - environments without fastapi only
        import pytest
        pytest.skip(f"fastapi not installed (optional extra deerflow) ⇒ runtime layer skipped; static layer has locked the same contract: {e}")

    df_dir = os.path.join(_REPO, "pysdk", "deer-flow")
    if df_dir not in sys.path:
        sys.path.insert(0, df_dir)
    import aimail_inbound  # noqa: E402

    assert aimail_inbound._INBOUND_PATH == contract.INBOUND_PATH, (
        "Constant used for adapter layer mounting is not the contract constant: "
        f"{aimail_inbound._INBOUND_PATH!r} != {contract.INBOUND_PATH!r}"
    )
    mounted = [r.path for r in aimail_inbound.router.routes]
    assert mounted == [contract.INBOUND_PATH], f"router actual path {mounted} != [{contract.INBOUND_PATH}]"

    app = FastAPI()
    app.include_router(aimail_inbound.router)  # byte-identical in structure to the host patch at manage.py:562
    paths = list(app.openapi()["paths"])
    assert paths == [contract.INBOUND_PATH], (
        f"Real FastAPI app's openapi paths = {paths}; expected exactly [{contract.INBOUND_PATH}] "
        f"(concatenation artifact {ARTIFACT!r} is also not allowed to appear)"
    )
