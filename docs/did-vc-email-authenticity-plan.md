# 基于 DID 的邮件地址真实性与完整性增强 —— 实施方案

> 状态：**方案，未执行**（P0 契约骨架已在 `did` 分支落地，见 §12）。
> 覆盖 `aimail`（pysdk / tssdk）、`aimail-gateway`、`aimail-advanced`、`did-sdk`、`chain-sdk`。

**目录**

| 章 | 内容 | 读者 |
|---|---|---|
| 0 | 一页结论 | 所有人 |
| 1 | 概念基线：事实 / 凭证 / 锚定 | 想先统一词汇的人 |
| **2** | **威胁模型与安全增益（含后续 HTTP 认证）** | **决策者** |
| 3 | 两条公理与推论 | 想理解"为什么这么设计"的人 |
| 4 | 事实基线：代码如何约束设计 | 实现者 |
| 5 | 职责矩阵 R1–R42（谁做什么） | 实现者 |
| 6 | 字节契约 | 实现者 |
| 7 | 拒绝与裁决（入口 / 收件方两级） | 实现者 + 运维 |
| 8 | 关键决策与理由 | 评审 |
| **9** | **不变量 N1–N23（逐条打勾用）** | **评审 + 测试** |
| 10 | 缺口与补齐记录 | 评审 |
| 11 | 组件边界与代码映射 | 实现者 |
| 12 | 分期、门禁、回滚 | 项目管理 |
| 13 | 风险 | 评审 |
| 14 | 对规范的修订清单 | 规范维护者 |
| 15 | 后续：HTTP 认证的信任模型（H 系列） | 决策者 |
| 16 | 决策状态与待确认 | 所有人 |

---

## 0. 一页结论

让一封邮件同时证明两件事：**「这个 `From` 地址此刻由这个 agent 的 `did:key` 控制」**
和**「正文、附件、收件人、时效未被改动」**；并且这份结论要传到最终消费者手里，由它自己再验一遍。

由此确定六个不可退让的点：

1. **签名只能客户端做**，服务商连"决定签什么"都不行（§3.1）。
2. **签名对象是脱了 MIME 的原始内容**，不是 MIME（§3.3 淘汰"服务端算好摘要"的方案）。
3. **哈希只能验证方自己算**：服务商递来的任何摘要都不可采信（§3.2）。
4. **发件服务商退化为纯转发**：不追加正文、不改写收件人、不参与签名（§3.4）。
5. **收件方 MTA 只做地址维度**，完整性由收件 SDK 独立复算（§5.2、§5.3）。
6. **锚点层独立成 crate**，因为 did-sdk（无链访问）与 chain-sdk（不含 VC 语义）都放不下它（§11.1）。

**一条主线**（全文只讲这一条因果链，其余各章都是它的展开）：

```text
地址真实性从哪来（第 1–2 章）
  → 因此签名只能由内容控制者做（第 3 章）
  → 代码允许这么做吗（第 4 章）
  → 于是每一步由谁、做什么（第 5 章）
  → 逐字节怎么定死（第 6 章）
  → 不通过时谁有权拒绝（第 7 章）
  → 为什么这么选（第 8 章）→ 怎么判定过关（第 9 章）→ 怎么落地（第 11–12 章）
```

**安全增益三句话**（第 2 章展开）：

- 对**域**的信任（SPF/DKIM/DMARC）之上，补出对**具体地址**的信任；
- 完整性结论不再停在 MTA——**gateway 无法替用户"看懂"邮件**；
- 后续把 HTTP 认证也换成 DID 签名后，**服务商库里不再有任何能冒充用户的秘密**。

---

## 1. 概念基线

### 1.1 三种东西不要混

| 东西 | 是什么 | 谁产生 | 例子 |
|---|---|---|---|
| **事实** | "某物归谁"的声明 | 发行方 | "alice@example.com 归 did:key:z6Mk…" |
| **凭证（VC）** | 可交付的 JSON 文档 | 发行方签发 | `EmailBindingVC` |
| **锚定** | 链上记录：承诺一个哈希 + 带生命周期 | 发行方（或经授权的提交代理） | `AnchorRecord` |

**锚定可以承诺"事实"，也可以承诺"凭证"** —— 这是本章最重要的区分。

### 1.2 锚定层只有一条记录，`anchor_type` 只是标签

```text
AnchorRecord { anchor_id, anchor_type, submitter_id, issuer_did,
               delegated_by_did, payload_hash, revoked, created_at, valid_until }
                              ↑
              anchor_type 只决定"payload_hash 承诺的是什么对象"
```

`AnchorType` 是**扁平枚举**（`chain-sdk/src/types.rs:134`，9 个值平级），**没有继承关系**——
`IssuedVc` 不是 `EmailBinding` 的父类，共享的只是 `AnchorRecord` 这一层结构。

`anchor_type` 由**调用方传入**（`chain-sdk/src/sd.rs:258` 直接传 `request.anchor_type`），
代码从不按类型校验语义：真正约束它的是各类型自己的 preimage 定义，**代码不会替你检查两者是否配对**（§8.9）。

### 1.3 三根互相独立的轴

| 轴 | 问的是什么 | 落在哪 |
|---|---|---|
| **承诺对象** | 承诺的是"事实"还是"凭证" | `anchor_type` 的语义 |
| **参与角色** | 谁发起、谁代提交、谁受益 | `issuer_did` / `submitter_id` / `delegated_by_did` / VC 的 `credentialSubject.id` |
| **权限** | 谁有资格发行、谁能吊销 | `chain_role` + 治理流程 + 服务端归属校验 |

**角色三者必须分开**：

| 角色 | 在 VC 里的位置 | 参与锚定吗 | 依据 |
|---|---|---|---|
| **issuer（签发方）** | `issuer` / `proof.verificationMethod` | **是，发起者** | `create_anchor` 门禁 `ensure_issuer()`；签发用 issuer 的 `participant-did` 密钥（`sd.rs:266`） |
| **subject / holder（持有方）** | `credentialSubject.id` | **否** | `Verifier` 角色无 `create_anchor` 权限 |
| **submitter / delegate（提交代理）** | 不在 VC 里 | **只代提交交易** | `submitter_id` / `delegated_by_did`，凭 `DelegationAuthorization-v1` |

### 1.4 `IssuedVc` 是什么

> **`IssuedVc` 是某一张 VC 的链上锚**：由该 VC 的签发方（或经其授权的提交代理）在**签发流程中**创建，
> 承诺 VC 全文，并提供吊销与有效期。持有方不参与创建。

它**不是**"事实锚定的再锚定"。通用签发顺序（chain-sdk 契约 §7.3）：

```text
建 VC → 算 H(VC) → create_anchor 拿 anchor_id → VC.anchor_id = id → 签发签名（覆盖 anchor_id）
```

锚在签名**之前**创建，且 `anchor_id` 被签进 VC——所以锚是**凭证身份的一部分**，不是事后承诺。
对 `WarrantVC`、学历凭证而言，它是**唯一**的锚。

拿到一张 VC 的第三方，凭它能验证什么：

| 想验证 | `IssuedVc` 管吗 | 实际靠什么 |
|---|---|---|
| 内容有没有被改过 | ✓ | `payload_hash` 比对 |
| 是否被吊销 / 过期 | ✓ | `revoked` / `valid_until` |
| 选择性披露下的真实性 | ✓（是前提） | 必须先锚定；机制在 `_sd` 加盐哈希 + `verify_vp`（`sd.rs:340` 硬性要求 `anchor_id`） |
| **发行方有资格发行** | ✗ | `chain_role`（治理）/ `Warrant` 锚定 |
| **发行方身份真实** | ✗ | 解析 `did:web`/`did:key`；域层面靠 `DomainControl` |

**核心价值**：锚定把"发行方**在线**签名"变成"发行方**离线**可验"。

### 1.5 为什么 `EmailBinding` 不能用 `IssuedVc` 表达

`IssuedVc` 的 preimage 是 `blake2b(VC 的 compact JSON)`（`vc.rs:462`，只丢 `proof` 与 `anchor_id`）。
**重算它必须持有那张 VC 的完整字节**，而接收方没有、也拿不到：

| 补救办法 | 为什么不行 |
|---|---|
| 让接收方自己找 VC | `did:key` 不承载 `service` 端点，无从查找（规范 §2.1） |
| 让接收方去问发行方 | 违反"验证时发行方可离线"（规范 §1.1-6） |
| 把 VC 塞进邮件头 | 体积大、攻击者可控、且自我指涉 |

**所以这是"能不能"的问题，不是取向问题。** 事实锚的 preimage 恰好是接收方手里那三样东西：

```text
did      ← DID-Signature 头
email    ← From 头
issuer   ← 链上锚定记录
```

### 1.6 三角关系与两个必须区分的字段

两者不是替代关系，是**同一事实的两份产物、两个锚**（加法）：

| 消费者 | 手里有什么 | 用哪个锚 |
|---|---|---|
| 接收方 MTA / 收件 agent | 邮件（From + 头） | `EmailBinding` 事实锚 |
| VC 持有方（要出示凭证、做选择性披露） | 那张 `EmailBindingVC` | `IssuedVc` 锚 |

```text
DomainControl 锚  ──授权证据──┐
                              ▼
EmailBinding 锚  ←── 被引用 ── EmailBindingVC ── 若锚定 ──→ IssuedVc 锚
   （承诺事实）                 （关于事实的凭证）            （承诺凭证）
```

