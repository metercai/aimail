//! endpoint liveness probe — semantic line-by-line re-implementation of `cli/check_status.py:1187-1202`.
//!
//! Criteria (2026-09-04 semantic correction, pinned by `tests/test_check_contract.py`):
//! - `POST` empty body; **alive = TCP connected and HTTP response received**;
//! - **404 = route not registered = not alive** (the old implementation treating it as "plugin route active" is wrong);
//! - 400/401/405 = endpoint alive (requires auth/body, but route exists);
//! - connection refused/timeout = not alive, `detail` is the raw error text (truncated to 60 chars);
//! - Non-http(s) or empty url ⇒ not alive + `no url`.

use std::time::Duration;

/// Default timeout (Python `_probe_endpoint(url, timeout=3.0)`).
pub const DEFAULT_TIMEOUT_SECS: f64 = 3.0;

/// `(alive, detail)`。
pub fn probe_endpoint(url: &str, timeout_secs: f64) -> (bool, String) {
    if url.is_empty() || !url.starts_with("http") {
        return (false, "no url".to_string());
    }
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_millis(
            (timeout_secs.max(0.0) * 1000.0) as u64,
        ))
        .build();
    match agent.request("POST", url).send_bytes(b"") {
        Ok(resp) => (true, format!("HTTP {}", resp.status())),
        Err(ureq::Error::Status(404, _)) => (false, "HTTP 404 (route not found)".to_string()),
        Err(ureq::Error::Status(code, _)) => (true, format!("HTTP {code}")),
        Err(e) => {
            let text: String = e.to_string().chars().take(60).collect();
            (false, text)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;

    /// Start a minimal HTTP service that only returns a **fixed status code** (equivalent to the Python side's `_Server`),
    /// returns (port, handle); drop it when tests end (thread exits naturally when connection ends).
    fn serve_status(code: u16) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut s) = stream else { continue };
                let mut buf = [0u8; 1024];
                let _ = s.read(&mut buf);
                let body = b"{}";
                let head = format!(
                    "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = s.write_all(head.as_bytes());
                let _ = s.write_all(body);
                let _ = s.flush();
                let _: Option<&TcpStream> = None;
            }
        });
        port
    }

    #[test]
    fn probe_200_is_alive() {
        let port = serve_status(200);
        let (alive, detail) = probe_endpoint(&format!("http://127.0.0.1:{port}/x"), 2.0);
        assert!(alive, "{detail}");
        assert!(detail.contains("200"), "{detail}");
    }

    #[test]
    fn probe_401_is_alive() {
        // Requires auth = endpoint exists and handled the request
        let port = serve_status(401);
        let (alive, detail) = probe_endpoint(&format!("http://127.0.0.1:{port}/x"), 2.0);
        assert!(alive, "{detail}");
        assert!(detail.contains("401"), "{detail}");
    }

    #[test]
    fn probe_405_is_alive() {
        let port = serve_status(405);
        let (alive, detail) = probe_endpoint(&format!("http://127.0.0.1:{port}/x"), 2.0);
        assert!(alive, "{detail}");
        assert!(detail.contains("405"), "{detail}");
    }

    #[test]
    fn probe_404_is_dead() {
        // 2026-09-04 semantics: 404 = route not registered = FAIL
        let port = serve_status(404);
        let (alive, detail) = probe_endpoint(&format!("http://127.0.0.1:{port}/x"), 2.0);
        assert!(!alive, "{detail}");
        assert!(detail.contains("404"), "{detail}");
    }

    #[test]
    fn probe_connection_refused_is_dead() {
        // Find a port no one is listening on: bind then release immediately
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        let (alive, detail) = probe_endpoint(&format!("http://127.0.0.1:{port}/x"), 1.0);
        assert!(!alive, "{detail}");
        assert!(!detail.is_empty());
    }

    #[test]
    fn probe_no_url_is_dead_with_pinned_detail() {
        let (alive, detail) = probe_endpoint("", 1.0);
        assert!(!alive);
        assert_eq!(detail, "no url");
        let (alive2, detail2) = probe_endpoint("ftp://example.test", 1.0);
        assert!(!alive2);
        assert_eq!(detail2, "no url");
    }
}
