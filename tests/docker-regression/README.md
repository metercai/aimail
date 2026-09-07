# Docker 纯净环境 SDK 上线回归(测试体系)

**目标**:SDK 适配全沉淀在各自 SDK 包,出问题最多在 SDK → SDK 上线须最严
回归。用 Docker 纯净环境重演主流场景(安装/注册/落盘/发信/收信),云侧
网关 = **生产 aimail.token.tm(真实环境)**;发布后用**真实 registry 包**
做最后上线回归。双保险:发布前(L2.5)+ 发布后(上线验证)。

## 两层回归

### 层 A:发布前(L2.5,rc/开发包)
- `PKG_SOURCE=repo`:当前工作区(pysdk/ + tssdk/)装入容器——每次 SDK
  改动先过容器场景,再谈发版。
- 干净环境(无本机 12+ 平台残留污染)重演:TS 包就位 → 注册链×真实网关
  → 绑定落盘 → 发信 → (收信双路由 host 侧双路 E2E 承接)。
- 全绿 → 发 rc → host 冒烟 → stable。

### 层 B:发布后(上线验证,真实 registry 包)
- `PKG_SOURCE=registry VERSION=<stable>`:容器从 npm/PyPI 拉真实包,
  完整跑一遍主流场景——证明"registry 上那个包"能装、能注册、能收发。
- 云侧=生产网关:复用宿主挂载的系统 cfg(admin_key 只读)注册容器内
  agent(测试地址 `reg-<ts>.{system}@{domain}`),场景完 deregister
  清理,不污染生产地址空间。

## 主流场景清单(SDK 相关)

| # | 场景 | 断言 |
|---|---|---|
| 1 | 真实包按版本装入(5 npm 包齐全) | 包目录存在且版本=target |
| 2 | 注册链×真实网关(node_entry register-cli) | `{ok:true,email}` 返回 |
| 3 | 绑定落盘 | agentmail.json 含 api_key/webhook_url/email |
| 4 | 发信(API 通道经网关 system sender) | 网关受理(send ok / pending 出现) |
| 5 | 收信链(ping 三阶段:投递→验签→ack→pong) | 两路 E2E 全闭环(host 侧 run-e2e 承接) |
| 6 | 清理(deregister 测试地址) | 地址注销,无残留 |

## 用法

```bash
# 构建(仓库根)
docker build -t aimail-regression \
  --build-arg PKG_SOURCE=registry --build-arg VERSION=0.1.10 \
  -f tests/docker-regression/Dockerfile .

# 运行(挂载系统 cfg 只读;--network host 便于容器内收信端点)
docker run --rm --network host \
  -v "$HOME/.aimail:/root/.aimail:ro" \
  aimail-regression <system-id> https://api.aimail.token.tm aimail.token.tm
```

## 门禁接法

- 发布流程把"容器场景 1-6 全绿"作为 stable tag 前硬门(替代/强化现
  L2.5 手动清单第 3-4 步;收信双路仍 host 侧,两路都测为铁律)。
- CI 可加 nightly job(registry latest 容器回归);tag 触发时跑 target
  版本回归,红了不发。

## 状态

- Dockerfile + 场景骨架 + 本 README:已提交。
- 场景 1-3 脚本逻辑完整;4-6 为宿主侧承接点(收信链依赖常驻进程与
  双路 E2E,归 host 测试)。首次真实执行需:docker daemon 可用、生产
  系统 cfg 挂载、一次全流程跑通后把输出固化到 release-gates 记录。