**命名坑**：两个都叫 anchor，大小写只差一个字母，指向不同的锚。

| 字段 | 位置 | 指向 | 谁用 |
|---|---|---|---|
| `credentialStatus.anchorId` | VC 的 `credentialStatus` 内 | **EmailBinding 事实锚** | 接收方 / 审计 |
| 顶层 `anchor_id` | `did_sdk::VerifiableCredential.anchor_id` | **该 VC 自己的 `IssuedVc` 锚** | did-sdk 的 SD 路径 |

`anchor_payload_hash` 只丢 `proof` 与 `anchor_id`，**`credentialStatus` 会进入 VC 哈希**。含义：

> 事实锚的 id 一旦写进 VC，就被钉死在 VC 的哈希里。**事实锚不可替换**——想换只能撤销、新建、重签 VC。

---

## 2. 威胁模型与安全增益

### 2.1 现状：三层信任，各自的边界

| 层 | 机制 | 证明了什么 | **不证明什么** |
|---|---|---|---|
| 传输 | TLS | 链路对端是它声称的服务器 | 服务器内部有什么、被谁运营 |
| 域 | SPF / DKIM / DMARC（**仅 advanced 版有**） | 某个**域**授权了这封邮件 | **具体地址是否真实、归谁** |
| 接口 | `api_key` + HMAC | 请求方持有一份共享秘密 | 这份秘密是否只有用户知道 |

后两行的"不证明"正是本方案要补的。

### 2.2 补的第一件事：地址真实性

`DID-Signature` + `EmailBinding` 锚定，把信任单位从"域"降到"地址"。

**谁现在能冒充谁（改前 → 改后）**：

| 冒充者 | 改前 | 改后 |
|---|---|---|
| 任意一台 MTA 声称任意 `From` | 无法区分（DKIM 通过也只证明域） | `DID-Signature` 缺失 → `did_absent`；声称了却无锚定 → 入口拒收 |
| 发件服务商替用户签名 | 可行（服务商拼正文、可代签） | **不可行**：私钥只在客户端，服务商不得代签也不得代拼正文（§8.4） |
| 域名易主后的旧持有者 | 无法及时失效 | 新所有者改 DNS TXT → 后续校验失败；旧锚定受 `valid_until` 有界窗口约束 |
| 已注销地址继续发信 | 只能靠 DB 状态 | 撤销锚定 → 接收方即时拒绝（不受 `valid_until` 约束） |
| 把别人的邮件签名搬到自己邮件上 | — | `From`/`did`/`vc` 都在签名对象里，改任一项即失败 |

### 2.3 补的第二件事：内容完整性，且结论不停在 MTA

`Content-v1` 覆盖正文、逐附件哈希、收件人、时效；签名由客户端做，**由收件 SDK 独立复算**。

这条的意义不在"多一个签名"，而在**信任位置**：

| | 只做 MTA 验证（传统做法） | 本方案 |
|---|---|---|
| 谁能判定"这封邮件可信" | 收件 MTA（以及运营它的服务商） | **最终消费者自己**（MTA 只给地址维度） |
| gateway 能否让一封被篡改的邮件"看起来通过" | 能（它出结论） | 不能（SDK 自己算 `Content-v1` 并与签名比对） |
| 结论的形式 | 易被视为"平台担保" | 密码学可验证的断言 |

我们刻意**不让 MTA 计算完整性**（§5.2），不只为了省算力，更是为了让完整性规则**只有一份实现**
（did-sdk），从而不存在"两处判断不一致"的灰色地带。

### 2.4 后续（H 系列）：HTTP 认证的信任模型

**今天是整条链上最薄的一环**：`api_keys.key_hash = sha256(api_key)`，而它就是 HMAC 密钥。

```text
拖库/备份泄露  →  拿到 key_hash  →  可以替任意地址构造任意请求（发信、拉信、改配置）
服务商内部人   →  同理，可以代表任何用户
```

这与邮件层"服务商不得代签"的公理**方向相反**：邮件层已经做到服务商无法伪造，接口层却仍能。

**两层堵的不是同一类对手**（这是它值得单独讲的原因）：§2.2 / §2.3 堵的是**外部伪造者**——别人冒充你；
这一层堵的是**服务商自己**——它是唯一"合法地"能代表用户的角色。前者是安全问题，后者是治理问题：
只要库里还存着一份能冒充用户的秘密，再严格的内部流程都只是承诺，不是性质。

所以这一层的增益更彻底：前两层让冒充**变难**（要攻破密码学），这一层让冒充**在服务商侧不可能发生**
（它手里根本没有能签的东西）。

换成 **DID 签名的 HTTP 请求**（§15）后：

| 威胁 | 今天 | 换后 |
|---|---|---|
| 数据库/备份泄露 | **全面冒充** | 只泄露 did→地址映射，无签名能力 |
| 服务商内部人冒充用户 | 可行 | 不可行 |
| 网关进程内存被读 | 可拿到 HMAC 密钥 | 只能拿到公钥 |
| `did:key` 轮换 | 换一把 key 即可 | 靠"一个地址绑多个 DID"补偿（加新→切 primary→删旧） |
| seed 泄露影响面 | 只丢接口访问 | 靠"按用途分离两把 DID"压回爆炸半径 |

### 2.5 三层叠加后的整体性质

| 性质 | 由谁保证 |
|---|---|
| **单一身份根**：地址 ↔ DID ↔ agent 是同一条线，而不是四套互不相关的标识 | 邮件层（`EmailBinding`）+ 接口层（`did_bindings`） |
| **两端可独立验证**：签名者与最终消费者之间不依赖中间方的判断 | 设备侧签名 + SDK 复算 |
| **服务商不再是信任根**：它只转发、只做一次地址查询 | §3.4 + §2.4 |
| **失败可归因**：入口拒收必须能证明"这个声称是发件人自己签的" | §7.1 |

### 2.6 明确**不**解决什么（避免过度承诺）

| 不做 | 原因 |
|---|---|
| 不替代 SPF/DKIM/DMARC | 它们证明域授权，本方案证明地址归属，两者正交（§7.4） |
| 不替代 TLS | 签名给的是真实性与完整性，没有机密性 |
| 不解决 `Bcc` 重放 | 收件人哈希只覆盖可见 `To`/`Cc`，SMTP 架构固有限制 |
| 不解决同一 VC 跨次出示的可关联性 | 加盐哈希方案（BBS+ 才解决），已在规范 §7.7 声明 |
| 不解决"收件方留存已披露数据" | 密码学范围之外 |
| 不防"合法中间件改写正文导致的验签失败" | 这类邮件会被 SDK 拒收或标记为不可验证；不做自动豁免（§7.3） |

---

## 3. 两条公理与推论

### 3.1 公理一：签名者必须是内容控制者

要证明"地址控制者此刻在签名"，签名的执行者与内容的决定者必须是同一个人。

推论：**发件服务商、milter、中转 MTA、KMS 都不得参与签名**，连"决定签什么"都不行。

### 3.2 公理二：哈希只能由验证方自己算

| 类别 | 例子 | 能否来自对方 | 为什么 |
|---|---|---|---|
| **被验证的输入** | subject、裸正文、附件字节、文件名、To/Cc | **能** | 对方撒谎 → 重算不符 → 失败（fail-closed） |
| **被比对的值** | 任何 hash | **不能** | 对方撒谎 → 恰好与我们该相等的目标一致 → 通过（fail-open） |
| **签名覆盖的声称** | 头里的 `bh`/`rh`/`s`/`did`/`t`/`vc` | 能 | 它们本身被签名，改了就验签失败 |

所以头里的 `bh`/`rh` 是安全的（有签名背书），"服务商算好的附件哈希"是不安全的（无人背书）。

### 3.3 推论一：签名对象是"原始内容"，不是 MIME（方案 C）

判据来自公理一：**谁决定签名对象**。

| 方案 | 做法 | 客户端能独立算出吗 | 判定 |
|---|---|---|---|
| A 两阶段冻结 | `/send/prepare` 冻结最终 MIME，摘要回给客户端签 | **不能**——摘要由服务端算 | **淘汰** |
| B 客户端提交完整 raw MIME | 客户端自造 MIME，服务商原样转发 | 能 | 合格，成本约 C 的 3 倍 |
| C 原始内容自签名 | 对 `Content-v1` 签名，与 MIME 无关 | 能 | **采纳** |

A 淘汰的关键：摘要不可伪造，但**"哪些内容会被签"由服务端决定**——服务商只要能选择被签的载荷，
就恢复了"代表用户"的能力。**A 是"看起来端到端、实际不是"的方案。**

C 的成立条件由 §4.1（`text/plain` = 存储正文逐字节原样）与 §4.3（附件逐字节可用）保证；
附带收益是把 QP/base64/随机 boundary/行折叠这些 MIME 规范化雷区整体消除。

### 3.4 推论二：gateway 停止三件事

| 停止什么 | 为什么 | 替代做法 |
|---|---|---|
| 追加 agent 签名到正文尾部 | 客户端签的正文与投递的正文必然不同 | 尾部移到客户端拼（§8.4） |
| 改写 To/Cc 头 | 接收方算出的 `rh` 与客户端签的对不上 | 白名单照旧，但**拒绝投递**被过滤地址（§8.5） |
| 参与签名 | 违反公理一 | 只转发、只做一次地址查询 |

