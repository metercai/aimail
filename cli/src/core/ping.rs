//! `aimail ping` 的判定引擎（`cli/ping_test.py:main` 后半段）—— **只信三阶段日志事件**。
//!
//! 三阶段事件（`systems/<sid>/<agent>/agentmail.log`，用户定调 2026-08-16 = 唯一权威判定）：
//! `ping_intercepted`（webhook 收到 ping）→ `pong_sent`（send_mail 发回 pong，成败写在
//! `pong_status` 字段）→ `pong_returned`（webhook 收到 pong）。
//! 判定：ping+pong 都到 ⇒ 通过；`pong_status` 全量采样后**一条 ok 都没有** ⇒ 翻红
//! （事件发生过但 send_mail 实际失败，2026-09-30 前驱定因①）；字段缺席**不判**（判据只增不减）。

pub const PING_PREFIX: &str = "__aimail_ping__:";

/// 解析日志里的时间戳（容忍：带/不带微秒、带/不带时区）。
pub fn parse_ts(s: &str) -> Option<f64> {
    let t = s.get(..s.len().min(26)).unwrap_or(s);
    let (date, rest) = t.split_once('T')?;
    let mut d = date.split('-');
    let (y, mo, da): (i64, i64, i64) = (
        d.next()?.parse().ok()?,
        d.next()?.parse().ok()?,
        d.next()?.parse().ok()?,
    );
    let (hh, mm, ss, frac) = {
        let (hms, _tz) = match rest.find(['+', 'Z', 'z']) {
            Some(i) => (&rest[..i], Some(&rest[i..])),
            None => (rest, None),
        };
        let (main, frac) = match hms.split_once('.') {
            Some((a, b)) => (
                a,
                b.parse::<f64>()
                    .ok()
                    .map(|f| f / 10f64.powi(b.len() as i32))
                    .unwrap_or(0.0),
            ),
            None => (hms, 0.0),
        };
        let mut p = main.split(':');
        (
            p.next()?.parse::<i64>().ok()?,
            p.next()?.parse::<i64>().ok()?,
            p.next()?.parse::<f64>().ok()?,
            frac,
        )
    };
    // 民用历 → epoch（Howard Hinnant）
    let (yy, mm2) = if mo <= 2 { (y - 1, mo + 12) } else { (y, mo) };
    let era = yy.div_euclid(400);
    let yoe = yy - era * 400;
    let doy = (153 * (mm2 - 3) + 2) / 5 + da - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    Some(days as f64 * 86400.0 + hh as f64 * 3600.0 + mm as f64 * 60.0 + ss + frac)
}

/// 事件观察器（每轮喂日志全文，返回是否已可结束）。
pub struct Watcher {
    pub ping_id: String,
    pub t0: f64,
    pub found_ping: bool,
    pub found_sent: bool,
    pub found_pong: bool,
    pub pong_statuses: Vec<String>,
    pub out: Vec<String>,
}

impl Watcher {
    pub fn new(ping_id: &str, t0: f64) -> Self {
        Self {
            ping_id: ping_id.to_string(),
            t0,
            found_ping: false,
            found_sent: false,
            found_pong: false,
            pong_statuses: Vec::new(),
            out: Vec::new(),
        }
    }

    fn secs(&self, ts: &str) -> f64 {
        parse_ts(ts).map(|t| t - self.t0).unwrap_or(0.0)
    }

    /// 扫日志（**倒序**，与 Python 同）并记录新事件。
    ///
    /// 循环到"这一遍没新事件"为止：Python 靠在 3s 轮询里反复扫同一份日志把
    /// `pong_returned`（反向扫描时先于 `ping_intercepted` 出现，受 `found_ping` 前置条件挡住）
    /// 补上；这里在一次调用内补完，**行序与 Python 多轮轮询一致**。
    /// 返回本遍是否新增了事件。
    pub fn observe(&mut self, log_text: &str) -> bool {
        let mut changed = false;
        loop {
            let before = (
                self.found_ping,
                self.found_sent,
                self.found_pong,
                self.pong_statuses.len(),
                self.out.len(),
            );
            self.scan_once(log_text);
            let after = (
                self.found_ping,
                self.found_sent,
                self.found_pong,
                self.pong_statuses.len(),
                self.out.len(),
            );
            if before == after {
                break;
            }
            changed = true;
        }
        changed
    }

