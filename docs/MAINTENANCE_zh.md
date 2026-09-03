# AgentMail 运维指南

---

## 目录

1. [本地存储](#1-本地存储)
2. [日志](#2-日志)
3. [诊断（CLI）](#3-诊断cli)
4. [aimail-bridge](#4-aimail-bridge)
5. [Hermes 网关](#5-hermes-网关)
6. [常见问题](#6-常见问题)
7. [CLI 参考](#7-cli-参考)

---

## 1. 本地存储

### 目录结构

```
~/.aimail/
├── systems/
│   └── {system_id}/
│       ├── agentmail_gateway.json     # 网关连接配置(gateway_url, admin_key, system_id, domain)
│       ├── board/                     # 系统级 A2A 角色 prompt（回退）
│       └── {agent_addr}/              # 按地址隔离的目录（清洗后的邮箱）
│           ├── agentmail.json         # agent 配置(email, api_key)
│           ├── board_creds.json       # A2A board 凭据（board_id → gateway_url/token）
│           └── role_prompt/           # 地址级角色 prompt（优先）
├── mail/
│   └── {agent_addr}/
│       ├── aimail.log              # agent 处理流水日志
│       └── {yyyymm}/in-*.json         # 按月入站快照
├── bridge/
│   ├── aimail_bridge.toml              # bridge 配置
│   ├── aimail_routes.toml              # 路由表(email → 本地 webhook)
│   ├── bin/aimail-bridge               # bridge 二进制
│   ├── bridge.pid                     # bridge PID
│   └── bridge.out                     # bridge stdout 日志
├── logs/
│   ├── aimail-bridge.log               # bridge 运行日志
│   └── aimail.agent.{addr}.log     # 各 agent 处理日志
├── backup-reset-*/                    # reset 前的配置快照
└── .system_raw_key/
    └── {system_id}_admin.key          # 原始 admin key(仅集成时)
```

### 关键文件

| 文件 | 内容 | 写入方 |
|------|------|--------|
| `systems/{sid}/agentmail_gateway.json` | gateway_url, admin_key, system_id, system_name, manager_address, system_home | `aimail install` / `reset` → `setup_system.py` |
| `systems/{sid}/{addr}/agentmail.json` | email, api_key, gateway_url, domain, system_id, manager_address | 注册链(`register_profiles.py` / `register_agent.py`) |
| `bridge/aimail_bridge.toml` | mode, addr/pull 配置 | `deploy_bridge.py` |

所有配置都放在 `~/.aimail/` 下；agent home 中不保存网关配置
（profile 目录里的 `.agentmail` 指针仅记录 system_id）。

---

## 2. 日志

### 日志文件

| 文件 | 内容 | 位置 |
|------|------|------|
| **aimail.log** | 邮件流水日志(ping/pong、入站/出站、预处理) | `~/.aimail/mail/{agent_addr}/aimail.log` |
| **aimail-bridge.log** | bridge 运行日志(pull、转发、路由、健康) | `~/.aimail/logs/aimail-bridge.log` |
| **gateway.log** | Hermes 网关日志(每 profile) | `~/.hermes/gateway.log`(根)或 `~/.hermes/profiles/{name}/gateway.log` |

### aimail.log 格式

每行一个 JSON 对象：

```json
{"ts":"2026-06-26T07:18:41Z","dir":"ping_intercepted","ping_id":"54deaff9cacc","from":"925457@qq.com","to":["mike@amail.token.tm"]}
```

`dir` 取值：
- `ping_intercepted` — webhook 收到 ping 邮件
- `pong_sent` — 经 send_mail 发出 pong
- `pong_returned` — pong 回到 webhook
- `inbound` — 普通入站邮件

### 日志轮转

无自动轮转。可配置 logrotate 或 cron：

```bash
# /etc/logrotate.d/aimail
~/.aimail/mail/*/aimail.log {
    daily
    rotate 7
    compress
    missingok
}
~/.aimail/logs/aimail-bridge.log {
    daily
    rotate 7
    compress
    missingok
}
```

---

## 3. 诊断（CLI）

### 运行

```bash
# 全链路诊断
./aimail check

# 带修复建议
./aimail check --verbose

# 心跳闭环测试(ping → pong)
./aimail ping

# welcome 端到端(向 manager 发一封欢迎邮件)
./aimail welcome
```

### check 层级

| 层级 | 检查项 | 目的 |
|------|--------|------|
| **Level 1: gateway** | Health / whoami / 域名列表 | 验证网关连通与权限 |
| **Level 2: bridge** | 进程存活 / 待处理查询 / 日志活跃 | 验证 bridge 运行与 pull 链路 |
| **Level 3: agent-gw** | webhook 端口可达 / 路由配置 | 验证 Hermes 网关就绪 |
| **Level 4: profile** | 配置文件存在 / 邮箱有效 | 验证 agent 配置完整 |

### Ping/Pong 测试

```bash
./aimail ping
```

经 SMTP 发送 ping 到网关 → bridge → webhook，触发自动 pong 回复，验证全链路。预期输出：

```
  Ping sent: __aimail_ping__:a1b2c3d4e5f6
  +  1.2s    Webhook Receive (ping)         OK
  +  2.9s    Pong Sent (send_mail)          OK
  +  5.1s    Webhook Return (pong)          OK
  Total round-trip: 5.1s
  Full pipeline verified
```

---

## 4. aimail-bridge

### 进程管理

```bash
# 状态(进程 / 配置 / 路由表 / 日志新鲜度)
./aimail bridge

# 重启(单实例)
./aimail bridge --restart

# 重刷某系统的转发路由
./aimail bridge --system-id <sid>
```

### 配置

`~/.aimail/bridge/aimail_bridge.toml`：

```toml
mode = "pull"

[pull]
amail_url = "https://amail.token.tm"
admin_key = "***"
system_id = "system-xxxx"
poll_interval_sec = 5

[health]
check_interval_sec = 60
fail_threshold = 3
connect_timeout_sec = 3
```

### 双模式

| 模式 | 适用场景 | 说明 |
|------|----------|------|
| `pull` | Hermes 内网、网关外网 | bridge 轮询待处理邮件 |
| `push` | Hermes 与网关同网 | 网关直接 webhook 推送(无需 bridge) |

---

## 5. Hermes 网关

### 进程管理

```bash
# 启动根 profile 网关
hermes gateway run --accept-hooks --replace

# 启动命名 profile 网关
hermes -p {name} gateway run --accept-hooks --replace

# 状态
hermes gateway status

# 端口
grep -A2 'webhook:' ~/.hermes/config.yaml
```

### 健康检查

```bash
curl http://127.0.0.1:{port}/health
```

根 profile 默认端口 8644，命名 profile 从 8645 顺序递增。

---

## 6. 常见问题

### ping 卡在 "pong not returned"

**原因：** pong 邮件未能回环。通常是 API key / 邮箱不匹配。

**检查：**
```bash
grep pong_status ~/.aimail/mail/*/aimail.log
```

**修复：** 核对 `~/.aimail/systems/{sid}/{addr}/agentmail.json` 中 email 与 api_key 是否一致。

### bridge 拉不到邮件

**检查：**
```bash
./aimail bridge
curl https://amail.token.tm/health
tail -20 ~/.aimail/logs/aimail-bridge.log
```

### 网关起不来

**检查：**
```bash
ss -tlnp | grep 8644
hermes gateway run --dry-run
cat ~/.hermes/gateway.log
```

### 重新集成

```bash
# 移除 aimail 对接(CLI,保留 ~/.aimail/ 本机数据)
./aimail uninstall --system-id <sid> --yes

# 重新安装
./aimail install --home ~/.hermes --system-id <sid>
```

`aimail install` 是幂等的——重复运行自动跳过已完成步骤。

### API key 更新

网关侧 key 轮换或失效时：

```bash
# 方法 1：清空 agentmail.json 的 activation_code 和 api_key，让 agent 下次启动时重新激活
# 方法 2：直接用新 key 替换 agentmail.json 中的 api_key
# 方法 3：重新运行 ./aimail reset --system-id <sid>
```

---

## 7. CLI 参考

`./aimail` 是唯一入口(仓库根 symlink → `scripts/aimail`)。
子命令(字母序)：`bridge`、`check`、`domain`、`install`、`mailname`、
`ping`、`reset`、`stats`、`uninstall`、`welcome`。

### 安装流程（重点）

仓库根目录的 `.env` 会被自动读取（CLI 参数 > shell 环境变量 > .env >
内置默认值），常用值只需配置一次：

```bash
# .env: AIMAIL_URL / AIMAIL_ADMIN_KEY | AIMAIL_PRODUCT_CODE / AIMAIL_MANAGER_ADDRESS

# 新系统——用激活码激活（未传参则从 .env 取）
./aimail install --home ~/.hermes --product-code <CODE> --manager admin@example.com

# 已有系统——复用已存配置或传入 admin key
./aimail install --home ~/.hermes --system-id <sid>
./aimail install --home ~/.openclaw --system-id <sid>
```

`install` 完成整条链路：系统激活 → bridge 部署 → 工具与 skill 安装 →
webhook 补丁与 profile 注册。随后验证：

```bash
./aimail check                      # 全链路诊断
./aimail ping                       # ping-pong 闭环
./aimail welcome                    # welcome 端到端(邮件到 manager)
./aimail stats                      # 本机总览(系统/agent/邮件统计)
```

### 日常运维

```bash
./aimail stats                      # 本机总览(系统 + agent + 邮件统计)
./aimail domain --system-id <sid>   # 查看系统域名
./aimail domain --system-id <sid> --add example.com   # 创建域名
./aimail mailname --system-id <sid> --default NAME    # 修改主 agent 名
./aimail reset --system-id <sid>    # 用已存 admin key 重跑注册链
./aimail uninstall --system-id <sid> --yes            # 移除对接
./aimail bridge --restart           # 重启本地 bridge
```

`--home` 定位平台根(`~/.hermes` / `~/.openclaw`)；平台无法自动识别时
优先用 `--system-id`。