### 3.5 推论三：收件方必须独立复验

```text
签名者（agent SDK）──→ 发件服务商（只转发）──→ 接收方 MTA（只查地址）──→ 收件 agent 的 SDK（复算完整性 + 裁决）
        签                        不改不签                    一次链查询                不过即拒收
```

只做前半段而让 agent 无条件接收，等于把验证结果丢在地上。

---

## 4. 事实基线：代码如何约束设计

每条事实后面紧跟它约束了哪个设计点。

### 4.1 正文：`record.body` 是唯一原始内容，MIME 是派生的

现行 `send.rs:100` 给正文尾部追加 `"\n\n-- \n" + meta.agent_signature`，入库为 `record.body`。
投递时（`sender.rs:112-118`）：

```rust
let html_body  = markdown_to_html(&record.body);   // HTML 只是派生视图
let plain_body = record.body.clone();              // ★ text/plain = 存储正文逐字节原样
```

→ 接收方解码 `text/plain` 即可完整还原签名对象（§3.3）。

### 4.2 收件人会在投递前被过滤与重写

`send.rs:745-771`：最终 To/Cc 由 `external ∪ final_internal ∪ system_sink` **按白名单/board 过滤后的桶**
重拼（注释明写 "NOT from to_set"），显示名被重写；被过滤的收件人不会出现。
SMTP 信封 = 仅外部收件人，内部走 webhook。

→ 白名单必须保留，但不能以"改写头"实现（§8.5）。

### 4.3 附件逐字节落盘，可按引用取回

`factory.rs:889` `tokio::fs::write(&file_path, content)`——上传即原样存储；
`GET /api/v1/attachments/{id}`（`files.rs:142`）可取回原始字节。

→ **系统不保存附件哈希，也不需要保存**：两端各自算（公理二）。

### 4.4 出站自定义头走白名单；DKIM 挂在 transform 上

`OUTBOUND_PASSTHROUGH_HEADERS`（`mx_deliverer.rs:26`，`sender.rs:329` 复用）当前只放行四个头。
`OutboundTransform::transform_or_passthrough`（`strategy.rs:12`）在 `formatted()` 之后、发送之前跑。

→ `DID-Signature` 加入白名单即可透传；advanced 的 DKIM 实现只前置头（§4.11）。

### 4.5 入站：只有 `data_end` 一个注入点；DSN 会提前返回

`receiver.rs:433 data_end` → `check_deferred_whitelist` → `process_data_end_post`；
`:590` 取 `raw_data` → `mailparse` → 入库。
**但 `process_data_end_post` 里 `MAIL FROM:<>` 的退信/NDR 分支会在 MIME 解析前 return。**
记录头只捕获 `to`/`cc`/`from` 与 `x-aimail-*`（`receiver.rs:893-903`）。
`ConnectionHandler` 已用 `block_on` 同步阻塞。

→ 验证必须插在退信分支**之后**、MIME 解析之前；DSN 不参与验证。

### 4.6 入站载荷的四个事实（三缺一对）

| # | 事实 | 影响 |
|---|---|---|
| ① | 入站 `record.body` **就是**发端正文的逐字节拷贝（`factory.rs:576-585`：plain 覆盖 html、不 trim、不剥引用） | 完整性可在收件侧重算 |
| ② | 但下发的 `payload["body"]` 是 `bodyproc` 处理后的（`records.rs:110-123`） | SDK 拿不到被签的那段，需新增 `body_signed` |
| ③ | 附件元数据没有哈希（`AttachmentMetaRecord`） | 这是**正确的**；SDK 需自己下载并算 |
| ④ | 记录里的 To/Cc 是**过滤后**的（`receiver.rs:893-903` 的 `contains_key` 让先写入者生效） | `rh` 需按原始头重算，需新增 `rcpt_*_raw` |

### 4.7 did-sdk：能力、边界与一个易误判点

- 有：`did:key` 生成/离线解析、Ed25519、VC 钱包（ChaCha20-Poly1305 `DVCS`）、`verify_vc`/`verify_vp`、选择性披露。
- **无链访问**（R1）：不做锚定查询、不持链账户私钥。
- 签名确认：`core.rs:48-57` `confirm_challenge`——装了 handler 才问，**没装就 `None => Ok(())`**；
  Python/Node 绑定都没暴露 `set_request_handler`。→ 无人值守签名今天就能用，"确认"上移（§8.6）。
- 已有但从未被生产代码使用的签名对象：`GatewayAccess-v1`（§15.2）。

### 4.8 chain-sdk：能力边界，以及它"不算哈希"

- `ChainSdk`：`get_anchor` / `create_anchor(anchor_type, payload_hash: &str, valid_until)` /
  `renew_anchor` / `revoke_anchor` / `resolve_did_key`（离线）/ `subscribe_event`。
- `AnchorType` 已含 `EmailBinding`（`types.rs:134`，9 个值）。
- `create_anchor` 收**字符串**：chain-sdk 从不自己算哈希（测试里直接写 `"blake2b:deadbeef"`），
  唯一校验点是 `sd.rs:352` 的 `verify_sd_presentation`，只覆盖 VC。
- 模块头把这件事写成约定：`sd.rs:13` "anchor-layer BLAKE2b convention"。
  **9 种类型里只有 `IssuedVc` 有实现，其余 8 种零实现**（§8.1）。
- **`revoke_anchor` / `renew_anchor` 门禁只有 `ensure_issuer()`**（`mock.rs:484`），
  不检查"是不是你建的"——归属校验只能由服务端做（§8.9）。

### 4.9 各仓发布现状

| 项 | 实测 |
|---|---|
| PyPI `did-sdk-client` / npm `@did-sdk/client` | **404，未发布** |
| CI 构建 | 5 个 target 成功，但只挂 GitHub Release 附件 |
| Python wheel | `-i python3.12`、无 abi3 → **只覆盖 3.12** |
| 缺失 target | `x86_64-apple-darwin`、`aarch64-unknown-linux-gnu`、Node 侧对应 triple |
| **实测一** | pyo3 加 `abi3-py39` 后 `cargo check -p did-sdk-python` 通过（23.7s）→ 一个 `cp39-abi3` 覆盖 3.9+，共 7 个 wheel |
| **实测二** | 全部 `Cargo.toml` 无 `openssl`/`native-tls`/`-sys` → 只需区分 arch + OS + libc |

### 4.10 平台适配机制

- npm 原生 `os`/`cpu`/`libc`；napi-rs 标准做法是 meta 包 + `optionalDependencies`；
- **N-API ABI 稳定**：一个 `.node` 覆盖 Node ≥ 18；
- did-sdk 的 `bindings/node/index.js` 已是标准 napi-rs loader，分包不用改代码。

### 4.11 advanced 版的介入点

`aimail-advanced` 是独立 crate，以 `aimail-gateway = { path = "../aimail-gateway" }` 依赖 base 库。

| 环节 | advanced 的做法 | 与 DID 的关系 |
|---|---|---|
| 出站字节 | `dkim::sign_dkim()`：**先拼 DKIM-Signature 头、再原样追加 `raw`**（`src/advanced/smtp/dkim.rs`） | **正文零改动** → `bh` 不受影响 |
| 入站顺序 | `data_end`：① 延迟白名单 → ② 每系统限流 → ③ **DKIM/DMARC 闸门** → ④ 附件限额 → ⑤ `core.process_data_end_post(...)` | 我们的 DID 检查在 ⑤ 内 ⇒ 排在 DKIM/DMARC **之后** |
| 入站改写 | 无。只返回裁决，**不注入 `Authentication-Results`**、不改正文 | `DID-Auth-Results` 是本生态第一个结论头 |
| 配置 | `AdvancedConfig` 与 base `Config` 各自解析同一份 TOML，**serde 忽略未知字段**（`src/advanced/config.rs` 注释） | `[did_mail]` 段不破坏 advanced |

---

## 5. 职责矩阵

把"要发生的事"按时间顺序拆成 R1–R42，标清**谁做、输入什么、产出什么、失败怎么办**。
§11 的代码映射**不引入这里没有的职责**；映射时若发现需要新职责，先补本章。

### 5.1 发送链路（客户端 → 服务商 → 外发）R1–R11

| # | 职责 | 执行方 | 输入 | 产出 / 失败语义 |
|---|---|---|---|---|
| R1 | 持有该地址的 `did:key` 私钥（seed） | SDK | `did/<地址>/seed` | 缺失 → 不签名，邮件仍可发（降级） |
| R2 | 持有该地址**有效**的 `EmailBindingVC` | SDK | 绑定审批下发 | 过期/撤销 → 不签名（降级），告警 |
| R3 | 持有 agent 签名文本 | SDK | 指令邮件写入的本地配置 | 为空 → 正文无尾部，不阻塞 |
| R4 | 组装正文 = markdown + `"\n\n-- \n" + 尾部` | SDK | R3、用户输入 | 顺序写死，向量覆盖 |
| R5 | 计算 `Content-v1`（逐附件 `name_hash`/`file_hash`） | SDK | R4、文件名、**本地文件字节** | 对**自己手里的字节**求哈希 |
| R6 | 计算 `rh` | SDK | 提交的 To∪Cc | — |
| R7 | 组七行签名对象并 Ed25519 签名 | SDK（经 did-sdk） | R1、R5、R6、`did`、`t`、`vc` | 失败 → 不签名 |
| R8 | 产出 `DID-Signature` 头并随 `/send` 提交 | SDK | R7 | — |
| R9 | 收件人白名单/board 过滤 | gateway | 既有机制 | **不改写头**；被过滤地址拒绝投递 + 双通道回报（§8.5） |
| R10 | 透传 `DID-Signature` | gateway | R8 | 加入出站头白名单，不剥不签 |
| R11 | DKIM 只加头不改正文 | advanced transform | 原字节 | 改正文 → `error!` 日志 + 文档约束 |

