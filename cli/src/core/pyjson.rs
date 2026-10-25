//! Equivalent output of Python `json.dumps(...)` default separators (`", "` / `": "`, no newlines).
//!
//! Why a separate one is needed: the machine interface contract is "stdout **exactly one line of JSON**", tests compare **verbatim** ⇒ separator style
//! is also part of the contract. serde's compact output (`{"a":1}`) is not equivalent to Python's `{"a": 1}`, empirically
//! `install --system-only` directly fails.
//!
//! Implementation: compact-serialize first, then insert a space after `,` / `:` **outside string literals** (with escape handling).

use serde_json::Value;

pub fn dumps_python(v: &Value) -> String {
    let compact = serde_json::to_string(v).unwrap_or_else(|_| "null".to_string());
    let mut out = String::with_capacity(compact.len() + 8);
    let mut in_str = false;
    let mut escaped = false;
    for ch in compact.chars() {
        out.push(ch);
        if in_str {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_str = false;
            }
            continue;
        }
        match ch {
            '"' => in_str = true,
            ',' | ':' => out.push(' '),
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn matches_python_default_separators() {
        let v = json!({"success": false, "error": "x", "n": 1, "arr": [1, 2]});
        assert_eq!(
            dumps_python(&v),
            r#"{"success": false, "error": "x", "n": 1, "arr": [1, 2]}"#
        );
    }

    #[test]
    fn keeps_separators_inside_strings_intact() {
        let v = json!({"hint": "a, b: c"});
        assert_eq!(dumps_python(&v), r#"{"hint": "a, b: c"}"#);
        // Backslash escapes do not misinterpret following commas as "outside string" (separators must not get spaces added)
        let v2 = json!({"k": "a\\b", "n": 1});
        assert_eq!(dumps_python(&v2), "{\"k\": \"a\\\\b\", \"n\": 1}");
    }
}

/// Equivalent of Python `repr()` (**for messages**): `{'a': 1, 'b': True}` / `[1, 'x']` / `None`.
///
/// Why it is needed: Python side uses many `_fail(f"… failed: {r}")` which print **dict repr** (single quotes,
/// Different from `json.dumps` double quotes) ⇒ cannot use `dumps_python` for verbatim comparison. Key order follows `preserve_order`.
pub fn repr_python(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => repr_str(s),
        Value::Array(a) => {
            let items: Vec<String> = a.iter().map(repr_python).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(o) => {
            let items: Vec<String> = o
                .iter()
                .map(|(k, val)| format!("{}: {}", repr_str(k), repr_python(val)))
                .collect();
            format!("{{{}}}", items.join(", "))
        }
    }
}

/// Python string repr quote/escape rules (no single quotes and has double quotes ⇒ use single quotes; otherwise switch as needed).
fn repr_str(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Equivalent of Python `str()` (what `{r.get('k')}` in an f-string prints): strings **without quotes**,
/// `None`→"None", `True`→"True"; containers are the same as `repr`.
pub fn str_python(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => repr_python(other),
    }
}

#[cfg(test)]
mod repr_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn repr_matches_python_shapes() {
        assert_eq!(repr_python(&json!(null)), "None");
        assert_eq!(repr_python(&json!(true)), "True");
        assert_eq!(repr_python(&json!(0)), "0");
        assert_eq!(repr_python(&json!("x")), "'x'");
        // Strings containing single quotes ⇒ switch to double quotes (CPython rule)
        assert_eq!(repr_python(&json!("it's")), "\"it's\"");
        assert_eq!(
            repr_python(&json!({"status": 400, "error": "bad"})),
            "{'status': 400, 'error': 'bad'}"
        );
        assert_eq!(repr_python(&json!([1, "x", false])), "[1, 'x', False]");
        assert_eq!(
            repr_python(&json!({"a": {"b": null}})),
            "{'a': {'b': None}}"
        );
    }
}