    fn scan_once(&mut self, log_text: &str) {
        for line in log_text.lines().rev() {
            if !line.contains(&self.ping_id) {
                continue;
            }
            let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) else {
                continue;
            };
            let d = entry.get("dir").and_then(|v| v.as_str()).unwrap_or("");
            let ts = entry.get("ts").and_then(|v| v.as_str()).unwrap_or("");
            if d == "ping_intercepted" && !self.found_ping {
                self.found_ping = true;
                self.out.push(format!(
                    "  +{:5.1}s    Webhook Receive (ping)         ✓",
                    self.secs(ts)
                ));
            }
            if d == "pong_sent" {
                let st = entry
                    .get("pong_status")
                    .map(|v| crate::core::pyjson::str_python(Some(v)))
                    .unwrap_or_default()
                    .trim()
                    .to_string();
                if !st.is_empty() && !self.pong_statuses.contains(&st) {
                    self.pong_statuses.push(st.clone());
                }
                if self.found_ping && !self.found_sent {
                    self.found_sent = true;
                    let mark = if st.is_empty() || st == "ok" {
                        "✓"
                    } else {
                        "✗"
                    };
                    let note = if st.is_empty() {
                        " (pong_status 缺席, 不判)".to_string()
                    } else {
                        format!(" (pong_status={})", st)
                    };
                    self.out.push(format!(
                        "  +{:5.1}s    Pong Sent (send_mail)          {}{}",
                        self.secs(ts),
                        mark,
                        note
                    ));
                }
            }
            if d == "pong_returned" && self.found_ping && !self.found_pong {
                self.found_pong = true;
                self.out.push(format!(
                    "  +{:5.1}s    Webhook Return (pong)          ✓",
                    self.secs(ts)
                ));
                self.out.push(format!(
                    "  +{:5.1}s    Total round-trip: {:.1}s",
                    self.secs(ts),
                    self.secs(ts)
                ));
            }
        }
    }

    pub fn done(&self) -> bool {
        self.found_ping && self.found_pong
    }

    /// 结果判定（返回 (追加行, 是否通过)）。
    pub fn verdict(&mut self, timeout: i64, log_path: &str) -> (Vec<String>, bool) {
        let mut ok = if self.found_ping && self.found_pong {
            self.out.push(format!(
                "  ✓ Full pipeline verified — ping intercepted & pong returned (ping_id={})",
                self.ping_id
            ));
            true
        } else if self.found_ping {
            self.out.push(format!(
                "  ✗ Ping intercepted, but pong not returned within {}s",
                timeout
            ));
            false
        } else {
            self.out.push(format!(
                "  ✗ No ping/pong events in {} within {}s",
                log_path, timeout
            ));
            false
        };
        if !self.pong_statuses.is_empty() {
            let mut sorted = self.pong_statuses.clone();
            sorted.sort();
            let joined = sorted.join(",");
            self.out
                .push(format!("  · pong_sent.pong_status 采样: {}", joined));
            if !sorted.iter().any(|s| s == "ok") {
                self.out.push(format!(
                    "  ✗ pong_sent.pong_status 无一条 ok ({}) — send_mail 实际失败, 上面的事件 ✓ 不作数",
                    joined
                ));
                ok = false;
            }
        }
        (std::mem::take(&mut self.out), ok)
    }
}

/// 快照检查（`mail/` 下 5 分钟内新文件数）。
pub fn snapshot_line(mail_dir: &std::path::Path, sid: &str, agent: &str) -> String {
    let (mut total, mut fresh) = (0usize, 0usize);
    let now = std::time::SystemTime::now();
    if mail_dir.exists() {
        let mut stack = vec![mail_dir.to_path_buf()];
        while let Some(d) = stack.pop() {
            if let Ok(it) = std::fs::read_dir(&d) {
                for e in it.flatten() {
                    let p = e.path();
                    if p.is_dir() {
                        stack.push(p);
                    } else if let Ok(md) = p.metadata() {
                        total += 1;
                        if let Ok(m) = md.modified() {
                            if now.duration_since(m).map(|d| d.as_secs()).unwrap_or(9999) < 300 {
                                fresh += 1;
                            }
                        }
                    }
                }
            }
        }
    }
    if fresh > 0 {
        format!(
            "  ✓ Snapshots: {} new file(s) in systems/{}/{}/mail/ (total {})",
            fresh, sid, agent, total
        )
    } else {
        format!(
            "  ⚠ Snapshots: {} total file(s), none from last 5min",
            total
        )
    }
}

/// epoch 秒 → (年, 月, 日, 时, 分, 秒) UTC（民用历，Howard Hinnant 算法）。
pub fn epoch_to_utc_parts(ts: i64) -> (i64, u32, u32, u32, u32, u32) {
    let days = ts.div_euclid(86400);
    let rem = ts.rem_euclid(86400);
    let (hh, mm, ss) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d, hh as u32, mm as u32, ss as u32)
}