### 5.2 接收链路（MTA 侧，只做地址维度）R12–R18

| # | 职责 | 执行方 | 输入 | 产出 / 失败语义 |
|---|---|---|---|---|
| R12 | 剥离邮件里已有的 `DID-Auth-Results` | gateway | `raw_data` | 外部结论一律不可信 |
| R13 | 解析 `From` 与 `DID-Signature`；多地址逐个处理 | gateway | R12 输出 | 无头 → `did_absent`（**照收**） |
| R14 | 离线解码 `did:key`；格式/时钟窗 | gateway | R13 | `did_unsupported`/`timestamp_out_of_window` → **收 + 标记** |
| R15 | 查锚定 + 用 `(did, from, issuer_did)` 重建 `payload_hash` | gateway（chain-sdk Verifier） | `vc=chain:<id>` | 五个 address reason |
| R16 | **失败路径归因**：仅当 R15 失败时，用头里的 `did/from/t/vc/bh/rh` 重建七行对象做一次 Ed25519 验签（**不碰正文与附件**） | gateway | R15 + 头 | 通过 → 真声称 → **SMTP 拒收**；不通过 → 头被改 → 收 + 标记 |
| R17 | 写 `DID-Auth-Results`（**只含地址维度**） | gateway | R15/R16 | `result=unknown; integrity=delegated; address=…` |
| R18 | 载荷构造：`body_signed`/`rcpt_*_raw`/附件引用/`results`/`gateway_time` | gateway | R17 + `record.body` | 仅 DID 记录追加（§6.5） |

四条约束：**稳态零密码学**（R16 只在准拒收时执行）；**不重算正文/附件**；`chain_unavailable` 不拒收；
**锚定查询防放大**（负缓存 + 限流 + 硬超时 ≤1.5s）。

### 5.3 接收链路（agent 侧，独立复验）R19–R31

| # | 职责 | 执行方 | 输入 | 产出 / 失败语义 |
|---|---|---|---|---|
| R19 | 系统邮件识别并分派（`X-AIMail-System`），先于 DID 裁决 | SDK | 载荷头 | 命中 → 处理并吞掉 |
| R20 | ping/pong 拦截 | SDK | 既有 | 命中 → 吞掉 |
| R21 | 解析头 + 时间窗（以 `gateway_time` 为基准） | SDK | `did.signature` | 对应 reason |
| R22 | 用 `body_signed` 重算 `body_hash` | SDK | R18 | — |
| R23 | **下载附件字节并本地算** `name_hash`/`file_hash` | SDK | `attachments[{id,filename}]` | 取不回 → `not_verifiable` |
| R24 | 组 `Content-v1` 比 `bh` | SDK | R22、R23 | `body_hash_mismatch` |
| R25 | 用 `rcpt_*_raw` 重算 `rh` | SDK | R18 | `rcpt_hash_mismatch` |
| R26 | Ed25519 验签 | SDK（经 did-sdk） | R21–R25 | `signature_invalid` |
| R27 | 地址维度：复用 `results` 或自查锚定 | SDK | `did.results` / `vc` | 两种模式（§7.2） |
| R28 | 地址维度交叉校验（`selfcheck` 时） | SDK | R27 | 不一致 → 告警，**以自己为准** |
| R29 | 裁决 accept / reject / accept_flagged | SDK | R26–R28 + 策略 | §7.2 |
| R30 | 拒收：写审计副本 + 日志事件 + 吞掉 | SDK | R29 | 不进 LLM、不写常规索引 |
| R31 | 放行：常规 preprocess → LLM | SDK | 既有链路 | — |

### 5.4 绑定与发行（低频）R32–R39

| # | 职责 | 执行方 | 输入 | 产出 |
|---|---|---|---|---|
| R32 | 发起绑定：验证码 + DID 挑战 | gateway 发行方端点 | agent 的 DID + 邮箱 | challenge / 验证码 |
| R33 | 校验：验证码 + DID 挑战签名（**复用 `GatewayAccess-v1`**） | gateway | R32 | 通过 / 拒绝 |
| R34 | 创建 / 续期 / 撤销 `DomainControl` 锚定 | gateway（Issuer） | 域名 + 自己的 participant DID | `domain_anchor_id` |
| R35 | DNS TXT 解析 + `DomainControl` 核对（EmailBinding 写入/续期前必做） | gateway | 域 | 控制权成立与否（§6.8） |
| R36 | `create_anchor(EmailBinding, payload_hash, valid_until)` | gateway（chain-sdk Issuer） | R35 | `anchor_id` |
| R37 | 签发 `EmailBindingVC`（含 `credentialStatus.anchorId`） | gateway（did-sdk Server） | R36 | VC |
| R38 | 推送指令邮件：VC + agent 签名文本（**不进 LLM**） | gateway | R37 | 客户端落盘（R1–R3） |
| R39 | 续期 / 撤销 + 推送；写 `did_bindings`（§15.3） | gateway | 运营操作 | 客户端标记失效 |

### 5.5 运维与可观测 R40–R42

| # | 职责 | 执行方 |
|---|---|---|
| R40 | 指标：`did_verified`/`did_failed_integrity`/`did_failed_address`/`did_absent`/`did_chain_error`/`did_not_verifiable`/`did_rejected_at_mta` | gateway |
| R41 | `aimail did status`：本地 VC / 签名 / 与网关比对（按需 GET，**不在发信路径**） | SDK |
| R42 | 审计：拒收副本、地址结论不一致事件 | SDK / gateway |

---

## 6. 字节契约

### 6.1 投影 `Content-v1`

```text
Content-v1（UTF-8，每行以 \n 结尾，含最后一行）

  Content-v1
  {subject}
  sha256:<body_hash>
  {attachment_count}
  {i}\tsha256:<name_hash>\tsha256:<file_hash>
```

| 值 | 定义 |
|---|---|
| `body_hash` | `SHA-256(规范化 body)`：`\r\n|\r → \n`、每行去行尾空白、去末尾空行、末尾补一个 `\n` |
| `name_hash` | `SHA-256(UTF-8 文件名)`——先哈希以消除分隔符歧义 |
| `file_hash` | `SHA-256(附件原始字节)`——**由持有字节的一方各自算** |

`body` **已含 agent 尾部**（R4 先拼、再哈希）。附件行按提交顺序，`{i}` 从 1 连续。

### 6.2 收件人哈希 `rh`

提交的 To ∪ Cc → 去显示名/注释、取 addr-spec、小写、去空 → 字典序排序 → `\n` 拼接 → SHA-256。
`Bcc` 不参与（SMTP 架构限制）。

### 6.3 签名对象与头

签名对象（七行，**每行尾随 `\n` 含末行**）：

```text
DID-Signature-v1
{did}
{from}
{t}
{vc}
{bh}
{rh}
```

头（字段顺序固定，解析接受任意顺序）：

```text
DID-Signature: v=1; did="did:key:z6Mk…"; t=1765144656; s=<base64url(64B)>;
  vc="chain:<anchor_id>"; bh="sha256:<64hex>"; rh="sha256:<64hex>"
```

`bh`/`rh` 必须回显：接收方验签用自己算的值，头里没有声称值就只能报 `signature_invalid`。
**先比对回显得归因，再验签确认**——验签始终权威。实测 **353 字节**（折 5 行）。
解析规则：先 unfold、拒绝 unfold 后 >998 octets、拒绝重复参数、容忍未知参数。

### 6.4 `DID-Auth-Results`

```text
DID-Auth-Results: result=unknown; integrity=delegated; address=verified; did-id="did:key:z6Mk…"
DID-Auth-Results: result=failed; integrity=pass; address=failed; reason=anchor_revoked
```

| 字段 | 取值 |
|---|---|
| `result` | `verified` / `failed` / **`unknown`** |
| `integrity` | `pass` / `fail` / **`delegated`** |
| `address` | `verified` / `failed` / `unknown` |

**本 profile 的分工**：MTA 只查地址，签 `integrity=delegated`（意为"MTA 未验完整性，收件方必须自验"，
**不得读作通过**）；只有收件 SDK 能给出 `integrity=pass|fail` 与最终 `result`。

### 6.5 端到端载荷

只对带 `did-signature` 的记录追加：

```json
{ "signature": "…", "body_signed": "record.body 原文", "rcpt_to_raw": "…", "rcpt_cc_raw": "",
  "attachments": [{ "id": "att_1", "filename": "q3.pdf" }], "gateway_time": 1765144656,
  "results": "result=unknown; integrity=delegated; address=verified; …" }
```

**刻意不含任何 hash**——附件哈希由 SDK 下载字节自己算（公理二）。`body_signed` 上限 1 MiB，超限置 `not_verifiable`。

### 6.6 `EmailBinding` 的 `payload_hash`

