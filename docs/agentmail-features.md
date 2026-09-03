# AIMail 特性矩阵

| 分类 | 特性 | Gateway | Advanced |
|------|------|:--:|:--:|
| **邮件通道** | SMTP 入站 + Webhook 出站 | ✅ | ✅ |
| | 出站 SMTP Relay | ✅ | ✅ |
| | 出站 HTTP API (`POST /api/v1/send`) | ✅ | ✅ |
| | Push + Pull 双模式 | ✅ | ✅ |
| | Bridge 内网拉取 | ✅ | ✅ |
| **安全** | 白名单双向控制 | ✅ | ✅ |
| | 反环检测 | ✅ | ✅ |
| | API Key 三级 scope | ✅ | ✅ |
| | 入站速率限制 | no-op（占位） | ✅ |
| | IP 黑名单 | — | ✅ |
| | 速率 + 配额双重限流 | — | ✅ |
| **身份** | Persona 多身份 | ✅ | ✅ |
| | `[WHOAMI]` 通用指令 + StrangerInterceptor | ✅ | ✅ |
| | `set_public_whoami()` | ✅ | ✅ |
| **A2A Board** | `[A2A] new` / `refresh` 创建更新 | ✅ | ✅ |
| | 20+ 指令动词 Rust 闭环 | ✅ | ✅ |
| | CC 会话流（board_id/role/from_role 注入） | ✅ | ✅ |
| | 10 种通知流 | ✅ | ✅ |
| | role_permissions 数据驱动 | ✅ | ✅ |
| | 5 项 Toolset API | ✅ | ✅ |
| **运维** | 全链路 Relay Log | ✅ | ✅ |
| | 心跳诊断 | ✅ | ✅ |
| | 退回通知 Bounce/NDR | ✅ | ✅ |
| | DNS 预检提示（MX/DKIM/DMARC） | — | ✅ |
| | Metrics 端点（/metrics） | — | ✅ |
| | 统计 API（全局/系统/Agent） | — | ✅ |
| **集成** | `integrate.sh` 双语向导 | ✅ | ✅ |
| | Skill 自动安装 | ✅ | ✅ |
| | Webhook 自动补丁 | ✅ | ✅ |
| | Role Prompt 模板 + common.md fallback | ✅ | ✅ |
| **业务管理** | Two-Phase Activation | — | ✅ |
| | 产品与配额管理 | — | ✅ |
| | 系统管理（CRUD + 续期） | — | ✅ |
| | Admin SPA | — | ✅ |

**Gateway 版：** 31 项，覆盖邮件收发、安全基础、A2A Board、集成工具链。

**Advanced 版：** Gateway 全部 31 项 + Advanced 独有 11 项 = 42 项（注：Board 新角色/verb 权限、Bounce/NDR 处理等细化子项未单列）。

---
*Gateway 版即 `aimail-gateway`，Advanced 版即 `aimail-advanced`。Advanced 包含 Gateway 全部功能。*
