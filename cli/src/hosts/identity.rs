//! ① 身份面：宿主识别特征 + 语言类型 + 指针形态。
//! 判定算法（Strict/Scan/Repair 三档）在 P2 迁入本模块。

use serde::ser::SerializeMap;
use serde::{Serialize, Serializer};

/// agent 系统的实现语言（由 `register.kind` 派生，取代分散的字符串比较）。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lang {
    Node,
    Python,
}

/// 指针形态：根指针 / 根 + `profiles/*`。
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PointerKind {
    Root,
    RootOrProfiles,
}

impl PointerKind {
    pub fn as_str(self) -> &'static str {
        match self {
            PointerKind::Root => "root",
            PointerKind::RootOrProfiles => "root_or_profiles",
        }
    }
}

/// 序列化成注册表里的形状 `{"kind": "..."}`（spec 输出逐字节保真）。
impl Serialize for PointerKind {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        let mut m = s.serialize_map(Some(1))?;
        m.serialize_entry("kind", self.as_str())?;
        m.end()
    }
}

/// 平台识别特征：`dir_name`（可选目录名闸）+ `markers`（必命中）。
#[derive(Serialize, Clone, Copy)]
pub struct Detect {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dir_name: Option<&'static str>,
    pub markers: &'static [&'static str],
}