```text
payload_hash = "blake2b:" + BLAKE2b-256( "EmailBinding-v1\n{did}\n{email}\n{issuer_did}\n" )
```

四行、每行尾随 `\n`；任一字段含 `\n`/`\r` 必须拒绝；`email` 去空白后小写。
**复用结构，不复用构造**：同一个 `AnchorRecord`，但 preimage 是类型特定的四行明文（承诺对象性质不同）。

### 6.7 `issuer_id` 的语义

| 字段 | 契约原文 | 含义 |
|---|---|---|
| `issuer_did` | semantic issuer = **trust-chain root** | 对这绑定关系负责的一方 ← **preimage 用这个** |
| `submitter_id` | actual submitter participant (governance audit) | 实际提交者，仅供审计 |
| `delegated_by_did` | proxy/delegate DID; `None` for self-submission | 代理方 |

`submitter_id` 淘汰理由要具体：服务商 X 签发后把提交权委托给链主 Y，若 preimage 用 `submitter_id`，
同一条绑定的哈希会在两种提交路径间跳变——**续期后所有已发出的 VC 的 `anchor_id → payload_hash` 全部失配**。

两条推论：验证顺序是"先查锚定、再重建哈希"；**发行方轮换 `controller_did` 不影响旧锚定**（记录冻结创建时的值）。

### 6.8 `DomainControl`

```text
payload_hash = "blake2b:" + BLAKE2b-256( "DomainControl-v1\n{domain}\n{issuer_did}\n" )
```

引导顺序（不能反）：

```text
1. 发行方创建 DomainControl 锚定            → anchor_id
2. 域名所有者发布 TXT: _email-verification.<domain> "ev-anchor=chain:<anchor_id>"
3. 每次写/续 EmailBinding 前，重做 2 的解析与锚定核对
```

**这是一条新的外部运营要求**：目前 gateway **没有任何域名归属的 DNS 验证**（`system_domains` 只是内部表，
`http.rs:599` 的"domain anchor"是内部地址记录）。不做 DomainControl 则绑定退化为"发行方自称"。

### 6.9 原语按层划分

| 层 | 原语 | 为什么 |
|---|---|---|
| **链上承诺**（`AnchorRecord.payload_hash`） | **BLAKE2b-256**，`blake2b:` | 链侧已全是 BLAKE2b（`did:peer:1`、SS58、`IssuedVc`）；同层两算法才是真不一致 |
| **邮件内摘要**（`bh`/`rh`/`name_hash`/`file_hash`） | **SHA-256**，`sha256:` | 永不落链，与 DKIM/TLS 同族，第三方零额外依赖 |

注意 `BLAKE2b-256` **不是** 512 截断、**不是** `BLAKE2s-256`：摘要长度是参数。

### 6.10 错误码

`did_absent` `did_unsupported` `signature_invalid` `body_hash_mismatch` `rcpt_hash_mismatch`
`timestamp_out_of_window` `anchor_not_found` `anchor_type_mismatch` `anchor_hash_mismatch`
`anchor_revoked` `anchor_expired` `chain_unavailable` `attachment_fetch_failed` `not_verifiable`

发送侧错误：`did_unavailable`（未装 extra）、`did_rcpt_filtered`（收件人会被过滤）、
`domain_control_required`（缺 DomainControl）。

---

## 7. 拒绝与裁决

### 7.1 入口（MTA）级：只做地址维度，且拒收必须可归因

| 入口结论 | 处置 |
|---|---|
| 锚定成立 | 放行，`address=verified` |
| 锚定不存在／类型不符／哈希不符／已撤销／已过期，**且 R16 归因通过** | **SMTP 拒收（550）** |
| 同上，但 R16 归因**不通过**（头被改过） | 放行 + 标记 |
| `chain_unavailable` / 超时 | 放行 + 标记 |
| 头格式非法 / 越窗 / 无头 | 放行 + 标记（**永不拒收**） |

**为什么拒收要经过 R16**：不验完整性就无从区分"发件人真的用了失效锚定"与"中转方改掉了 `vc`"。
不归因就拒收，等于给中间人一个"让任意合法邮件被拒"的开关。

**为什么"签名失败"类缺陷不在入口拒收**：同样的归因问题——合法中间件改写正文也会导致验签失败，
据此拒收就是误伤。这类缺陷由 SDK 拒收（§7.2），代价是它先占了一点存储。

### 7.2 收件 SDK 级：完整性 + 地址维度组合

关键区分：**"没签名"与"签名验不过"是两件事**。前者是兼容性，后者是安全性。

| 情形 | 判定 | 处置 |
|---|---|---|
| 外部客户端不支持 DID | `did_absent` | **照常收** |
| MTA 只给地址结论（`integrity=delegated`） | 正常态 | **SDK 自己算完整性，不得读作通过** |
| `did` 非 `did:key` / 格式非法 | `did_unsupported` | 照常收 + 告警 |
| SDK 独立验签失败 | `signature_invalid`/`body_hash_mismatch`/`rcpt_hash_mismatch`/`timestamp_out_of_window` | **拒收** |
| 锚定基础设施不可用 | `chain_unavailable` | 照常收 + 标记 |
| 锚定判定失败 | `anchor_not_found`/`_type_mismatch`/`_hash_mismatch`/`_revoked`/`_expired` | **拒收** |
| 附件字节取不回 | `attachment_fetch_failed` | 照常收 + 标记（严格模式可拒） |
| 载荷缺 `body_signed` | `not_verifiable` | 照常收 + 告警 |
| 全部通过 | `verified` | 收 |

**"拒收"语义**：不进 LLM、不写常规 inbound 索引；落审计副本（`did/rejected/<mail_id>.json`）+ 日志事件。
可选回信给发件方（默认关，避免被用作探测/放大）。

**地址维度两种模式**：`delegated`（默认，复用 `results`，零网络）/ `selfcheck`（经 gateway 代理查锚定，一次网络）。
两者都不因 `chain_unavailable` 拒收。

### 7.3 不可独立复验时的降级

| 原因 | 处置 |
|---|---|
| 载荷缺字段（旧 gateway） | `not_verifiable`：照常收 + 告警 |
| `body_signed` 超长（>1 MiB） | 同上——**不能改成"下发哈希"**，那又变成让服务商出题 |
| 附件取不回 | `attachment_fetch_failed`：收 + 标记 |
| 未装 `did` extra | 策略退化 `record`（只记不拦） |

**`not_verifiable` 永不等于 `verified`。**

### 7.4 与 advanced 的 DKIM/DMARC 的次序

入站顺序固定：限流 → **DKIM/DMARC 闸门** → 附件限额 → **DID 地址检查** → 入库。

**DID 是叠加层，不是豁免层**：不允许"已验证 DID"绕过 DKIM/DMARC 拒绝。理由是域级信任在语义上先于地址级，
且开豁免通道等于给出一条绕过域策略的路径。真有业务需要就做按对端白名单，不做默认行为。

要写进文档的一句话：**DID 不替代 SPF/DKIM/DMARC；它回答的是它们答不出的那个问题。**

### 7.5 收件侧管线顺序（pysdk 与 tssdk 必须一致）

```text
1. 系统邮件识别（X-AIMail-System / __aimail_system__）→ 处理并吞掉
   └ 豁免 DID 裁决（这类邮件由 gateway 发出，没有 agent 私钥）
2. ping / pong 拦截 → 吞掉
3. DID 独立验签 + 裁决 → 拒收（留审计副本，吞掉）或放行
4. 常规 preprocess → 交给 LLM
```

顺序不能调换：系统邮件放行到第 3 步会被当作 `did_absent`（污染裁决统计）；
DID 裁决若晚于第 4 步，拒收就成了"已经进过 LLM 再拦截"，等于没拦。

---

## 8. 关键决策与理由

### 8.1 锚点层算法：BLAKE2b，且只有 `IssuedVc` 有实现

- 全仓只有 `did_sdk::anchor_payload_hash`（`blake2b` over VC JSON），chain-sdk 直接委托它；
- 其余 8 种 `AnchorType` **零实现**，`payload_hash` 对它是不透明字符串；
- `sd.rs:13` 把这件事写成"anchor-layer BLAKE2b convention"。

所以 `EmailBinding` 用 `blake2b:` 是**跟随锚点层既成约定**，不是新造；邮件层继续 SHA-256（§6.9）。

### 8.2 字节约定：为什么以规范为准、让 did-sdk 适配

did-sdk 的 `signing_input` **11 个函数全部**用 `"…\n{末字段}"`（无尾随换行），并有
`assert!(!bytes.ends_with(b"\n"))` 守护；规范统一"每行 `\n` 结尾（含最后一行）"。

| 维度 | 尾随 `\n`（规范） | 无尾随（did-sdk 现状） |
|---|---|---|
| 密码学强度 | 等价 | 等价 |
| 字段自定界 | 每字段完整一行，可 `writeln!` 流式写 | 末字段隐含"不能拼接" |
| 实现错误面 | 小，无特判 | 大——漏/多一个 `\n` 静默失配 |
| 现有消费者 | 无 | **零**（未发布、旧 API 只在自测用） |

**结论**：规范更优且改造成本≈0 → did-sdk 做 v0.3.0 新增，旧格式保留并标 deprecated。

### 8.3 附件：先各自求哈希

