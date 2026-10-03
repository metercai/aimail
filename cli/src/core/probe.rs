//! 端点存活探测 —— `cli/check_status.py:1187-1202` 的逐语义复刻。
//!
//! 判据（2026-09-04 语义修正，`tests/test_check_contract.py` 钉住）：
//! - `POST` 空体；**alive = TCP 连上且拿到 HTTP 响应**；
//! - **404 = 路由没注册 = 不存活**（旧实现把它当 "plugin route active" 是错的）；
//! - 400/401/405 = 端点活着（要鉴权/要 body，但路由在）；
//! - 连接被拒/超时 = 不存活，`detail` 为错误原文（截断 60 字符）；
//! - 非 http(s) 或空 url ⇒ 不存活 + `no url`。

use std::time::Duration;

/// 默认超时（Python `_probe_endpoint(url, timeout=3.0)`）。
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

    /// 起一个只回**固定状态码**的极小 HTTP 服务（等价 Python 侧 `_Server`），
    /// 返回 (port, handle)；测试结束 drop 掉（线程随连接结束自然退出）。
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
        // 要鉴权 = 端点存在并处理了请求
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
        // 2026-09-04 语义：404 = 路由未注册 = FAIL
        let port = serve_status(404);
        let (alive, detail) = probe_endpoint(&format!("http://127.0.0.1:{port}/x"), 2.0);
        assert!(!alive, "{detail}");
        assert!(detail.contains("404"), "{detail}");
    }

    #[test]
    fn probe_connection_refused_is_dead() {
        // 找一个没人监听的端口：绑定后立刻释放
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
