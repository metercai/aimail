//! `aimail ping` decision engine (second half of `cli/ping_test.py:main`) —— **only trusts three-stage log events**.
//!
//! Three-stage events (`systems/<sid>/<agent>/agentmail.log`, user-defined 2026-08-16 = sole authoritative decision):
//! `ping_intercepted` (webhook received ping) → `pong_sent` (send_mail sends back pong, success/failure written in
//! `pong_status` field) → `pong_returned` (webhook received pong).
//! Decision: both ping+pong arrived ⇒ pass; `pong_status` after full sampling has **no ok at all** ⇒ turn red
//! (events occurred but send_mail actually failed, 2026-09-30 predecessor cause①); field absence **does not decide** (criteria only increase, never decrease).

pub const PING_PREFIX: &str = "__aimail_ping__:";

/// Parse timestamps in logs (tolerant: with/without microseconds, with/without timezone).
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
    // Civil calendar → epoch (Howard Hinnant)
    let (yy, mm2) = if mo <= 2 { (y - 1, mo + 12) } else { (y, mo) };
    let era = yy.div_euclid(400);
    let yoe = yy - era * 400;
    let doy = (153 * (mm2 - 3) + 2) / 5 + da - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    Some(days as f64 * 86400.0 + hh as f64 * 3600.0 + mm as f64 * 60.0 + ss + frac)
}

/// Event observer (feeds full log text each round, returns whether it can end).
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

    /// Scan logs (**reverse order**, same as Python) and record new events.
    ///
    /// Loop until "no new events in this pass": Python relies on repeatedly scanning the same log in 3s polling to
    /// supplement `pong_returned` (appears before `ping_intercepted` in reverse scan, blocked by `found_ping` precondition)
    ///; here it is completed within one call, **line order consistent with Python multi-round polling**.
    /// Returns whether new events were added in this pass.
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
                        " (pong_status absent, no verdict)".to_string()
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

    /// Result determination (returns (appended lines, passed or not)).
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
                .push(format!("  · pong_sent.pong_status sample: {}", joined));
            if !sorted.iter().any(|s| s == "ok") {
                self.out.push(format!(
                    "  ✗ pong_sent.pong_status has no ok at all ({}) — send_mail actually failed, the ✓ above do not count",
                    joined
                ));
                ok = false;
            }
        }
        (std::mem::take(&mut self.out), ok)
    }
}

/// Snapshot check (number of new files under `mail/` within 5 minutes).
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

/// epoch seconds → (year, month, day, hour, minute, second) UTC (civil calendar, Howard Hinnant algorithm).
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