若直接拼 `name ‖ 0x00 ‖ bytes`，含 `\x00` 的文件名可构造碰撞。各字段先哈希后每行固定三段形状，无法注入；
`file_hash` 还成为独立可复用的值（审计/跨系统比对）。

### 8.4 agent 签名归属：整体移到客户端

（与是否用 DID 无关，顺带修正"服务端代拼署名"的旧范式）

| 项 | 现状 | 改为 |
|---|---|---|
| 正文尾部拼接 | `send.rs:100` 服务端 | **SDK 拼**（R4） |
| 值的存储 | `domain_addr_meta.agent_signature` | **客户端** `did/<地址>/agent-meta.json` |
| 值的下发 | 不需要 | 指令邮件（R38） |
| 对外暴露 | `whoami.rs:51` 返回、`http.rs:1397` 可写 | 两者下线该字段 |

**迁移必须分三步**：① 发 SDK + 服务端临时幂等补丁（正文已以 `"\n\n-- \n"` 结尾则跳过）→ ② 删除服务端拼接与存储
→ ③ 下线 `whoami`/`agent-meta` 的字段。破坏性影响：非 SDK 的直接 API 调用方不再自动获得署名尾部。

### 8.5 白名单：保留机制，拒绝投递，不改写头

- 白名单/board 过滤**完全保留**；To/Cc 头保持客户端提交原样；对被过滤地址**拒绝投递**（不进信封、不进 webhook）。
- 边界：判定基准是最终 To/Cc 头的 addr-spec 集合；**显示名归一化允许**（`rh` 只覆盖 addr-spec）；persona 展开无损。
- **补闭环**：`/send` 响应 `undelivered: [...]`（同步）+ 一封 `X-AIMail-System: did-undelivered` 系统邮件（durable）。
  系统邮件必须被 SDK 吞掉、不进 LLM，且三条约束：豁免 DID 裁决、不受白名单影响、不进 outbound 队列。

### 8.6 确认模型：一次审批，长期可签

安全员审批的是**绑定本身**（地址 ↔ DID ↔ agent 身份，一对一），等价于签发身份确认 VC；
之后该地址发信只要符合系统规约即自动签名。不要引入"有效期内的 did_scope"这种时间窗机制。

对外话术：「该地址的 DID 身份经安全员审批」——**不能**说"每封邮件人工确认"。

签名前置条件：本地有该地址**有效**的 `EmailBindingVC` + seed（+ 可选的 agent 签名文本）。

### 8.7 实现载体：编译产物而非三语言重写

判据是**漂移面 × 生命周期**。契约植入的最小集合不大（Ed25519/SHA-256/base58 是冻结标准），
但**会变的部分**（投影、头、附件规则）最容易静默漂移；而 VC 语义等另外 90% 必然回到 did-sdk，届时边界失控。

依赖形态：基础包仍零依赖，`did` 作为可选 extra；未装时 `send_mail` 照常工作，`sign=True` 返回 `did_unavailable`。

### 8.8 多 `From`：不支持签名，但必须逐地址判定

aimail 结构上产生不了多地址（`send.rs:78-95` 强制 `sender_base == api_key.email_address`）。
但**接收侧必须逐地址严格判定**：`From` 有 N 个地址、只有 M<N 个头时，缺头者 `did_absent`，
整封不得为 `verified`——十几行代码的安全必需项。

### 8.9 两个必须的工程约束

**① 类型与 preimage 必须人工配对**：`create_anchor` 的类型是调用方传的标签。若把 `AnchorType::EmailBinding`
传给 `issue_sd_credential`，chain-sdk 会老实存下 `blake2b(VC JSON)`，而按行式 preimage 重建的接收方**永久**失配。
对策：我方是这两种类型的唯一写入方 + 写入前校验已 `register_anchor_type` + 编译期绑定枚举。

**② SDK 不校验锚定归属**：`revoke_anchor` 门禁只有 `ensure_issuer()`（`mock.rs:484`）——任何已注册 Issuer
都能吊销任意 `anchor_id`。中央化锚定服务**必须**自行校验 `anchor.issuer_did == 请求者`。
这也是"`IssuedVc` 必须由 issuer 锚、不能由持有方锚"的真实原因：谁锚，谁握有吊销权。

### 8.10 本期锚定范围

| 类型 | 谁实现 | 本期 |
|---|---|---|
| `EmailBinding` | gateway（Issuer） | **必做** |
| `DomainControl` | gateway（Issuer）＋ 域名所有者发 TXT | **必做** |
| `IssuedVc` | 签发 VC 的一方 | **不做**（触发条件：需要选择性披露） |
| `GatewayAccess` | gateway | **只用签名对象，不上链**（§15.2） |
| `DidDocument` | 身份链 / DID 注册服务 | 不需要（全程 `did:key`） |
| `Warrant` / `OrgMembership` | 相应机构 | 不需要 |
| `Delegation` | chain-owner-sdk | 不需要（间接用到） |
| `Custom` | 链主 | 不使用 |

---

## 9. 不变量（评审与测试的唯一打勾清单）

每条都是可判定的断言，不是描述。

### 9.1 字节层

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N1** | 签名对象七行，每行（含末行）以 `\n` 结尾 | 向量 `bytes_hex` 以 `0a` 结尾；`len == Σ(line)+7` |
| **N2** | `Content-v1` 首行恒为 `Content-v1` | 向量 |
| **N3** | `body` 已含 agent 尾部，顺序是先拼尾、再哈希 | 向量带"含/不含尾部"两例 |
| **N4** | 附件条目数 == `attachment_count`，`{i}` 从 1 连续 | 向量（0/1/2 附件） |
| **N5** | 头解析：先 unfold；≤998 octets；拒绝重复参数；容忍未知参数 | 单测（超长/重复/未知） |
| **N6** | `bh`/`rh` 回显与签名对象第 6/7 行**逐字节同源** | 单测断言同源（不许二次计算） |
| **N7** | `rh` 大小写不敏感 | 向量（混合大小写） |
| **N8** | 改任一头字段 → 验签失败；改回显 → 归因为 `*_hash_mismatch` | 单测（逐字段篡改矩阵） |

### 9.2 信任边界

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N9** | 进入比对环节的 hash 一律验证方本地计算；**载荷里不出现 hash** | 代码审查 + 载荷 schema 断言 |
| **N10** | 收件方不采信 MTA 结论里的任何完整性含义；地址维度可用作交叉校验 | 单测：篡改 `results` 不影响 SDK 自己的判定 |
| **N11** | 缺字段/附件取不回/超长 → `not_verifiable`，**永不等于 `verified`** | 单测（三种降级各一例） |
| **N12** | 系统邮件只接受来自本地配置的网关地址 | 单测：伪造 from 的系统邮件被忽略 |

### 9.3 生命周期

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N13** | 锚定 `valid_until` 是有效期权威；VC 的 `expirationDate` 仅客户端约束 | 单测（冲突时以锚定为准） |
| **N14** | 撤销即时生效，不受 `valid_until` 约束 | 单测 |
| **N15** | 事实锚不可替换（`credentialStatus.anchorId` 在 VC 哈希内） | 单测（改 anchorId → VC 哈希变） |
| **N16** | 服务端拒绝 `revoke` 非本发行方的锚定 | 服务契约测试（不在 chain-sdk 能力内） |

### 9.4 投递、路由与入口

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N17** | To/Cc 头不改写；被过滤地址拒绝投递并双通道回报 | e2e（`undelivered` + 通知邮件） |
| **N18** | `DID-Signature` 不剥不改；入口必剥外部 `DID-Auth-Results` | e2e（预置伪造结论） |
| **N19** | 多 `From` 逐地址判定，缺头者 `did_absent`，整封不得 `verified` | 单测 + 向量 |
| **N20** | DSN / `MAIL FROM:<>` 不参与验证 | 单测（退信路径不产生结论头） |
| **N21** | 完整性**只有 SDK 一处实现**：MTA 从不计算 `Content-v1`；`integrity=delegated` 不得读作通过 | 代码审查：MTA 侧无正文/附件哈希调用；单测：`delegated` 时 SDK 必须自算 |
| **N22** | 入口只在"锚定失败 + 归因通过"时拒收；其余一律放行 | 单测：伪造 `vc` 的邮件**不被拒收**（只标记） |
| **N23** | 锚定查询有负缓存、限流与硬超时，超时按 `chain_unavailable` 放行 | 单测 + 压测（随机 anchor_id 洪泛） |

---

## 10. 缺口与补齐记录

按职责走查时发现、并已写进上面各章的问题。

| # | 缺口 | 影响 | 落在哪里 |
|---|---|---|---|
| 1 | **时钟偏差**：MTA 与 SDK 都校验 ±300s，但两侧时钟不同源 | agent 主机漂 5 分钟就"网关过、SDK 拒" | 载荷带 `gateway_time`，SDK 以它为基准（R21、§6.5） |
| 2 | **可伪造的系统邮件**：任何人能伪造 `X-AIMail-System` | 攻击者可改本地 agent 签名或诱使停签 | 只接受来自本地网关地址 + 必须经 MTA 入口 + 内容最小化（N12） |
| 3 | **多地址 → 每地址一把密钥** | 单文件布局会互相覆盖 | `did/<地址>/{seed,agent-meta.json,vc/}`（§11.4） |
| 4 | **两个有效期** | 冲突时判断不一致 | 锚定 `valid_until` 是权威（N13） |
| 5 | **DSN 提前返回** | 退信被误判成 `did_absent` | 验证插在退信分支之后（N20） |
| 6 | **`did:key` 无轮换** | 私钥丢失＝换身份 | 必须写进用户文档；HTTP 层用"多 DID 绑定"补偿（§15.3） |
| 7 | **锚定查询放大** | 随机 `anchor_id` 洪泛打爆锚定服务 | 负缓存 + 限流 + 硬超时（N23） |
| 8 | **拒收不可归因** | 中间人改 `vc` 即可让合法邮件被拒 | 入口拒收必须过 R16（N22、§7.1） |

