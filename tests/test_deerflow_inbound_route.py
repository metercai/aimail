"""deerflow 入站路由: 实际挂载路径必须 == 契约路径(可投递性基线)。

2026-09-27 实机缺陷(产品侧, 已修): `pysdk/deer-flow/aimail_inbound.py` 曾把契约路径
(`aimail_contract.INBOUND_PATH`)用 `rpartition("/")` 拆成 `APIRouter(prefix=前缀)` +
`@router.post(叶子)`。FastAPI 的挂载是**纯字符串拼接**
(`fastapi/routing.py`: `self.prefix + path`) ⇒ 实际挂载 `/aimailinbound` ——
契约里那个 `/` 丢了。而注册链(`pysdk/deer-flow/manage.py`:
`local_webhook_url = inbound_base.rstrip("/") + _contract.INBOUND_PATH`)/绑定/桥
全部按**契约路径**投递 ⇒ `POST <契约路径>` 404, 入站邮件永远到不了适配器
(不止门禁, 生产同链路)。

本文件两层断言(都可证伪: 把修复回退成 prefix + 无前导斜杠叶子, 两层都必红):

  ① 静态层(AST 重建, **零第三方依赖**, CI 无 fastapi 也跑): 从模块源码按
     FastAPI 的拼接规则 `prefix + 声明路径` 重建实际挂载路径 —— 它能忠实重建
     缺陷形状(`A, _, B = PATH.rpartition("/")`), 因此"必须含契约路径、必不含
     拼接伪影 `/aimailinbound`"是真断言而非空转;
  ② 运行时层(装了 fastapi 时): 真 Router 的 `route.path` 与真 FastAPI app 的
     openapi `paths` 必须逐字等于契约路径 —— 与宿主
     `app.include_router(aimail_inbound.router)`(manage.py:562)逐字同构。
"""
import ast
import os
import sys

import aimail_contract as contract

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE = os.path.join(_REPO, "pysdk", "deer-flow", "aimail_inbound.py")

# 契约路径去掉最后一个 `/` 之后的"拼接伪影"(= FastAPI 的 prefix+叶子 拼接结果):
# <契约路径> -> <前缀><叶子>。字面量不在此处重复(契约常量唯一真源)。
_SLASH = contract.INBOUND_PATH.rfind("/")
ARTIFACT = contract.INBOUND_PATH[:_SLASH] + contract.INBOUND_PATH[_SLASH + 1:]


def _mounted_paths_from_source(module_path: str | None = None) -> list[str]:
    """按 FastAPI 的拼接规则(self.prefix + path)重建模块声明的挂载路径。

    只解析本次用到的形状: 模块级简单赋值、`A, _, B = <expr>.rpartition(sep)`
    解包、`_contract.<NAME>` 属性、字符串字面量, 以及 `router.<method>(<path>)`
    装饰器。解析不出来的表达式**显式报错**(不静默跳过 —— 静默 = 假绿)。
    """
    module_path = module_path or _MODULE
    with open(module_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=module_path)

    # name -> ast 表达式 | ("rpartition", <整体表达式>, <分隔符>, "head"|"tail")
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
            # `a, b, c = expr` 在 AST 里是**单目标 Tuple**, 不是三个目标
            if isinstance(only, ast.Tuple):
                targets = only.elts
        # 缺陷形状: 三段解包自 .rpartition(sep)(修复前的 aimail_inbound.py:67)
        if (len(targets) == 3 and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute) and value.func.attr == "rpartition"
                and value.args and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)):
            for tgt, kind in zip(targets, ("head", "mid", "tail")):
                if isinstance(tgt, ast.Name):
                    assigns[tgt.id] = ("rpartition", value.func.value, value.args[0].value, kind)

    def resolve(node: object) -> str:
        if isinstance(node, str):
            raise AssertionError(f"静态层内部标记错位: {node}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
            raise AssertionError(f"路径表达式不是字符串: {ast.dump(node)[:100]}")
        if isinstance(node, ast.Name):
            assert node.id in assigns, f"{module_path} 里 {node.id} 不是静态层可解析的模块级赋值"
            return resolve(assigns[node.id])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return resolve(node.left) + resolve(node.right)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "_contract":
            return getattr(contract, node.attr)
        if isinstance(node, tuple) and len(node) == 4 and node[0] == "rpartition":
            _, whole, sep, kind = node
            head, _, tail = resolve(whole).rpartition(sep)
            return head if kind == "head" else (tail if kind == "tail" else "")
        raise AssertionError(f"静态层无法解析路径表达式: {str(node)[:140]}")

    # APIRouter(prefix=…) —— 缺失 / 空 ⇒ FastAPI 视为 ""
    prefix = ""
    router_seen = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "APIRouter":
            router_seen = True
            for kw in node.keywords:
                if kw.arg == "prefix" and kw.value is not None:
                    prefix = resolve(kw.value)
    assert router_seen, f"{module_path} 里没有 APIRouter(...) —— 静态层形状假设失效, 必须复核"

    paths = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "router"
                and node.args):
            paths.append(prefix + resolve(node.args[0]))  # fastapi: self.prefix + path
    return paths


