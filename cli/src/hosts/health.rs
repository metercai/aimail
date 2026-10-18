//! ④ 体检面：健康检查模型（7 kind）。执行引擎仍是 `core::checks::l2r`。

use serde::Serialize;

/// 运行时健康检查项：`id` 在外层，`kind` + 变体字段由 [`HealthSpec`] 平铺展开。
#[derive(Serialize, Clone, Copy)]
pub struct Health {
    pub id: &'static str,
    #[serde(flatten)]
    pub spec: HealthSpec,
}

/// 健康检查的 7 种 kind（`ok_text`/`fail_text` 恒在尾部，保持既有键序）。
#[derive(Serialize, Clone, Copy)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum HealthSpec {
    PointerMatch {
        path: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileContains {
        path: &'static str,
        marker: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileContainsAlt {
        path: &'static str,
        alt: &'static str,
        marker: &'static str,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileExists {
        path: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        fix: Option<&'static str>,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    FileExistsAny {
        paths: &'static [&'static str],
        ok_text: &'static str,
        fail_text: &'static str,
    },
    CommandMatch {
        argv: &'static [&'static str],
        #[serde(rename = "match")]
        match_: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        fallback_glob: Option<&'static str>,
        #[serde(skip_serializing_if = "Option::is_none")]
        fix: Option<&'static str>,
        ok_text: &'static str,
        fail_text: &'static str,
    },
    YamlToolsets {
        configs: &'static [&'static str],
        ok_text: &'static str,
        fail_text: &'static str,
    },
}