边界情形清单（进向量）：0 附件 / 空正文 / 只有 HTML 无 plain / 超长 subject / CRLF 与 LF 混用 /
文件名含 `\x00` 与换行 / QP 与 base64 两种编码的同内容 / 附件被重排 / 附件被改名 / 多 `From` 部分缺头 /
载荷缺 `body_signed`。

---

## 11. 组件边界与代码映射

### 11.1 组件边界：仓内独立 crate（已定）

| 层 | 归属 | 内容 |
|---|---|---|
| 邮件层（跨语言） | **did-sdk v0.3.0** | `Content-v1`、头编解码、签名/验签、`GatewayAccess-v1` |
| 锚点层（Rust only） | **`crates/mail-anchor`**（gateway 仓内 workspace member） | `EmailBinding`/`DomainControl` preimage、DNS TXT 解析、生命周期、VC 声明 |
| 接线层 | **gateway / advanced** | HTTP、SMTP、SQLite、feature 门控、指标 |

**为什么必须独立**：放进 did-sdk 违反 R1（无链访问）；放进 chain-sdk 违反它的"不含 VC 存储/治理"边界；
埋在 gateway 里不可复用、难单测。

**P0 已落地**：`protocol/DID-SIGNATURE-PROTOCOL.md`、`protocol/vectors/`、`crates/mail-anchor`
（三个 trait + preimage + 生命周期，32 个测试）、`[workspace]` + `did-mail` feature 占位。

### 11.2 did-sdk（v0.3.0）

| 职责 | 落点 |
|---|---|
| R5–R8 的密码学与编码 | `keyvault-core::signing_input::did_signature_mail_v1` + `Content-v1` 组装 + 头编解码 + Ed25519 |
| R1 密钥 | 已有 `FileKeystore`，加"按地址分目录"封装 |
| R2 VC 钱包 | 已有 `DVCS` 信封 |
| R21–R26 验签原语 | 新增 `verify_mail_signature(...)`（gateway 与 SDK 共用同一实现） |
| 发布工程 | `abi3-py39`；补 3 个 target；PyPI + npm publish |

### 11.3 aimail-gateway

| 职责 | 落点 |
|---|---|
| R12–R18 | 新模块 `src/core/did/{mod,signature,results}.rs`（**不含** `content.rs`——完整性在 did-sdk）；接在 `process_data_end_post` 的退信分支之后、MIME 解析之前 |
| R15/R16 | 调 `crates/mail-anchor` 的 preimage + `ChainSdk`(Verifier) 的 `get_anchor`；TTL 缓存 + 剩余 <24h 穿透 |
| R16 归因验签 | 调 did-sdk 的 `signing_input` + 验签（**不碰正文**） |
| R17/R18 | `results.rs` 编解码 + `records.rs:110-140` 增 `did` 对象 |
| R13 原始 To/Cc | `receiver.rs:893-903` 增保留未过滤的 `to`/`cc` 原始头 |
| R9 | `send.rs:745-771`：不改写头；被过滤地址拒绝投递 + `undelivered` 回报 |
| R10 | `mx_deliverer.rs:26` 白名单加 `DID-Signature` |
| R32–R39 | 新 `src/core/api/did_binding.rs`；业务逻辑调 `crates/mail-anchor`；**`did_bindings` 表在此写入**（§15.3） |
| R40 | `Metrics` 增七个计数器 |
| feature | `did-mail = ["dep:chain-sdk", "dep:did-sdk"]`（P0 只有空占位） |

P0 已落地的配置段：

```toml
[did_mail]
enabled = false
policy = "record_only"              # off | record_only | reject_failed
anchor_query_timeout_ms = 1500
cache_ttl_secs = 300
clock_skew_secs = 300
max_anchor_validity_days = 180
[did_mail.centralized]
endpoint = "http://127.0.0.1:8080"
```

### 11.4 pysdk

| 职责 | 落点 |
|---|---|
| R1–R8 | 新 `pysdk/aimail_did.py`（薄）：惰性导入 `did_sdk`、`build_header()`、`DidWallet`（按地址目录） |
| R5 附件哈希 | 对**本地文件字节**求哈希 |
| R19–R31 | `aimail_base.py` 入站管线顺序（§7.5） |
| R23 附件下载 | 复用已有 `GatewayClient.download_attachment()`（`aimail_tools.py:355`） |
| R41 | `aimail did status` |
| 打包 | `[project.optional-dependencies] did = [...]`；基础 `dependencies = []` 不变 |

**P0 已落地**：`aimail_did.py`（协议常量 + 错误码，仅标准库）、`tests/sdk/test_did_constants.py`。

### 11.5 tssdk

| 职责 | 落点 |
|---|---|
| R1–R8 | 新 `core/did-signature.ts`、`core/did-wallet.ts`（薄，动态 `import('@did-sdk/client')`） |
| R23 附件下载 | **新增** `GatewayClient.downloadAttachment(id)`（当前缺失） |
| R19–R31 | `core/preprocess.ts` 三级管线 |
| R41 | CLI/工具层状态命令 |
| 打包 | `optionalDependencies`；`dependencies: {}` 不变 |

**P0 已落地**：`src/core/did-constants.ts`、`test/core/did-constants.test.ts`。

### 11.6 aimail-advanced

| 职责 | 落点 |
|---|---|
| 继承 DID | `Cargo.toml` 的 base 依赖加 `features = ["did-mail"]`（**不加就是静默缺失**） |
| R11 | `OutboundTransform` 已经只前置头（`dkim.rs`）→ **无需改动**，但要加正文一致性断言 |
| 次序 | DID 检查天然在 DKIM/DMARC 之后（§4.11、§7.4）→ 无需改动 |
| 配置 | `[did_mail]` 段对 advanced 透明（serde 忽略未知字段）→ 无需改动 |
| 验收 | base 与 advanced **两个仓**都要过 `cargo build --release --features did-mail`（含 musl 静态） |

---

## 12. 分期、门禁、回滚

| 期 | 内容 | 影响面 | 回滚 | 验收 |
|---|---|---|---|---|
| **P0** 契约 | 契约文档 + 向量 + 镜像 + `crates/mail-anchor` 骨架 + `did-mail` feature 占位 | 无 | 删文件 | ✅ 已落地：32 个 crate 测试、三端读通向量 |
| **P1** did-sdk v0.3.0 | abi3、3 个 target、PyPI、npm、`Content-v1` + 新头 | 独立仓 | tag 回退 | 7 平台可装；向量全绿；依赖树无 `subxt` |
| **P2** 入口地址检查 | R12–R18（`policy=record_only`）；原始 To/Cc 保留；负缓存/限流/超时 | 入站头多一行 | `enabled=false` | N1–N8、N20、N22、N23；锚定六 reason |
| **P3** 地址真实性接真链 | R15 接 `Centralized` + 缓存 + 穿透 | 每封多一次 HTTP（≤1.5s） | 同上 | 六个 address reason + 缓存命中/穿透 |
| **P4** 发件侧 | R4–R11；载荷 `did` 对象；`undelivered` + 通知邮件 | 出站组装、收件人回报、入站载荷均变 | `sign=False`；幂等补丁可留 | 端到端：SDK 签 → 纯转发 → **SDK 自己验出 verified** |
| **P5** 移除服务端签名 | 删 `send.rs:100`、下线存储字段与 `whoami`/`agent-meta` | **破坏性** | 保留列与逻辑 | 双尾部/空尾部回归；非 SDK 调用方文档 |
| **P6** 收件侧独立验签 | R19–R31（先 `record` 再 `enforce`）、R28 地址交叉校验、时钟基准 | agent 可能拒收（默认 `enforce`） | 策略切 `record`/`off` | N9–N12、N21；§7.2 全情形；**拒收必须由 SDK 自身验签触发** |
| **P7** 发行侧 | R32–R39，含 `DomainControl` 与 DNS TXT；“发 TXT”列为接入必做步 | 仅新增管理端点 + 运营要求 | 端点级移除 | 绑定→`DomainControl`→`EmailBinding`→VC→落盘→发信 verified；缺 TXT 明确失败 |
| **P8** 策略与运营 | `policy=reject_failed`、灰度、看板 | 可拒绝投递（默认关） | 配置回退 | 灰度开关 + 指标快照 |

**每期门禁**：gateway `fmt`/`clippy -D warnings`/`cargo test`（无 feature 零回归）/
`cargo test --features did-mail`/`cargo build --release --features did-mail`（**base 与 advanced 各一次**）；
pysdk `pytest` + `dependencies = []` 不得变；tssdk `pnpm test`/`pnpm build` + `dependencies: {}` 不得变；
did-sdk `fmt`/`clippy`/`test --locked --all-features`。

