//! v1 API 签名 —— 协议契约的实现。
//!
//! 真源：gateway `docs/API-SIGNATURE-PROTOCOL.md`；Python 权威实现
//! `pysdk/aimail_base.compute_api_signature:29-59`（另有离线副本
//! `cli/check_status._signed_headers`）。Rust 侧必须逐字复刻：
//!
//! ```text
//! key_hash = sha256_hex(raw_key)          # 原始 key 不上线
//! base     = "{METHOD}\n{path_and_query}\n{timestamp_ms}\n{sha256_hex(body)}"
//! sig      = hex(HMAC-SHA256(key = key_hash 的 utf-8 字节, msg = base))
//! ```
//!
//! `path` 必须是**上线时用的**请求目标（path + query，URL 编码后），否则服务端
//! `path_and_query()` 重算出的 base 不相等。空 `api_key` ⇒ `None`（调用方不发签名头）。
//!
//! 黄金向量跨语言固化在 `tests/test_signature.py:16-24`（该文件自陈"portable to the
//! Rust side for cross-language diff"）—— 本模块的单测直接钉同一组值。

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::time::{SystemTime, UNIX_EPOCH};

/// 签名头（`X-Api-Timestamp` / `X-Api-Signature`）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Signature {
    pub timestamp: String,
    pub signature: String,
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// sha256 十六进制小写（与 Python `hashlib.sha256(...).hexdigest()` 同值）。
pub fn sha256_hex(data: &[u8]) -> String {
    hex(Sha256::digest(data).as_slice())
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

/// 计算 v1 签名头。`api_key` 为空 ⇒ `None`（与 Python 同语义）。
pub fn compute(
    api_key: &str,
    method: &str,
    path: &str,
    body: &[u8],
    timestamp_ms: Option<u128>,
) -> Option<Signature> {
    if api_key.is_empty() {
        return None;
    }
    let key_hash = sha256_hex(api_key.as_bytes());
    let timestamp = match timestamp_ms {
        Some(ts) => ts.to_string(),
        None => now_ms().to_string(),
    };
    let base = format!(
        "{}\n{}\n{}\n{}",
        method.to_uppercase(),
        path,
        timestamp,
        sha256_hex(body)
    );
    let mut mac =
        Hmac::<Sha256>::new_from_slice(key_hash.as_bytes()).expect("HMAC accepts any key length");
    mac.update(base.as_bytes());
    Some(Signature {
        timestamp,
        signature: hex(&mac.finalize().into_bytes()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── 与 Python 侧 tests/test_signature.py:16-24 同一组黄金向量（跨语言 diff） ──
    const GOLDEN_KEY: &str = "0123456789abcdef";
    const GOLDEN_TS: u128 = 1_700_000_000_000;
    const GOLDEN_SIG: &str = "febe8865310009c77673b64a04310ef531ec6e2d49c9d05538a13b7ff80e268d";

    #[test]
    fn golden_vector_matches_python_baseline() {
        let s = compute(
            GOLDEN_KEY,
            "post",
            "/api/v1/whoami",
            b"{\"a\":1}",
            Some(GOLDEN_TS),
        )
        .expect("non-empty key signs");
        assert_eq!(s.timestamp, GOLDEN_TS.to_string());
        assert_eq!(s.signature, GOLDEN_SIG);
    }

    #[test]
    fn base_string_layout_is_independent_of_key_derivation() {
        // 手算一遍文档里的 base 串布局（不依赖实现内部函数）
        let key_hash = sha256_hex(GOLDEN_KEY.as_bytes());
        let base = format!(
            "POST\n/api/v1/whoami\n{}\n{}",
            GOLDEN_TS,
            sha256_hex(b"{\"a\":1}")
        );
        let mut mac = Hmac::<Sha256>::new_from_slice(key_hash.as_bytes()).unwrap();
        mac.update(base.as_bytes());
        assert_eq!(hex(&mac.finalize().into_bytes()), GOLDEN_SIG);
    }

    #[test]
    fn empty_key_yields_none_and_method_is_upper_cased() {
        assert!(compute("", "GET", "/api/v1/whoami", b"", None).is_none());
        let lower = compute("k", "get", "/x", b"", Some(1)).unwrap();
        let upper = compute("k", "GET", "/x", b"", Some(1)).unwrap();
        assert_eq!(
            lower, upper,
            "method must be upper-cased into the base string"
        );
    }

    #[test]
    fn signature_is_64_hex_chars_and_timestamp_is_numeric() {
        let s = compute("k", "POST", "/api/v1/ping", b"{}", None).unwrap();
        assert_eq!(s.signature.len(), 64);
        assert!(s.signature.chars().all(|c| c.is_ascii_hexdigit()));
        assert!(s.timestamp.chars().all(|c| c.is_ascii_digit()));
    }
}
