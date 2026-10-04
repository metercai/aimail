//! Python `json.dumps(...)` 默认分隔符的等价输出（`", "` / `": "`，无换行）。
//!
//! 为什么需要单独一个：机器面契约是"stdout **恰一行 JSON**"，测试按**逐字**比对 ⇒ 分隔符风格
//! 也是契约的一部分。serde 的紧凑输出（`{"a":1}`）与 Python 的 `{"a": 1}` 不等价，实测在
//! `install --system-only` 上直接判红。
//!
//! 实现：先紧凑序列化，再在**字符串字面量之外**的 `,` / `:` 后补空格（含转义处理）。

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
        // 反斜杠转义不会把后随的逗号误判成"字符串外"（分隔符不得被补空格）
        let v2 = json!({"k": "a\\b", "n": 1});
        assert_eq!(dumps_python(&v2), "{\"k\": \"a\\\\b\", \"n\": 1}");
    }
}

/// Python `repr()` 的等价物（**报文用**）：`{'a': 1, 'b': True}` / `[1, 'x']` / `None`。
///
/// 为什么要它：Python 侧大量 `_fail(f"… failed: {r}")` 打的是 **dict 的 repr**（单引号，
/// 与 `json.dumps` 的双引号不同）⇒ 逐字比对时不能用 `dumps_python`。键序沿用 `preserve_order`。
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

/// Python 字符串 repr 的引号/转义规则（无单引号且有双引号 ⇒ 用单引号；否则按需切换）。
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

/// Python `str()` 的等价物（f-string 里 `{r.get('k')}` 打的就是它）：字符串**不带引号**、
/// `None`→"None"、`True`→"True"；容器与 `repr` 相同。
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
        // 含单引号的串 ⇒ 切双引号（CPython 规则）
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