**首个嵌入者门禁**（aimail 是 did-sdk 第一个使用者）：CI 只从 registry 装
（`pip install agent-aimail[did]` / `npm i agent-aimail`），跑签名回路 + 7 平台 import/require 冒烟；
不装 extra 时必须报 `did_unavailable`，不许静默降级。

---

## 13. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| P1 拖延 | P4/P6 无法落地 | P1 独立可交付；P2/P3 不依赖它 |
| **P5 破坏性变更** | 非 SDK 调用方失去自动署名尾部 | 三步迁移（§8.4） |
| **anchor 洪泛** | 打爆锚定服务 | 负缓存 + 限流 + 硬超时（N23） |
| **不可归因的拒收** | 中间人让合法邮件被拒 | 拒收必须过 R16（N22） |
| **advanced 未开 feature** | 静默缺失 DID | 显式 feature + CI 矩阵 + 启动日志打印实际值 |
| 时钟偏差 | SDK 误拒 | 以 `gateway_time` 为基准 + 偏差告警 |
| 附件被中间改写/重排/改名 | 验签失败（期望行为） | 契约写明"改附件即视为篡改"；reason 带附件维度 |
| 合法中间件改写正文 | SDK 拒收或 `not_verifiable` | 不做自动豁免；对外说明"这类场景本协议无解" |
| 收件侧误判 | 合法邮件被拒 | 先 `record` 灰度；`chain_unavailable`/取附件失败不拒 |
| 附件下载延迟 | 大附件拖慢预处理 | 正文先验、附件后验 |
| `body_signed` 体积/隐私 | 载荷带被剥掉的引用历史 | 上限 1 MiB；不进 LLM；不改发哈希 |
| 多地址只验一个就整体通过 | 伪造邮件被误判已验证 | N19 列为 P2 必测 |
| 完整性实现漂移 | 判断不一致 | B′ 下 MTA 不算完整性 ⇒ 只有一处实现（N21） |
| 跨仓版本锁 | 升级需两侧同步 | 写进 `scripts/check-versions.sh` |

---

## 14. 对规范的修订清单

| 规范条款 | 现状 | 需要改成 | 依据 |
|---|---|---|---|
| §2.1 字段表 | 只有 `v/did/t/s/vc` | 增 `bh`、`rh` | §6.3 |
| §2.3 正文哈希 | DKIM simple/relaxed 规范化 MIME 正文 | `Content-v1` 的 `body_hash` | §6.1 |
| §2.3 附件 | **未覆盖** | 增附件条款：各字段先求哈希再入投影 | §6.1 |
| §2.4 收件人哈希 | 取自 SMTP 会话 `RCPT TO` | 取自客户端提交的可见 To∪Cc | §6.2 |
| §2.2 签名对象 | 七行，尾随 `\n` | **不变**（did-sdk 适配） | §8.2 |
| §5.3 reason 表 | 完整性项依赖头里不存在的值 | 增补 `bh`/`rh` 后自洽 | §6.3 |
| §6 3.i–3.k 顺序 | 先验签再比对第 6/7 行 | 先比对回显得归因，再验签确认 | §6.3 |
| §9.1 约束 2 | 「签名前必须用户确认」 | 「身份绑定须人工审批；发信签名由 VC 授权，不逐封确认」 | §8.6 |
| §9.3 接收方能力 | 只要求 MTA 验证并注入 | 增：**最终消费者应能独立复验**；协议须明确"验证可用输入"并规定服务商原样投递 | §6.5、§3.5 |
| §5.1 整体结论 | 只有 `verified`/`failed` | 增 `not_verifiable` 与 `integrity=delegated` 语义 | §6.4、§7.3 |
| §1.1-5 / §8.2 | 只约束"不得代签" | 增：**服务商也不得代拼正文尾部** | §8.4 |
| §3.3 `IssuedVc` preimage | 五行明文 | **与代码不一致**：`anchor_payload_hash` 是 BLAKE2b over VC JSON，且已被 `vp_sd` 与 `sd.rs:352` 依赖。启用前须二选一并同步 | §1.4、§8.1 |
| §3.4 / §4.1 摘要算法 | 锚点层 SHA-256 明文行 | **锚点层改 BLAKE2b-256**；邮件层仍 SHA-256 | §6.9 |
| §3.2 `issuer_id` | 未指明对应哪个链上字段 | `≡ AnchorRecord.issuer_did`，并注明"先查记录再重建" | §6.7 |
| §2.5 多 `From` | 每地址一个头实例 | 保留条款；注明 aimail 不产生多地址签名，接收方须逐地址判定 | §8.8 |

---

## 15. 后续：HTTP 认证的信任模型（H 系列）

详细评估见 **`docs/did-http-auth-plan.md`**（含实测算力、威胁对照、9 条负面与缓解、H0–H4 分期）。
这里只留决策所需的骨架。

### 15.1 问题与目标

今天 `api_keys.key_hash = sha256(api_key)`，而它就是 HMAC 密钥：拖库/备份泄露即**全面冒充**，
服务商内部人也**可以代表任何一个用户**——这与邮件层的公理一方向相反。

目标：把 HTTP 认证换成 **DID 私钥签名的请求**，让服务商库里不再有能冒充用户的秘密。

### 15.2 两段式

| 阶段 | 做法 |
|---|---|
| 注册 | `POST /api/v1/did/enroll` → 网关给 `challenge` → 客户端签 **`GatewayAccess-v1`**（did-sdk 里早就有、一直没用上的签名对象）→ 网关离线解 `did:key` 验签 → 写绑定 |
| 稳态 | 每请求签 `AIMail-Request-v1`（`METHOD/PATH/T/t/BODY_SHA256/DID/ADDRESS` 七行），头 `Authorization: AIMail-DID …` |

授权来源是**本地 `did_bindings`**，不是链上 `valid_until`——否则锚定过期会让 agent 连收信都进不去。

### 15.3 `did_bindings` 的形态（已定）

```text
did_bindings(did PK, address, purpose, primary, active, approved_by, approved_at)
                  索引: (address, purpose, primary)
```

- **1:N + `purpose`**（`mail-signing` / `api-access`）：轮换＝加新 DID → 切 `primary` → 删旧，
  不换地址、不重走"地址归属"审批；
- **按用途分离**两把钥匙：`mail-signing` 由安全员审批，`api-access` 自持 → 哪把泄露处理哪把，
  爆炸半径压回现状；
- **本期就落表**：P7 的绑定审批流程顺手写入，H 开始时数据已就位。

### 15.4 代价（实测）

| 操作 | 耗时 |
|---|---|
| Ed25519 验签 | 128 µs（Python+OpenSSL；Rust 预期 50–70 µs，**H1 需 criterion 实测**） |
| HMAC-SHA256（现方案） | 1.8 µs |
| 每请求增量 | +126 µs → 100 rps 占单核 1.3% |

更关键的是**负载分布**：收信走 SSE 长连接，鉴权落在**建连**与 pending/ack，不是每封信；
真正按请求计的是发信与零星调用。必要时切"挑战换 15 分钟会话令牌"，仍是 DID 派生物、库里无共享秘密。

### 15.5 净收益与代价

| | 结论 |
|---|---|
| 安全增益 | **结构性**：库里无可冒充秘密；接口层与邮件层统一到 DID 一个身份根 |
| 算力代价 | 可量化、可降级、绝对值小 |
| 最大负面 | `did:key` 不可轮换 → 由 1:N 绑定补偿；seed 泄露面 → 由按用途分离补偿 |
| 排期 | **P8 之后**（H0–H4）；`did_bindings` 表现在随 P7 落地 |

---

## 16. 决策状态与待确认

**已锁定**

| # | 决策 | 结论 |
|---|---|---|
| 1 | 签名对象 | 方案 C：脱 MIME 的原始内容（§3.3） |
| 2 | 原语分层 | 链上 `blake2b:` / 邮件内 `sha256:`（§6.9） |
| 3 | `issuer_id` | `≡ AnchorRecord.issuer_did`（§6.7） |
| 4 | 头回显 `bh`/`rh` | 必须带，353 字节（§6.3） |
| 5 | 附件 | 各字段先哈希；`file_hash` 由持有者自己算（§8.3） |
| 6 | agent 签名 | 整体移到客户端（§8.4） |
| 7 | 白名单 | 保留机制，拒绝投递，不改写头；双通道回报（§8.5） |
| 8 | 确认模型 | 绑定审批一次，发信不逐封确认（§8.6） |
| 9 | 实现载体 | 编译产物 + 可选 extra（§8.7） |
| 10 | 多 `From` | 不支持签名，但逐地址严格判定（§8.8） |
| 11 | 锚定范围 | 只做 `EmailBinding` + `DomainControl`（§8.10） |
| 12 | 组件边界 | 锚点层落 `crates/mail-anchor`（§11.1） |
| 13 | **拒收模型** | **B′**：入口只做地址维度，拒收必须归因（§7.1） |
| 14 | **`did_bindings`** | **1:N + `purpose`**（§15.3） |
| 15 | **H 排期** | **P8 之后**；表随 P7 落（§15） |

**待确认**（都不阻塞开工）

1. **规范修订的推动节奏**（§14，共 15 条）：哪些随 P1 一起提交给规范方，哪些等实现稳定后再改。
2. **`DomainControl` 的运营落地**：每个接入域发 DNS TXT（§6.8）由谁在接入流程里执行、如何校验完成。
