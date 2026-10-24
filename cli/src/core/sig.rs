//! v1 API signature — the implementation of the protocol contract.
//!
//! Source of truth: gateway `docs/API-SIGNATURE-PROTOCOL.md`; Python authoritative implementation
//! `pysdk/aimail_base.compute_api_signature:29-59` (also offline copy
//! `cli/check_status._signed_headers`). The Rust side must replicate verbatim:
//!
//! ```text
//! key_hash = sha256_hex(raw_key)          # raw key not sent online
//! base     = "{METHOD}\n{path_and_query}\n{timestamp_ms}\n{sha256_hex(body)}"
//! sig      = hex(HMAC-SHA256(key = utf-8 bytes of key_hash, msg = base))
//! ```
//!
//! `path` must be the request target **used when going online** (path + query, after URL encoding), otherwise the server-side
//! not equal to the base recomputed by `path_and_query()`. Empty `api_key` ⇒ `None` (caller does not send the signature header).
//!
//! Golden vectors are cross-language fixed in `tests/test_signature.py:16-24` (the file self-describes as "portable to the
//! Rust side for cross-language diff") — the unit tests in this module pin the same set of values directly.

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::time::{SystemTime, UNIX_EPOCH};

/// Signature headers (`X-Api-Timestamp` / `X-Api-Signature`).
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

/// sha256 hex lowercase (same value as Python `hashlib.sha256(...).hexdigest()`).
pub fn sha256_hex(data: &[u8]) -> String {
    hex(Sha256::digest(data).as_slice())
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

/// Compute the v1 signature header. `api_key` empty ⇒ `None` (same semantics as Python).
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

/// Sign HTTP headers (`cli/check_status.py:800-817` / `pysdk/aimail_base._signed_headers` same protocol).
///
/// Note two points consistent with Python verbatim: ① `Content-Type: application/json` is **always** included
/// (even for GET with no body); ② `X-Api-Identity` appears only when identity is non-empty.
pub fn signed_headers(
    api_key: &str,
    method: &str,
    path: &str,
    body: Option<&[u8]>,
    identity: &str,
) -> Vec<(&'static str, String)> {
    let mut headers: Vec<(&'static str, String)> =
        vec![("Content-Type", "application/json".to_string())];
    if !identity.is_empty() {
        headers.push(("X-Api-Identity", identity.to_string()));
    }
    // Empty key does not sign (Python side also does not produce signature headers; caller should have already determined failure at this point)
    if let Some(sig) = compute(api_key, method, path, body.unwrap_or(b""), None) {
        headers.push(("X-Api-Timestamp", sig.timestamp));
        headers.push(("X-Api-Signature", sig.signature));
    }
    headers
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Same set of golden vectors as Python side tests/test_signature.py:16-24 (cross-language diff) ──
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
        // Manually calculate the base string layout in the documentation (does not rely on internal implementation functions)
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
