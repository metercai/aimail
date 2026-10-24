//! ④ Health Check Surface: Health check model (7 kinds). Execution engine is still `core::checks::l2r`.

use serde::Serialize;

/// Runtime health check item: `id` is in the outer layer, `kind` + variant fields are flattened by [`HealthSpec`].
#[derive(Serialize, Clone, Copy)]
pub struct Health {
    pub id: &'static str,
    #[serde(flatten)]
    pub spec: HealthSpec,
}

/// The 7 kinds of health checks (`ok_text`/`fail_text` are always at the end, preserving the existing key order).
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

use serde_json::Value;

use crate::hosts::Host;

/// JSON view of health checks (input for the generic engine [`crate::core::checks::l2r`] and repair).
pub fn checks_json(h: &dyn Host) -> Vec<Value> {
    h.health_checks()
        .iter()
        .map(|c| serde_json::to_value(c).expect("health check must serialize"))
        .collect()
}
