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
