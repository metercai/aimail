"""aimail_did — DID-Signature / DID-Auth-Results 的协议常量（P0 骨架）。

本模块在当前阶段**只提供常量与错误标识**，不实现任何字节布局。这是有意的：

* **邮件层**（`Content-v1` 投影、`DID-Signature` 头编解码、Ed25519 签名/验签）
  由 `did-sdk` v0.3.0 提供，经可选依赖 ``pip install agent-aimail[did]`` 引入，
  SDK 侧只在 P4 做几十行胶水；
* **收件侧裁决**（``reason`` → accept / reject / flag）在 P6 接入。

为什么 SDK 不自己实现投影：那会变成三份实现（Rust / Python / TypeScript）。
字节契约只有一份——``aimail-gateway/docs/DID-SIGNATURE-PROTOCOL.md``，
向量在 ``contract/did-signature-vectors.json``（与 gateway 的
``docs/vectors/did-signature.json`` 逐字节一致）。一旦三份实现漂移，
签名在对端永远验不过，而且很难定位。

依赖约束：本模块**只用标准库**，不引入任何第三方包（``pyproject.toml`` 的
``dependencies = []`` 不变）。
"""

from __future__ import annotations

from typing import Tuple

__all__ = [
    "HEADER_NAME",
    "RESULTS_HEADER_NAME",
    "SYSTEM_MAIL_HEADER",
    "PROTOCOL_VERSION",
    "SIGNING_TAG",
    "CONTENT_TAG",
    "EMAIL_BINDING_TAG",
    "DOMAIN_CONTROL_TAG",
    "BODY_HASH_PREFIX",
    "ANCHOR_HASH_PREFIX",
    "ANCHOR_REF_PREFIX",
    "ATTACHMENT_SEPARATOR",
    "SYSTEM_MAIL_PREFIX",
    "DID_UNAVAILABLE",
    "ERRORS",
]

# ── 头名 ────────────────────────────────────────────────────────────────────
#: 发送端产出的签名头：**不可信声称**，中转方原样透传、不剥不改不重签。
HEADER_NAME = "DID-Signature"
#: 接收方 MTA 产出的验证结论：下游只信任这一份。
RESULTS_HEADER_NAME = "DID-Auth-Results"
#: 系统邮件标记（指令邮件），SDK 预处理阶段吞掉、不进 LLM 上下文。
SYSTEM_MAIL_HEADER = "X-AIMail-System"

# ── 协议版本与各级标记 ──────────────────────────────────────────────────────
PROTOCOL_VERSION = "1"
SIGNING_TAG = "DID-Signature-v1"
CONTENT_TAG = "Content-v1"
EMAIL_BINDING_TAG = "EmailBinding-v1"
DOMAIN_CONTROL_TAG = "DomainControl-v1"

# ── 摘要前缀：按层划分，不要混用 ────────────────────────────────────────────
#: 邮件内摘要（`bh` / `rh` / `name_hash` / `file_hash`）——永不落链。
BODY_HASH_PREFIX = "sha256:"
#: 链上承诺（`AnchorRecord.payload_hash`）——与链侧原语一致。
ANCHOR_HASH_PREFIX = "blake2b:"
#: `vc="chain:<anchor_id>"` 的前缀。
ANCHOR_REF_PREFIX = "chain:"

#: `Content-v1` 里附件行的字段分隔符（TAB，0x09）。
ATTACHMENT_SEPARATOR = "\t"

#: 系统邮件的主题前缀（与 `X-AIMail-System` 配合标识机器可读类型）。
SYSTEM_MAIL_PREFIX = "__aimail_system__:"

# ── 错误标识 ────────────────────────────────────────────────────────────────
#: 未安装 ``did`` 可选依赖时的显式错误（**不得**静默降级为"不签名"）。
DID_UNAVAILABLE = "did_unavailable"

#: 协议里全部 `reason` / 错误码。顺序与向量文件的 ``constants.errors`` 一致。
ERRORS: Tuple[str, ...] = (
    "did_absent",
    "did_unsupported",
    "signature_invalid",
    "body_hash_mismatch",
    "rcpt_hash_mismatch",
    "timestamp_out_of_window",
    "anchor_not_found",
    "anchor_type_mismatch",
    "anchor_hash_mismatch",
    "anchor_revoked",
    "anchor_expired",
    "chain_unavailable",
    "attachment_fetch_failed",
    "not_verifiable",
)
