//! CLI detection surface —— Contract v1.0 §5 (owner 2026-10-11 added).
//!
//! **Single source of truth for platform knowledge = `crate::hosts`** (one `HostDef` per host). This module only does
//! "given home, answer which host this is / where things are / what is installed", data is always derived from host table:
//!   · Feature markers ← `HostDef.detect.markers`            · Landing point ← `HostDef.home_dir`
//!   · Plugin entry ← `register.node_path` (node host)   · Language ← `HostDef.lang()`
//!   · agent key container ← `HostDef.agents_container`
//!
//! This module **does not implement** platform adaptation and SDK logic (that is SDK's job), nor does it hold any platform constants.
//!
//! Priority (Contract §5): explicit `--platform` (validated, no silent fallback) → installed components (entry present)
//! → host feature markers (fallback for uninstalled state) → `unknown` ⇒ loud failure. Uninstalled state **only allows** the first two methods.
//!
//! Candidate roots = `[home, home/<home_dir>]`: caller provides **host root** by convention (e.g. `~/.hermes`),
//! or may provide their parent directory (e.g. `~`). **Only search for entry under candidate roots according to that host's own layout**
//! —— no unbounded depth traversal: otherwise any nested host tree under parent directory would be misclaimed (tested defect).

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::hosts::exec::Register;
use crate::hosts::{all, order, Host, HostDef, Lang};

/// Detection result (`platform spec --json`'s `detected` section).
#[derive(Debug, Clone)]
pub struct StaticInfo {
    pub platform: String,
    /// `"node"` (platform registrar entry present) | `"python"` (python host, goes through gate) | `""` (unknown/uninstalled).
    pub transport: &'static str,
    /// Hit candidate root (platform home).
    pub home: PathBuf,
    /// Scanned agent keys (multi-agent host); empty for single-agent host ⇒ SDK takes default name.
    pub agent_keys: Vec<String>,
    pub pointer: Option<PathBuf>,
    /// node host: `<pkg>/dist/register-cli.js` (matched by host layout pattern; owner 2026-10-11: three packages in same `dist/` directory).
    pub entry: Option<PathBuf>,
}

impl StaticInfo {
    pub fn to_json(&self) -> Value {
        json!({
            "platform": self.platform,
            "transport": self.transport,
            "home": self.home.display().to_string(),
            "agent_keys": self.agent_keys,
            "pointer": self.pointer.as_ref().map(|p| p.display().to_string()),
            "entry": self.entry.as_ref().map(|p| p.display().to_string()),
        })
    }
}

/// Candidate roots: `home` itself, and `home/<home_dir>` (when caller provides parent directory).
fn candidate_roots(home: &Path, h: &HostDef) -> Vec<PathBuf> {
    let mut roots = vec![home.to_path_buf()];
    let nested = home.join(h.home_dir);
    if nested.is_dir() {
        roots.push(nested);
    }
    roots
}

fn expand_alts(seg: &str) -> Vec<String> {
    let t = seg.trim();
    if t.starts_with('{') && t.ends_with('}') {
        t[1..t.len() - 1]
            .split(',')
            .map(|s| s.trim().to_string())
            .collect()
    } else {
        vec![seg.to_string()]
    }
}

/// Find files under `root` by pattern (`*` single-level wildcard + `{a,b}` alternatives).
fn match_pattern(root: &Path, pattern: &str) -> Option<PathBuf> {
    let segs: Vec<&str> = pattern.split('/').collect();
    fn walk(dir: &Path, segs: &[&str]) -> Option<PathBuf> {
        let Some((seg, rest)) = segs.split_first() else {
            return dir.is_file().then(|| dir.to_path_buf());
        };
        if *seg == "*" {
            let rd = std::fs::read_dir(dir).ok()?;
            let mut names: Vec<PathBuf> = rd
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.is_dir())
                .collect();
            names.sort();
            for n in names {
                if let Some(hit) = walk(&n, rest) {
                    return Some(hit);
                }
            }
            return None;
        }
        for alt in expand_alts(seg) {
            if let Some(hit) = walk(&dir.join(alt), rest) {
                return Some(hit);
            }
        }
        None
    }
    walk(root, &segs)
}

/// node host entry template (`register.node_path`, `{home}` relative to host root); python host ⇒ None.
fn entry_pattern(h: &HostDef) -> Option<&'static str> {
    match h.register {
        Register::NodeEntry { node_path, .. } => Some(node_path),
        _ => None,
    }
}

fn find_entry(root: &Path, h: &HostDef) -> Option<PathBuf> {
    let pat = entry_pattern(h)?;
    let rel = pat.strip_prefix("{home}/").unwrap_or(pat);
    match_pattern(root, rel)
}