# ── ① 静态层(零第三方依赖)─────────────────────────────────────────

def test_static_layer_reconstructs_the_fastapi_join_rule(tmp_path):
    """静态层本身不是空转: 把缺陷形状喂进去, 它必须算出拼接伪影。

    这是主断言的可证伪性证明 —— 修复回退成 `prefix + 无前导斜杠叶子` 时, 静态层
    算出的正是 ARTIFACT(而不是契约路径), 于是 `test_source_...` 必红。
    """
    assert ARTIFACT != contract.INBOUND_PATH, "伪影 == 契约路径 ⇒ 本文件的判定式退化了, 必须复核"
    probe = tmp_path / "neg_probe.py"
    probe.write_text(
        # 负例探针的输入必须保持"字面量形态"(用常量构造不出拼接伪影 ⇒ 静态层断言空转),
        # 故这一行是本文件唯一保留契约字面量的地方, 按门禁规则行内登记理由:
        "INBOUND_PATH = '/aimail/inbound'\n"  # contract-allowed: 负例探针输入需保持字面量形态
        "from fastapi import APIRouter\n"
        "_P, _, _R = INBOUND_PATH.rpartition('/')\n"
        "router = APIRouter(prefix=_P)\n"
        "@router.post(_R)\n"
        "async def h(): ...\n",
        encoding="utf-8",
    )
    got = _mounted_paths_from_source(str(probe))
    assert got == [ARTIFACT], (
        f"静态层没能重建 FastAPI 的拼接伪影(实测 {got})⇒ 断言空转, 必须修静态层")


def test_source_declares_mounted_path_equal_to_contract():
    paths = _mounted_paths_from_source()
    assert paths, "模块没声明任何 router 路由 —— 断言会空转, 必须复核"
    assert ARTIFACT not in paths, (
        f"入站路由挂载成拼接伪影 {ARTIFACT!r}(契约里那个 '/' 丢了): {paths}"
    )
    assert paths == [contract.INBOUND_PATH], (
        f"入站路由实际挂载路径 {paths} != [{contract.INBOUND_PATH}]; "
        "契约路径是注册链/绑定/桥的唯一投递目标(manage.py 用 INBOUND_PATH 拼 webhook_url)"
    )


# ── ② 运行时层(真 FastAPI)─────────────────────────────────────────

def test_router_and_openapi_mount_the_contract_path():
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as e:  # pragma: no cover - 仅无 fastapi 的环境
        import pytest
        pytest.skip(f"fastapi 未安装(可选 extra deerflow)⇒ 运行时层跳过; 静态层已锁同一契约: {e}")

    df_dir = os.path.join(_REPO, "pysdk", "deer-flow")
    if df_dir not in sys.path:
        sys.path.insert(0, df_dir)
    import aimail_inbound  # noqa: E402

    assert aimail_inbound._INBOUND_PATH == contract.INBOUND_PATH, (
        "适配层挂载用的常量不是契约常量: "
        f"{aimail_inbound._INBOUND_PATH!r} != {contract.INBOUND_PATH!r}"
    )
    mounted = [r.path for r in aimail_inbound.router.routes]
    assert mounted == [contract.INBOUND_PATH], f"router 实际路径 {mounted} != [{contract.INBOUND_PATH}]"

    app = FastAPI()
    app.include_router(aimail_inbound.router)  # 与 manage.py:562 的宿主 patch 逐字同构
    paths = list(app.openapi()["paths"])
    assert paths == [contract.INBOUND_PATH], (
        f"真 FastAPI 应用的 openapi paths = {paths}; 期望恰好 [{contract.INBOUND_PATH}] "
        f"(拼接伪影 {ARTIFACT!r} 也不许出现)"
    )
