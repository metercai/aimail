//! Minimal time helper -- only for replicating "days until expiry" interpretation (`stats` expires line).
//!
//! Current code: `cli/aimail:3144-3148`
//! ```python
//! dt = datetime.fromisoformat(str(qr["expires_at"]).replace("Z", "+00:00"))
//! days = (dt - datetime.now(timezone.utc)).days
//! ```
//! Semantic note: `timedelta.days` is the **floor** of days (floors for negative values too); this module implements it accordingly.
//!
//! Scope covered (sufficient for now): `YYYY-MM-DDTHH:MM:SS[.frac][Z|±HH:MM|±HHMM]`.
//! Unparseable ⇒ `None` (callers treat it as "no expiry info" rather than fabricating a value).

use std::time::{SystemTime, UNIX_EPOCH};

/// Parse an RFC3339 subset into Unix seconds (UTC); failure ⇒ None.
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
    // Time part: HH:MM[:SS[.frac]], possibly followed directly by Z or an offset
    let (clock, tz) = {
        let bytes = rest.as_bytes();
        let mut idx = None;
        for (i, c) in rest.char_indices() {
            if c == 'Z' || c == 'z' || c == '+' {
                idx = Some(i);
                break;
            }
            if c == '-' && i > 0 {
                // The only '-' outside of 'Z' appears after the time (timezone offset)
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

/// Timezone suffix → second offset (`Z`/empty = 0; `+HH:MM`/`-HHMM`, etc.).
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

/// Civil date → days (Howard Hinnant's days_from_civil, 1970-01-01 = 0).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Current Unix seconds (UTC).
pub fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Unix seconds -> days (floor, including negative values) — aligned with Python `timedelta.days`.
pub fn secs_to_days_floor(secs: i64) -> i64 {
    secs.div_euclid(86_400)
}

/// Civil date ← days (Howard Hinnant's civil_from_days, the inverse of `days_from_civil`).
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Current moment in ISO8601 (UTC, 6-digit microseconds, ending with `Z`).
///
/// Aligned with Python `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`
/// (The `timestamp` field in `check --json` is this, normalized by timestamp whitelist on the parity side).
pub fn now_iso8601_z() -> String {
    let d = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = d.as_secs() as i64;
    let micros = d.subsec_micros();
    let days = secs.div_euclid(86_400);
    let sod = secs.rem_euclid(86_400);
    let (y, m, day) = civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{day:02}T{:02}:{:02}:{:02}.{micros:06}Z",
        sod / 3600,
        (sod % 3600) / 60,
        sod % 60
    )
}

/// Days from `expires_at` to `now` (floor; used by `stats`'s `(expires in Nd)`).
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
        // After 23:59:59 < 1 day ⇒ 0 days; crossing a day ⇒ 1 day
        assert_eq!(days_until(86_400 - 1, 0), 0);
        assert_eq!(days_until(86_400, 0), 1);
        // Floor for negative values (-1 second ⇒ -1 day)
        assert_eq!(days_until(-1, 0), -1);
    }

    #[test]
    fn iso8601_now_round_trips_and_is_python_shaped() {
        let s = now_iso8601_z();
        // Format: YYYY-MM-DDTHH:MM:SS.ffffffZ (equivalent to Python isoformat)
        assert_eq!(s.len(), 27, "{s}");
        assert!(s.ends_with('Z'), "{s}");
        assert_eq!(s.as_bytes()[10], b'T', "{s}");
        assert_eq!(s.as_bytes()[19], b'.', "{s}");
        let parsed = parse_rfc3339_secs(&s).expect("own output must parse");
        assert!(
            (parsed - now_secs()).abs() <= 1,
            "round trip within 1s: {s} → {parsed}"
        );
    }
}