fn markers_hit(root: &Path, h: &HostDef) -> bool {
    let det = h.detect;
    !det.markers.is_empty() && det.markers.iter().all(|m| root.join(m).exists())
}

/// Scan agent keys (derivable): `<root>/profiles/*` (hermes/dsh) or `<root>/agents/*` (openclaw).
/// Single-agent host scan finds nothing ⇒ empty set (naming belongs to SDK, CLI does not guess).
fn scan_agent_keys(root: &Path, h: &HostDef) -> Vec<String> {
    if h.agents_container.is_empty() {
        return Vec::new();
    }
    let dir = root.join(h.agents_container);
    if !dir.is_dir() {
        return Vec::new();
    }
    let mut keys: Vec<String> = std::fs::read_dir(&dir)
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.path().is_dir())
                .filter_map(|e| e.file_name().to_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    keys.sort();
    keys
}

/// Detect static info. `explicit` is `--platform` explicit value: if hit, use it (and run self-check once),
/// if not hit **loud failure**, no silent fallback (Contract §5).
pub fn detect_static(home: &Path, explicit: Option<&str>) -> Result<StaticInfo, String> {
    let (h, root) = match explicit {
        Some(id) => match crate::hosts::get(id) {
            Some(h) => (
                h,
                candidate_roots(home, h)
                    .pop()
                    .unwrap_or_else(|| home.to_path_buf()),
            ),
            None => {
                return Err(format!(
                    "--platform '{id}' unknown; valid: {}",
                    order().join(", ")
                ))
            }
        },
        None => {
            // Installed components (entry present) take priority over feature markers: entry is hard evidence of "installed".
            let mut by_entry: Option<(&'static HostDef, PathBuf)> = None;
            let mut by_marker: Option<(&'static HostDef, PathBuf)> = None;
            for p in all() {
                for r in candidate_roots(home, p) {
                    if by_entry.is_none() && find_entry(&r, p).is_some() {
                        by_entry = Some((*p, r));
                        break;
                    }
                    if by_marker.is_none() && markers_hit(&r, p) {
                        by_marker = Some((*p, r));
                    }
                }
            }
            match by_entry.or(by_marker) {
                Some((p, r)) => (p, r),
                None => {
                    return Err(format!(
                        "cannot determine platform: {} directory has no markers and no aimail pointer (specify --platform explicitly; valid: {})",
                        home.display(),
                        order().join(", ")
                    ))
                }
            }
        }
    };

    let entry = find_entry(&root, h);
    let transport = if entry.is_some() {
        "node"
    } else if matches!(h.lang(), Lang::Python) {
        "python"
    } else {
        ""
    };
    let pointer = {
        // Single source of truth for contract constants: filename taken from `contract::pointer_file()`
        let p = root.join(crate::core::contract::pointer_file());
        p.is_file().then_some(p)
    };
    Ok(StaticInfo {
        platform: h.id.to_string(),
        transport,
        home: root.clone(),
        agent_keys: scan_agent_keys(&root, h),
        pointer,
        entry,
    })
}

/// Payload for `aimail platform spec --json`: value view of the host table + (optional) detection results.
/// shape = `order` + `platforms`{id→object} + `detected`; advanced gate/clamp uses this command as the sole entry.
pub fn spec_json(home: Option<&Path>, explicit: Option<&str>) -> Value {
    let reg = crate::hosts::describe_all();
    let detected = match home {
        Some(h) => match detect_static(h, explicit) {
            Ok(info) => info.to_json(),
            Err(msg) => json!({ "error": msg }),
        },
        None => Value::Null,
    };
    json!({
        "order": reg.get("order").cloned().unwrap_or_else(|| json!([])),
        "platforms": reg.get("platforms").cloned().unwrap_or_else(|| json!({})),
        "detected": detected,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn touch(path: &Path) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, "{}").unwrap();
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("aimail-detect-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn explicit_platform_unknown_fails_loudly() {
        let h = tmp("unknown");
        let err = detect_static(&h, Some("nope")).unwrap_err();
        assert!(err.contains("--platform 'nope' unknown"), "{err}");
        assert!(err.contains("hermes"), "valid list should list platforms: {err}");
    }

    #[test]
    fn explicit_platform_targets_nested_home_when_parent_given() {
        let h = tmp("explicit-nested");
        let nested = h.join(".hermes");
        std::fs::create_dir_all(nested.join("profiles")).unwrap();
        let info = detect_static(&h, Some("hermes")).unwrap();
        assert_eq!(info.platform, "hermes");
        assert_eq!(info.home, nested, "when given parent dir, should land in <parent>/.hermes");
    }

    #[test]
    fn markers_probe_identifies_host_without_registry() {
        let h = tmp("markers");
        std::fs::create_dir_all(h.join("hermes-agent")).unwrap();
        let info = detect_static(&h, None).unwrap();
        assert_eq!(info.platform, "hermes");
        assert_eq!(info.transport, "python"); // no node entry ⇒ python host goes through gate
    }

    #[test]
    fn markers_all_required_not_any() {
        let h = tmp("markers-all");
        std::fs::create_dir_all(h.join("backend")).unwrap(); // only hits the first half of the deerflow marker
        let err = detect_static(&h, None).unwrap_err();
        assert!(err.contains("cannot determine platform"), "{err}");
    }

    #[test]
    fn installed_node_entry_wins_over_markers_and_is_derived_by_layout_pattern() {
        let h = tmp("entry");
        std::fs::create_dir_all(h.join("openclaw.json")).ok();
        // dsh layout: profiles/<profile>/node_modules/agent-aimail/dist/dsh/register-cli.js
        touch(&h.join("profiles/web/node_modules/agent-aimail/dist/dsh/register-cli.js"));
        let info = detect_static(&h, None).unwrap();
        assert_eq!(info.platform, "dsh", "installed components take precedence over feature markers");
        assert_eq!(info.transport, "node");
        assert!(info
            .entry
            .unwrap()
            .ends_with("node_modules/agent-aimail/dist/dsh/register-cli.js"));
        assert_eq!(info.agent_keys, vec!["web".to_string()]);
    }

    #[test]
    fn nested_foreign_home_is_not_claimed() {
        // Regression for measured defect: giving /tmp (which nests another host tree under it) must not treat /tmp as dsh.
        let outer = tmp("outer");
        touch(&outer.join("inner/profiles/web/node_modules/agent-aimail/dist/dsh/register-cli.js"));
        let err = detect_static(&outer, None).unwrap_err();
        assert!(
            err.contains("cannot determine platform"),
            "unknown tree under parent dir must not be claimed: {err}"
        );
    }

    #[test]
    fn parent_dir_with_nested_own_home_is_detected() {
        // caller gives parent dir (~ style): candidate roots include <parent>/.dsh ⇒ hit
        let parent = tmp("parent");
        touch(&parent.join(".dsh/profiles/web/node_modules/agent-aimail/dist/dsh/register-cli.js"));
        let info = detect_static(&parent, None).unwrap();
        assert_eq!(info.platform, "dsh");
        assert_eq!(info.home, parent.join(".dsh"));
        assert!(info.entry.unwrap().ends_with("dist/dsh/register-cli.js"));
    }

    #[test]
    fn single_agent_host_leaves_agent_keys_empty() {
        let h = tmp("single");
        touch(&h.join("agent/npm/node_modules/agent-aimail/dist/pi/register-cli.js"));
        let info = detect_static(&h, None).unwrap();
        assert_eq!(info.platform, "pi");
        assert_eq!(info.transport, "node");
        assert!(
            info.agent_keys.is_empty(),
            "single agent host does not guess names (naming belongs to SDK)"
        );
    }

    #[test]
    fn spec_json_carries_full_registry_and_detection() {
        let h = tmp("specjson");
        std::fs::create_dir_all(h.join("profiles")).ok();
        let v = spec_json(Some(&h), Some("dsh"));
        // After S3a shape = original platform registry (order + platforms{id→object}) + detected.
        let order = v["order"].as_array().unwrap();
        let ids: Vec<&str> = order.iter().map(|p| p.as_str().unwrap()).collect();
        assert_eq!(ids, vec!["pi", "dsh", "hermes", "openclaw", "deerflow"]);
        let plats = v["platforms"].as_object().unwrap();
        assert_eq!(plats.len(), crate::hosts::ALL.len());
        // Full shape export surface: install_steps / health_checks / register / pointer keys are present.
        let dsh = &plats["dsh"];
        assert!(dsh["install_steps"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["kind"] == "register_default"));
        assert!(dsh["health_checks"].as_array().is_some());
        assert_eq!(dsh["register"]["kind"], "node_entry");
        // pointer.file belongs to contract single source (`contract::pointer_file()`), registry only keeps kind.
        assert_eq!(dsh["pointer"]["kind"], "root");
        assert!(dsh["pointer"].get("file").is_none());
        assert_eq!(v["detected"]["platform"], "dsh");
        assert_eq!(v["detected"]["transport"], ""); // platform explicitly given but not installed ⇒ no entry, non-python host
    }
}
