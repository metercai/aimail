//! 核心基础设施层（plan v3 S2 起逐模块填充）。
//!
//! S1 只落 `home`（`version` 需要它）；其余（perms/config/gateway/smtp/
//! platforms/proc/bridge/runtime/payload）在各自步骤加入。

pub mod home;
