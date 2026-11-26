/**
 * DID 协议常量（P0 骨架）。
 *
 * 本模块**只放常量**，不含任何字节布局实现。这是有意的：
 *
 * - **邮件层**（`Content-v1` 投影、`DID-Signature` 头编解码、Ed25519）由
 *   `did-sdk` v0.3.0 提供，经 `optionalDependencies: { "@did-sdk/client": … }` 引入，
 *   本包在 P4 只做几十行胶水；
 * - **收件侧裁决**（`reason` → accept / reject / flag）在 P6 接入。
 *
 * 为什么不在 SDK 里自己实现投影：那会变成三份实现（Rust / Python / TypeScript）。
 * 字节契约只有一份 —— `aimail-gateway/docs/DID-SIGNATURE-PROTOCOL.md`，
 * 向量在 `contract/did-signature-vectors.json`（与 gateway 的
 * `docs/vectors/did-signature.json` 逐字节一致）。三份实现一旦漂移，
 * 签名在对端永远验不过，而且极难定位。
 *
 * 依赖约束：`agent-aimail` 的 `dependencies` 必须保持 `{}`，本文件不得 import 任何运行期依赖。
 */

/** 发送端产出的签名头：**不可信声称**，中转方原样透传、不剥不改不重签。 */
export const HEADER_NAME = 'DID-Signature'

/** 接收方 MTA 产出的验证结论：下游只信任这一份。 */
export const RESULTS_HEADER_NAME = 'DID-Auth-Results'

/** 系统邮件标记（指令邮件），SDK 预处理阶段吞掉、不进 LLM 上下文。 */
export const SYSTEM_MAIL_HEADER = 'X-AIMail-System'

export const PROTOCOL_VERSION = '1'

export const SIGNING_TAG = 'DID-Signature-v1'
export const CONTENT_TAG = 'Content-v1'
export const EMAIL_BINDING_TAG = 'EmailBinding-v1'
export const DOMAIN_CONTROL_TAG = 'DomainControl-v1'

/**
 * 摘要前缀：按层划分，不要混用（协议 §2）。
 *
 * - `BODY_HASH_PREFIX` —— 邮件内摘要（`bh` / `rh` / `name_hash` / `file_hash`），永不落链；
 * - `ANCHOR_HASH_PREFIX` —— 链上承诺（`AnchorRecord.payload_hash`），与链侧原语一致。
 */
export const BODY_HASH_PREFIX = 'sha256:'
export const ANCHOR_HASH_PREFIX = 'blake2b:'

/** `vc="chain:<anchor_id>"` 的前缀。 */
export const ANCHOR_REF_PREFIX = 'chain:'

/** `Content-v1` 里附件行的字段分隔符（TAB，0x09）。 */
export const ATTACHMENT_SEPARATOR = '\t'

/** 系统邮件的主题前缀（与 `X-AIMail-System` 配合标识机器可读类型）。 */
export const SYSTEM_MAIL_PREFIX = '__aimail_system__:'

/** 未安装 `did` 可选依赖时的显式错误（**不得**静默降级为"不签名"）。 */
export const DID_UNAVAILABLE = 'did_unavailable'

/** 协议里全部 `reason` / 错误码。顺序与向量文件的 `constants.errors` 一致。 */
export const DID_ERRORS = [
  'did_absent',
  'did_unsupported',
  'signature_invalid',
  'body_hash_mismatch',
  'rcpt_hash_mismatch',
  'timestamp_out_of_window',
  'anchor_not_found',
  'anchor_type_mismatch',
  'anchor_hash_mismatch',
  'anchor_revoked',
  'anchor_expired',
  'chain_unavailable',
  'attachment_fetch_failed',
  'not_verifiable',
] as const

export type DidErrorCode = (typeof DID_ERRORS)[number]
