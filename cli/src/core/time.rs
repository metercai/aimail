//! 极简时间助手 —— 只为复刻"到期天数"判读（`stats` 的 expires 行）。
//!
//! 现状码：`cli/aimail:3144-3148`
//! ```python
//! dt = datetime.fromisoformat(str(qr["expires_at"]).replace("Z", "+00:00"))
//! days = (dt - datetime.now(timezone.utc)).days
//! ```
//! 语义要点：`timedelta.days` 是**向下取整**的天数（负值也向下），本模块照此实现。
//!
//! 覆盖的范围（够用即止）：`YYYY-MM-DDTHH:MM:SS[.frac][Z|±HH:MM|±HHMM]`。
//! 解析不了 ⇒ `None`（调用方按"没有到期信息"处理，而不是编一个值）。

use std::time::{SystemTime, UNIX_EPOCH};

/// 把一个 RFC3339 子集解析成 Unix 秒（UTC）；失败 ⇒ None。
pub fn parse_rfc3339_secs(text: &str) -> Option<i64> {
    let t = text.trim();
    let (date, rest) = t.split_once(['T', 't', ' '])?;
    let (y, m, d) = {
        let mut it = date.split('-');
        let y: i64 = it.next()?.parse().ok()?;
        let m: i64 = it.next()?.parse().ok()?;
        let d: i64 = it.next()?.parse().ok()?;
        (y, m, d)
    };
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    // 时间部分：HH:MM[:SS[.frac]]，后面可能直接跟 Z 或偏移
    let (clock, tz) = {
        let bytes = rest.as_bytes();
        let mut idx = None;
        for (i, c) in rest.char_indices() {
            if c == 'Z' || c == 'z' || c == '+' {
                idx = Some(i);
                break;
            }
            if c == '-' && i > 0 {
                // 'Z' 之外唯一的 '-' 出现在时间之后（时区偏移）
                idx = Some(i);
                break;
            }
        }
        let _ = bytes;
        match idx {
            Some(i) => (&rest[..i], &rest[i..]),
            None => (rest, ""),
        }
    };
    let mut parts = clock.split(':');
    let hh: i64 = parts.next()?.parse().ok()?;
    let mm: i64 = parts.next()?.parse().ok()?;
    let ss: i64 = match parts.next() {
        Some(s) => {
            let s = s.split('.').next().unwrap_or("0");
            s.parse().ok()?
        }
        None => 0,
    };
    let offset = tz_offset_secs(tz)?;
    let days = days_from_civil(y, m, d);
    Some(days * 86_400 + hh * 3600 + mm * 60 + ss - offset)
}

/// 时区后缀 → 秒偏移（`Z`/空 = 0；`+HH:MM`/`-HHMM` 等）。
fn tz_offset_secs(tz: &str) -> Option<i64> {
    let t = tz.trim();
    if t.is_empty() || t.eq_ignore_ascii_case("z") {
        return Some(0);
    }
    let sign = match t.chars().next()? {
        '+' => 1,
        '-' => -1,
        _ => return None,
    };
    let digits: String = t[1..].chars().filter(|c| *c != ':').collect();
    if digits.len() != 4 {
        return None;
    }
    let hh: i64 = digits[..2].parse().ok()?;
    let mm: i64 = digits[2..].parse().ok()?;
    Some(sign * (hh * 3600 + mm * 60))
}

/// 民用日期 → 天数（Howard Hinnant 的 days_from_civil，1970-01-01 = 0）。
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// 当前 Unix 秒（UTC）。
pub fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Unix 秒 → 天数（向下取整，含负值）—— 对齐 Python `timedelta.days`。
pub fn secs_to_days_floor(secs: i64) -> i64 {
    secs.div_euclid(86_400)
}

/// `expires_at` 距 `now` 的天数（向下取整；`stats` 的 `(expires in Nd)` 用它）。
pub fn days_until(expires_secs: i64, now_secs: i64) -> i64 {
    secs_to_days_floor(expires_secs - now_secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_z_and_offsets() {
        assert_eq!(
            parse_rfc3339_secs("2026-10-03T00:00:00Z"),
            Some(1_790_985_600)
        );
        assert_eq!(
            parse_rfc3339_secs("2026-10-03T01:00:00+01:00"),
            Some(1_790_985_600)
        );
        assert_eq!(
            parse_rfc3339_secs("2026-10-03T00:00:00.500Z"),
            Some(1_790_985_600)
        );
        assert_eq!(parse_rfc3339_secs("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_rfc3339_secs("garbage"), None);
    }

    #[test]
    fn day_difference_floors_like_python_timedelta() {
        // 23:59:59 之后 < 1 天 ⇒ 0 天；跨一天 ⇒ 1 天
        assert_eq!(days_until(86_400 - 1, 0), 0);
        assert_eq!(days_until(86_400, 0), 1);
        // 负值向下取整（-1 秒 ⇒ -1 天）
        assert_eq!(days_until(-1, 0), -1);
    }
}
