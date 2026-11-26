"""P0: DID 协议常量与向量镜像的对齐检查。

这一组测试不做密码学、也不碰邮件链路——它只保证三件事：

1. ``contract/did-signature-vectors.json`` 是**可解析的、结构完整的**镜像；
2. ``pysdk/aimail_did.py`` 的常量与向量文件里 ``constants`` 完全一致
   （三语言共用的字符串不允许各写一份、慢慢漂移）；
3. 若同机存在 ``aimail-gateway`` 仓，镜像必须与它的
   ``docs/vectors/did-signature.json`` **逐字节相同**。

字节层实现（`Content-v1` / 头 / Ed25519）由 did-sdk v0.3.0 提供，在 P4 接入；
收件侧裁决在 P6 接入。所以本文件刻意**不**断言投影字节。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import aimail_did

_REPO = Path(__file__).resolve().parents[2]
_MIRROR = _REPO / "contract" / "did-signature-vectors.json"
_GATEWAY_VECTORS = (
    _REPO.parent / "aimail-gateway" / "protocol" / "vectors" / "did-signature.json"
)

#: 镜像必须包含的全部分组（少一个就说明生成/拷贝被截断）。
_REQUIRED_GROUPS = (
    "constants",
    "content_v1",
    "rcpt_hash",
    "signing_input",
    "header",
    "payload_hash",
    "domain_control",
    "auth_results",
    "verdict",
    "payload",
)


def _vectors() -> dict:
    assert _MIRROR.is_file(), f"missing vector mirror: {_MIRROR}"
    return json.loads(_MIRROR.read_text(encoding="utf-8"))


def test_mirror_has_every_group_and_they_are_non_empty():
    data = _vectors()
    for group in _REQUIRED_GROUPS:
        assert group in data, f"vector group `{group}` missing"
        if group != "constants":
            assert isinstance(data[group], list) and data[group], (
                f"vector group `{group}` must be a non-empty list"
            )


def test_constants_match_the_module():
    constants = _vectors()["constants"]

    assert constants["header_name"] == aimail_did.HEADER_NAME
    assert constants["results_header_name"] == aimail_did.RESULTS_HEADER_NAME
    assert constants["system_mail_header"] == aimail_did.SYSTEM_MAIL_HEADER
    assert constants["protocol_version"] == aimail_did.PROTOCOL_VERSION
    assert constants["signing_tag"] == aimail_did.SIGNING_TAG
    assert constants["content_tag"] == aimail_did.CONTENT_TAG
    assert constants["email_binding_tag"] == aimail_did.EMAIL_BINDING_TAG
    assert constants["domain_control_tag"] == aimail_did.DOMAIN_CONTROL_TAG
    assert constants["body_hash_prefix"] == aimail_did.BODY_HASH_PREFIX
    assert constants["anchor_hash_prefix"] == aimail_did.ANCHOR_HASH_PREFIX
    assert constants["anchor_ref_prefix"] == aimail_did.ANCHOR_REF_PREFIX
    assert constants["attachment_separator"] == aimail_did.ATTACHMENT_SEPARATOR


def test_error_vocabulary_matches_the_module():
    """reason/错误码必须先钉死：三端都按这些字符串裁决，改一个就全乱。"""
    assert list(aimail_did.ERRORS) == _vectors()["constants"]["errors"]


def test_primitives_are_split_by_layer():
    """层边界（协议 §2）：邮件内 sha256、链上 blake2b —— 不允许在同层混用。"""
    constants = _vectors()["constants"]
    assert constants["body_hash_prefix"] == "sha256:"
    assert constants["anchor_hash_prefix"] == "blake2b:"

    for case in _vectors()["content_v1"]:
        assert case["body_hash"].startswith("sha256:")
    for case in _vectors()["payload_hash"]:
        assert case["hex"].startswith("blake2b:")
    for case in _vectors()["domain_control"]:
        assert case["hex"].startswith("blake2b:")


def test_header_echoes_come_from_the_same_vector_cases():
    """头的 `bh`/`rh` 必须与同源向量相等（不许二次计算，协议 §5.1）。"""
    data = _vectors()
    header = data["header"][0]["fields"]

    rh_cases = {case["case"]: case["hex"] for case in data["rcpt_hash"]}
    assert header["rh"] == rh_cases["header-main-set"]

    body_hash = data["content_v1"][0]["body_hash"]
    assert header["bh"] == body_hash

    # 签名对象七行、末行带换行（N1）
    raw = bytes.fromhex(data["signing_input"][0]["bytes_hex"])
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 7


def test_load_payload_carries_no_hash():
    """N9：端到端载荷里不得出现任何 hash —— 否则等于让服务商自己出题。"""
    case = _vectors()["payload"][0]
    keys = set(case["expect_did_keys"])
    forbidden = set(case["expect_absent_keys"])

    assert "body_signed" in keys
    assert "attachments" in keys
    for bad in forbidden:
        assert bad not in keys, f"payload must not advertise `{bad}`"


@pytest.mark.skipif(
    not _GATEWAY_VECTORS.is_file(),
    reason="sibling aimail-gateway repo not present (CI compares them separately)",
)
def test_mirror_is_byte_identical_to_the_gateway_canonical_file():
    assert _MIRROR.read_bytes() == _GATEWAY_VECTORS.read_bytes(), (
        "contract/did-signature-vectors.json must be a byte-identical mirror of "
        "aimail-gateway/protocol/vectors/did-signature.json; regenerate with "
        "`python3 protocol/vectors/gen_did_vectors.py` in the gateway repo and copy again"
    )


def test_module_is_stdlib_only():
    """pysdk 的 `dependencies = []` 是硬约束：本模块不得拉第三方包。"""
    source = (_REPO / "pysdk" / "aimail_did.py").read_text(encoding="utf-8")
    for banned in ("import cryptography", "import nacl", "import did_sdk", "from did_sdk"):
        assert banned not in source, f"pysdk/aimail_did.py must not depend on `{banned}`"
