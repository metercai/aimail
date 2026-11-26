# 基于 DID 的邮件地址真实性与完整性增强 —— 实施方案

> 状态：**方案，未执行**。本文不含代码改动。
> 覆盖 `aimail`（pysdk / tssdk）、`aimail-gateway`、`did-sdk`、`chain-sdk` 四仓。
>
> **阅读路径**
> Part I 概念基线（先统一词汇与边界，否则后面每个词都有歧义）
> → Part II 两条公理及其推论（设计从何而来）
> → Part III 事实基线（代码与依赖如何约束）
> → Part IV 职责矩阵（谁做什么）
> → Part V 契约（字节级定义）
> → Part VI 决策依据（每个取舍的理由）
> → **Part VII 不变量与检验（本文最该被逐条打勾的部分）**
> → Part VIII 代码映射 → Part IX 执行面

---

## Part I 概念基线

### 1. 三种东西不要混

| 东西 | 是什么 | 谁产生 | 例子 |
|---|---|---|---|
| **事实** | 一条关于"某物归谁"的声明 | 发行方 | "alice@example.com 归 did:key:z6Mk…" |
| **凭证（VC）** | 一份可交付的 JSON 文档，描述某个事实或属性 | 发行方签发 | `EmailBindingVC` |
| **锚定** | 链上的一条记录，承诺某个哈希 + 带生命周期 | 发行方（或经授权的提交代理） | `AnchorRecord` |

三者关系：**锚定可以承诺"事实"，也可以承诺"凭证"**。这是本节最重要的区分。

### 2. 锚定层只有一条记录，`anchor_type` 只是标签

```text
锚定层只有一种东西：

AnchorRecord { anchor_id, anchor_type, submitter_id, issuer_did,
               delegated_by_did, payload_hash, revoked, created_at, valid_until }
                              ↑
              anchor_type 只决定"payload_hash 承诺的是什么对象"
```

`AnchorType` 是**扁平枚举**（`chain-sdk/src/types.rs:134`，9 个值平级），**没有继承关系**。
共享的是 `AnchorRecord` 这一层结构，不是类型层次——**`IssuedVc` 不是 `EmailBinding` 的父类**。

`anchor_type` 由**调用方传入**（`chain-sdk/src/sd.rs:258` 直接传 `request.anchor_type`），
代码从不按类型校验语义。真正约束它的是**各类型自己的 preimage 定义**，而代码不会替你检查两者是否配对（§35）。

### 3. 三根互相独立的轴

| 轴 | 问的是什么 | 落在哪 | 与 `anchor_type` 有关吗 |
|---|---|---|---|
| **承诺对象** | 这条哈希承诺的是"事实"还是"凭证" | `anchor_type` 的语义 | 就是它 |
| **参与角色** | 谁发起、谁代提交、谁受益 | `issuer_did` / `submitter_id` / `delegated_by_did` / VC 的 `credentialSubject.id` | 无关 |
| **权限** | 谁有资格发行、谁能吊销 | `ParticipantRecord.chain_role` + 治理流程 + 服务端归属校验 | 无关 |

**角色三者必须分开**（最容易混的一处）：

| 角色 | 在 VC 里的位置 | 参与锚定吗 | 依据 |
|---|---|---|---|
| **issuer（签发方）** | `issuer` / `proof.verificationMethod` | **是，发起者** | `create_anchor` 门禁 `ensure_issuer()`；签发用 issuer 的 `participant-did` 密钥（`sd.rs:266`） |
| **subject / holder（持有方）** | `credentialSubject.id` | **否** | `Verifier` 角色无 `create_anchor` 权限；若兼为 Issuer 去锚别人的 VC，其 `issuer_did` 会与 VC 的 `issuer` 冲突 |
| **submitter / delegate（提交代理）** | 不在 VC 里 | **只代提交交易** | `submitter_id` / `delegated_by_did`，凭 `DelegationAuthorization-v1` |

### 4. 四种锚定类型的定位

| `anchor_type` | `payload_hash` 承诺的对象 | 谁消费 | 本项目 |
|---|---|---|---|
| `DomainControl` | `(domain, issuer_did)` | **发行方自己**（写 EmailBinding 前校验） | **做** |
| `EmailBinding` | `(did, email, issuer_did)` | **接收方**（从邮件本身重建，不需要 VC） | **做** |
| `IssuedVc` | 某张 **VC 的全文** | **持有该 VC 的人**（含选择性披露路径） | 本期不做 |
| `Warrant` | `(subject_did, warrant_type, issuer_did)` | 要判定"某主体是否被授权"的人 | 不做 |
| `DidDocument` / `OrgMembership` / `Delegation` / `GatewayAccess` / `Custom` | 见 §33 | 各自的角色 | 不做 |

**一句话**：本项目只做两个**事实锚**——`EmailBinding` 与 `DomainControl`，都由 gateway 以 `Issuer` 角色完成。

### 5. `IssuedVc` 的准确定义

> **`IssuedVc` 是某一张 VC 的链上锚**：由该 VC 的签发方（或经其授权的提交代理）在**签发流程中**创建，
> 承诺 VC 全文，并提供吊销与有效期。持有方不参与创建。

它**不是**"事实锚定的再锚定"。通用签发顺序（chain-sdk 契约 §7.3）：

```text
建 VC → 算 H(VC) → create_anchor 拿 anchor_id → VC.anchor_id = id → 签发签名（覆盖 anchor_id）
```

锚在 VC 签名**之前**创建，且 `anchor_id` 被签进 VC——所以锚是**凭证身份的一部分**，
不是事后对既有事物的再承诺。对 `WarrantVC`、学历凭证等而言，它是**唯一**的锚。

第三方拿到一张 VC，想验证什么 → 靠谁：

| 想验证 | `IssuedVc` 管吗 | 实际靠什么 |
|---|---|---|
| 内容有没有被改过 | **✓** | `payload_hash` 比对（BLAKE2b over VC 全文） |
| 是否被吊销 | **✓** | `revoked` |
| 是否过期 | **✓** | `valid_until`（VC 的 `expirationDate` 只作参考） |
| 选择性披露下的真实性 | **✓（是前提，不是机制）** | 必须先锚定；机制在 `_sd` 加盐哈希 + `verify_vp`，`sd.rs:340` 硬性要求 `anchor_id` |
| 发行方**有资格**发行 | ✗ | `ParticipantRecord.chain_role`（治理分配）/ `Warrant` |
| 发行方身份真实 | ✗ | 解析 `did:web`/`did:key`；域层面靠 `DomainControl` |

**核心价值**：锚定把"发行方**在线**签名"变成"发行方**离线**可验"——验证方查锚定即可，不必联系发行方（规范 §1.1-6、§6）。

### 6. 为什么 `EmailBinding` 不能用 `IssuedVc` 表达

这是本方案最容易被质疑的一处，给出决定性理由。

`IssuedVc` 的 preimage 是 `blake2b(VC 的 compact JSON)`（`vc.rs:462`，只丢 `proof` 与 `anchor_id`）。
**重算它必须持有那张 VC 的完整字节。**

而接收方**没有**那张 VC，且拿不到：

| 补救办法 | 为什么不行 |
|---|---|
| 让接收方自己找 VC | `did:key` 不承载 `service` 端点，无从查找（规范 §2.1 原话） |
| 让接收方去问发行方 | 违反"验证时发行方可离线"（规范 §1.1-6） |
| 把 VC 塞进邮件头 | 体积大、攻击者可控、且自我指涉（VC 的 `anchorId` 指向的锚又哈希着这张 VC） |

所以问题不是"取向"，是"能不能"：**`IssuedVc` 在结构上无法服务"手里没有凭证的第三方"。**

事实锚的 preimage 恰好是接收方手里那三样东西，一个不多一个不少：

```text
did      ← DID-Signature 头
email    ← From 头
issuer   ← 链上锚定记录
```

### 7. 三角关系与两个必须区分的字段

两者不是替代关系，是**同一事实的两份产物、两个锚**（加法）：

| 消费者 | 手里有什么 | 用哪个锚 |
|---|---|---|
| 接收方 MTA / 收件 agent | 邮件（From + 头） | `EmailBinding` 事实锚 |
| VC 持有方（要出示凭证、做选择性披露） | 那张 `EmailBindingVC` | `IssuedVc` 锚 |

若将来两者并存，形态是：

```text
DomainControl 锚  ──授权证据──┐
                              ▼
EmailBinding 锚  ←── 被引用 ── EmailBindingVC ── 若锚定 ──→ IssuedVc 锚
   （承诺事实）                 （关于事实的凭证）            （承诺凭证）
```

方向不同：VC 往回指向事实锚，`IssuedVc` 锚往前指向 VC。三个对象、三条承诺。

**命名坑（必须写进契约）**——两个都叫 anchor，大小写只差一个字母，指向不同的锚：

| 字段 | 位置 | 指向 | 谁用 |
|---|---|---|---|
| `credentialStatus.anchorId` | VC 的 `credentialStatus` 内（规范 §4.2） | **EmailBinding 事实锚** | 接收方 / 审计 |
| 顶层 `anchor_id` | `did_sdk::VerifiableCredential.anchor_id` | **该 VC 自己的 `IssuedVc` 锚** | did-sdk 的 SD 路径 |

**且 `anchor_payload_hash` 只丢 `proof` 与 `anchor_id`，`credentialStatus` 会进入 VC 哈希。** 含义：

> 事实锚的 id 一旦写进 VC，就被钉死在 VC 的哈希里。**事实锚不可替换**——想换只能先撤销、新建、再重签 VC。
> 不存在"原地改指向"。

---

## Part II 两条公理及其推论

### 8. 问题：SPF/DKIM/DMARC 的盲区

| 机制 | 回答的问题 | 不回答的问题 |
|---|---|---|
| SPF | 这个 IP 被授权代表这个域发信吗 | 地址是否存在、归谁 |
| DKIM | 这个域授权了这封邮件吗 | 同上——信任单位是**域**，不是**具体地址** |
| DMARC | 域策略是什么、对齐了吗 | 同上 |

要补的是**两件事，缺一不可**：地址真实性（`From` ↔ `did:key` 控制者 ↔ 目前有效的绑定）
与邮件完整性（正文、附件、收件人、时效未被篡改或重放）。任一不成立，整体即 `failed`。

### 9. 公理一：签名者必须是内容控制者

要证明"地址控制者此刻在签名"，签名的执行者与内容的决定者必须是同一个人。

推论：**发件服务商、milter、中转 MTA、KMS 都不得参与签名**，连"决定签什么"都不行。

### 10. 公理二：哈希只能由验证方自己算

| 类别 | 例子 | 能否来自对方 | 为什么 |
|---|---|---|---|
| **被验证的输入** | subject、裸正文、附件字节、文件名、To/Cc | **能** | 对方撒谎 → 重算不符 → 失败（fail-closed） |
| **被比对的值** | 任何 hash | **不能** | 对方撒谎 → 恰好与我们该相等的目标一致 → 通过（fail-open） |
| **签名覆盖的声称** | 头里的 `bh`/`rh`/`s`/`did`/`t`/`vc` | 能 | 它们本身被签名，改了就验签失败 |

所以头里的 `bh`/`rh` 安全（有签名背书），而"服务商算好的附件哈希"不安全（无人背书）。
**凡是 hash，验证方必须从原始字节自己算。**

### 11. 推论一：签名对象是"原始内容"，不是 MIME（方案 C）

判据来自公理一：**谁决定签名对象**。

| 方案 | 做法 | 客户端能独立算出吗 | 判定 |
|---|---|---|---|
| A 两阶段冻结 | `/send/prepare` 冻结最终 MIME，把摘要回给客户端签 | **不能**——摘要由服务端算 | **淘汰** |
| B 客户端提交完整 raw MIME | 客户端自造 MIME，服务商原样转发 | 能 | 合格，成本约 C 的 3 倍 |
| C 原始内容自签名 | 对 `Content-v1` 签名，与 MIME 无关 | 能 | **采纳** |

A 淘汰的关键：摘要不可伪造，但**"哪些内容会被签"由服务端决定**——服务商只要能选择被签的载荷，就恢复了
"代表用户"的能力，那正是规范 §8.2 用整节篇幅要消除的东西。**A 是"看起来端到端、实际不是"的方案。**

C 的成立条件由 §14（`text/plain` = 存储正文逐字节原样）与 §16（附件逐字节落盘、可按引用取回）保证。
附带收益：把 QP/base64/随机 boundary/行折叠这些 MIME 规范化雷区整体消除。

### 12. 推论二：gateway 必须停止三件事

同一决定的后果，不是额外需求：

| 停止什么 | 为什么 | 替代做法 |
|---|---|---|
| 给正文尾部追加 agent 签名 | 客户端签的正文与投递的正文必然不同 | 尾部移到客户端拼（§41） |
| 改写 To/Cc 头 | 接收方算出的 `rh` 与客户端签的对不上 | 白名单照旧，但**拒绝投递**被过滤地址（§43） |
| 参与签名 | 违反公理一 | 只转发、只验证 |

另外 DKIM 只许加头、不许改正文，否则 `bh` 必然失效（§44）。

### 13. 推论三：收件方必须独立复验

"端到端"要求结论从签名者传到最终消费者，中途任一环判失败都不能被忽略：

```text
签名者（agent SDK）──→ 发件服务商（只转发）──→ 接收方 MTA（验证 + 注入结论）──→ 收件 agent 的 SDK（独立复验 + 裁决）
        签                        不改不签                      验                    再验一遍，不过即拒收
```

只做前半段而让 agent 无条件接收，等于把验证结果丢在地上。收件侧两道关：**MTA 验** + **SDK 独立复验**；
后者不信任前者，只把它的结论当交叉校验的第二意见。

---

## Part III 事实基线

每条事实后面紧跟它约束了哪个设计点。

### 14. 正文：`record.body` 是唯一原始内容，MIME 是派生的

现行 `send.rs:100` 给正文尾部追加 `"\n\n-- \n" + meta.agent_signature`，入库为 `record.body`。
投递时（`sender.rs:112-118`）：

```rust
let html_body  = markdown_to_html(&record.body);   // HTML 只是派生视图
let plain_body = record.body.clone();              // ★ text/plain = 存储正文逐字节原样
```

→ 接收方解码 `text/plain` 即可完整还原签名对象，使"签名与 MIME 无关"与"接收方可重算"同时成立（§11）。

### 15. 收件人会在投递前被过滤与重写

`send.rs:745-771`：最终 To/Cc 由 `external ∪ final_internal ∪ system_sink` **按白名单/board 过滤后的桶**重拼
（注释明写 "NOT from to_set"），显示名被 `name_map` 或 local-part 重写；`persona_map` 能映射回原地址，
但被过滤的收件人不会出现。SMTP 信封 = 仅外部收件人，内部走 webhook。

→ 白名单必须保留，但不能以"改写头"实现（§43）。

### 16. 附件逐字节落盘，可按下发引用取回

`factory.rs:889` `tokio::fs::write(&file_path, content)`——上传即原样存储；
`GET /api/v1/attachments/{id}`（`files.rs:142`）可取回原始字节。

→ 两端字节一致，附件可入签名。**系统不保存附件哈希，也不需要保存**——两端各自算（公理二）。

### 17. 出站自定义头走白名单；DKIM 挂在 transform 上

`OUTBOUND_PASSTHROUGH_HEADERS`（`mx_deliverer.rs:26`，`sender.rs:329` 复用）当前只放行四个头。
`OutboundTransform::transform_or_passthrough` 在 `formatted()` 之后、发送之前跑。

→ `DID-Signature` 加入白名单即可透传；DKIM 若改正文则签名必失效（§44）。

### 18. 入站：只有 `data_end` 一个注入点；DSN 会提前返回

`receiver.rs:433 data_end` → `check_deferred_whitelist` → `process_data_end_post`；
`:590` 取 `raw_data` → `mailparse` → 入库。
**但 `process_data_end_post` 里 `MAIL FROM:<>` 的退信/NDR 分支会在 MIME 解析前 return。**
记录头只捕获 `to`/`cc`/`from` 与 `x-aimail-*`（`receiver.rs:893-903`）。
`ConnectionHandler` 已用 `block_on` 同步阻塞（`rcpt_board_addr`、`verify_bounce`）。

→ 验证必须插在退信分支**之后**、MIME 解析之前；DSN 不参与验证（§52⑤）。

### 19. 入站载荷缺三样东西

**① 入站 `record.body` 就是发端正文的逐字节拷贝。** `factory.rs:576-585` 的 `walk_mime_parts`：
遇 `text/plain` 时 `body.clear(); body.push_str(&text)`——plain 覆盖一切，html 只在 body 为空时兜底；
不 trim、不剥引用。`decode_text_body` 只做 charset 解码。→ `inbound.record.body == outbound.record.body`。

**② 但下发的 `payload["body"]` 不是它。** `records.rs:110-123`：下发前先跑
`bodyproc::process_email_body()`（引用剥离、分层），正常情况 `payload["body"] = processed.body`。

**③ 附件元数据没有哈希**——这是正确的（公理二），但载荷因此缺了验签输入。

**④ 记录里的 To/Cc 是过滤后的**：`receiver.rs:893-903` 的 `if !headers_map.contains_key(...)`
让先写入的（按信封过滤后的）列表优先生效，客户端原始头被丢弃；而 `rh` 要按原始头重算。

→ 端到端可验性不缺能力，缺**三个载荷字段**（§38）。

### 20. did-sdk：能力、边界与一个易误判点

- 有：`did:key` 生成/离线解析、Ed25519、VC 钱包（ChaCha20-Poly1305 `DVCS`）、`verify_vc`/`verify_vp`、选择性披露。
- **无链访问**（R1）：不做锚定查询、不持链账户私钥。
- 签名确认：`core.rs:48-57` `confirm_challenge`——装了 handler 才问，**没装就 `None => Ok(())`**；
  Python / Node 绑定都没暴露 `set_request_handler`。→ 无人值守签名今天就能用，"确认"要上移（§41）。
- 已有但从未被生产代码使用的签名对象：`GatewayAccess-v1`（§36）。

### 21. chain-sdk：能力边界，以及它"不算哈希"

- `ChainSdk` trait：`get_anchor` / `create_anchor(anchor_type, payload_hash: &str, valid_until)` /
  `renew_anchor` / `revoke_anchor` / `resolve_did_key`（离线）/ `subscribe_event`。
- `AnchorType` **已含 `EmailBinding`**（`types.rs:134`，9 个值）。
- `create_anchor` 收的是**字符串**：chain-sdk 从不自己算 `payload_hash`（测试里甚至直接写
  `"blake2b:deadbeef"`），唯一校验点是 `sd.rs:352` 的 `verify_sd_presentation`，且只覆盖 VC 一种。
- 模块头把这件事写成约定：`sd.rs:13` "`payload_hash` uses the **anchor-layer BLAKE2b convention**"。
  **9 种 `AnchorType` 里只有 `IssuedVc` 有实现，其余 8 种零实现**（§33）。
- 角色 `Verifier`（只读、无 KeyVault）/ `Issuer`。后端 `Mock` / `Centralized`（HTTP+WS）/
  `Chain`（subxt，v0.1.1 存储读取与 extrinsics 均 `Unsupported`）。
- **`revoke_anchor` / `renew_anchor` 门禁只有 `ensure_issuer()`**（`mock.rs:484`），
  不检查"这条锚定是不是你建的"——归属校验只能由服务端做（§35）。

### 22. did-sdk 的发布现状

| 项 | 实测 |
|---|---|
| PyPI `did-sdk-client` / npm `@did-sdk/client` | **404，未发布** |
| CI 构建 | 5 个 target 成功 |
| 发布去向 | 只挂 GitHub Release 附件，无 PyPI / npm publish |
| Python wheel | `-i python3.12`、无 abi3 → `cp312`，**只覆盖 3.12** |
| 缺失 target | `x86_64-apple-darwin`、`aarch64-unknown-linux-gnu`、Node 侧对应 triple |

**实测一**：pyo3 加 `abi3-py39` 后 `cargo check -p did-sdk-python` 通过（23.7s），
`multiple-pymethods` + `pyo3-async-runtimes` 无冲突 → **一个 `cp39-abi3` wheel 覆盖 3.9+**，共 7 个 wheel。

**实测二**：全部 `Cargo.toml` 无 `openssl` / `native-tls` / `-sys`（`reqwest{rustls-tls}`、
`ed25519-dalek`、`argon2`、`chacha20poly1305`、`ring`）→ **只需区分 arch + OS + libc**。

### 23. npm 与 PyPI 的平台适配

- npm 原生 `os` / `cpu` / `libc` 字段；napi-rs 标准做法是 meta 包 + `optionalDependencies`；
- **N-API ABI 稳定**：一个 `.node` 覆盖 Node ≥ 18，不需按 Node 大版本重建；
- did-sdk 的 `bindings/node/index.js` 已是标准 napi-rs loader，两套加载都支持，分包不用改代码。

→ 发布工程是用编译产物的硬前置，工作量在 CI/打包，不碰密码学（§42）。

---

## Part IV 职责矩阵

先把"要发生的事"按时间顺序拆成 R1–R43，标清**谁做、输入什么、产出什么、失败怎么办**。
Part VIII 再把它们映射到代码，**且不引入任何这里没出现的职责**——映射时若发现代码需要新职责，说明这里漏了，先补这里。

### 24. 发送链路（客户端 → 服务商 → 外发）

| # | 职责 | 执行方 | 输入 | 产出 / 失败语义 |
|---|---|---|---|---|
| R1 | 持有该地址的 `did:key` 私钥（seed） | SDK | 本地 `did/<地址>/seed` | 缺失 → 不签名，邮件仍可发（降级） |
| R2 | 持有该地址**有效**的 `EmailBindingVC` | SDK | 绑定审批下发 | 过期/撤销 → 不签名（降级），告警 |
| R3 | 持有 agent 签名文本 | SDK | 指令邮件写入的本地配置 | 为空 → 正文无尾部，不阻塞 |
| R4 | 组装正文 = markdown + `"\n\n-- \n" + 尾部` | SDK | R3、用户输入 | 顺序写死，向量覆盖 |
| R5 | 计算 `Content-v1`（逐附件 `name_hash`/`file_hash`） | SDK | R4、文件名、**本地文件字节** | 对**自己手里的字节**求哈希 |
| R6 | 计算 `rh` | SDK | 提交的 To∪Cc | — |
| R7 | 组七行签名对象并 Ed25519 签名 | SDK（经 did-sdk） | R1、R5、R6、`did`、`t`、`vc` | 失败 → 不签名 |
| R8 | 产出 `DID-Signature` 头并随 `/send` 提交 | SDK | R7 | — |
| R9 | 收件人白名单/board 过滤 | gateway | 既有机制 | **不改写头**；被过滤地址拒绝投递 + 双通道回报（§43） |
| R10 | 透传 `DID-Signature` | gateway | R8 | 加入出站头白名单，不剥不签 |
| R11 | DKIM 只加头不改正文 | gateway transform | 原字节 | 改正文 → `error!` 日志 + 文档约束 |

### 25. 接收链路（MTA 侧）

| # | 职责 | 执行方 | 输入 | 产出 / 失败语义 |
|---|---|---|---|---|
| R12 | 剥离邮件里已有的 `DID-Auth-Results` | gateway | `raw_data` | 外部结论一律不可信 |
| R13 | 解析 `From` 与 `DID-Signature`；多地址逐个处理 | gateway | R12 输出 | 无头 → `did_absent` |
| R14 | 离线解码 `did:key`、校验时间窗 | gateway | R13 | `did_unsupported` / `timestamp_out_of_window` |
| R15 | 重算 `Content-v1`（`text/plain` + 逐附件部件字节） | gateway | 原始 MIME 在手 | 不符 → `body_hash_mismatch` |
| R16 | 重算 `rh`（按原始 To/Cc 头） | gateway | MIME 头 | 不符 → `rcpt_hash_mismatch` |
| R17 | Ed25519 验签 | gateway | R14–R16 | `signature_invalid` |
| R18 | 锚定查询 + `payload_hash` 重建比对 | gateway（chain-sdk Verifier） | `vc=chain:<id>` | 六个 address reason |
| R19 | 生成 `DID-Auth-Results` 并写入记录头 | gateway | R12–R18 | 每地址一个实例 |
| R20 | 载荷构造：`body_signed` / `rcpt_*_raw` / 附件引用 / `results` | gateway | R19 + `record.body` | 仅 DID 记录追加（§38） |

R12–R17 零网络；R18 一次查询，失败按 `chain_unavailable` 处理且**不拒收**（§51）。

### 26. 接收链路（agent 侧，独立复验）

| # | 职责 | 执行方 | 输入 | 产出 / 失败语义 |
|---|---|---|---|---|
| R21 | 系统邮件识别并分派（`X-AIMail-System`），先于 DID 裁决 | SDK | 载荷头 | 命中 → 处理并吞掉 |
| R22 | ping/pong 拦截 | SDK | 既有 | 命中 → 吞掉 |
| R23 | 解析头 + 时间窗（以 `gateway_time` 为基准） | SDK | `did.signature` | 对应 reason（§51） |
| R24 | 用 `body_signed` 重算 `body_hash` | SDK | R20 | — |
| R25 | **下载附件字节并本地算** `name_hash`/`file_hash` | SDK | `attachments[{id,filename}]` | 取不回 → `not_verifiable` |
| R26 | 组 `Content-v1` 比 `bh` | SDK | R24、R25 | `body_hash_mismatch` |
| R27 | 用 `rcpt_*_raw` 重算 `rh` | SDK | R20 | `rcpt_hash_mismatch` |
| R28 | Ed25519 验签 | SDK（经 did-sdk） | R23–R27 | `signature_invalid` |
| R29 | 地址维度：复用 `results` 或自查锚定 | SDK | `did.results` / `vc` | 两种模式（§51） |
| R30 | 双侧交叉校验 | SDK | R26–R29 | 不一致 → 告警，**以自己为准** |
| R31 | 裁决 accept / reject / accept_flagged | SDK | R28–R30 + 策略 | 见 §51 |
| R32 | 拒收：写审计副本 + 日志事件 + 吞掉 | SDK | R31 | 不进 LLM、不写常规索引 |
| R33 | 放行：常规 preprocess → LLM | SDK | 既有链路 | — |

### 27. 低频：绑定与发行

| # | 职责 | 执行方 | 输入 | 产出 |
|---|---|---|---|---|
| R34 | 发起绑定：验证码 + DID 挑战 | gateway 发行方端点 | agent 的 DID + 邮箱 | challenge / 验证码 |
| R35 | 校验：验证码 + DID 挑战签名（**复用 `GatewayAccess-v1`**） | gateway | R34 | 通过 / 拒绝 |
| R36a | 创建 / 续期 / 撤销 `DomainControl` 锚定 | gateway（Issuer） | 域名 + 自己的 participant DID | `domain_anchor_id` |
| R36b | DNS `_email-verification.<domain>` TXT 解析 + `DomainControl` 核对（EmailBinding 写入/续期前必做） | gateway | 域 | 控制权成立与否（§34） |
| R37 | `create_anchor(EmailBinding, payload_hash, valid_until)` | gateway（chain-sdk Issuer） | R36b | `anchor_id` |
| R38 | 签发 `EmailBindingVC`（含 `credentialStatus.anchorId`、`expirationDate`） | gateway（did-sdk Server） | R37 | VC |
| R39 | 推送指令邮件：VC + agent 签名文本（不进 LLM） | gateway | R38 | 客户端落盘（R1–R3） |
| R40 | 续期 / 撤销 + 推送 | gateway | 运营操作 | 客户端标记失效 |

### 28. 运维与可观测

| # | 职责 | 执行方 |
|---|---|---|
| R41 | 指标：`did_verified` / `did_failed_integrity` / `did_failed_address` / `did_absent` / `did_chain_error` / `did_not_verifiable` | gateway |
| R42 | `aimail did status`：本地 VC / 签名 / 与网关比对（按需 GET，**不在发信路径**） | SDK |
| R43 | 审计：拒收副本、双侧不一致事件 | SDK / gateway |

---

## Part V 契约

### 29. 投影 `Content-v1`

```text
Content-v1（UTF-8，每行以 \n 结尾，含最后一行）

  Content-v1
  {subject}
  sha256:<body_hash>
  {attachment_count}
  {i}\tsha256:<name_hash>\tsha256:<file_hash>        # i 从 1 起，按提交顺序
  ...
```

| 值 | 定义 | 归属 |
|---|---|---|
| `body_hash` | `SHA-256(规范化 body)`；规范化只做三件与 MIME 无关的事：`\r\n|\r → \n`、每行去行尾空白、去末尾空行 | 正文已含 agent 尾部（R4） |
| `name_hash` | `SHA-256(UTF-8 文件名)` | 先哈希消除分隔符歧义 |
| `file_hash` | `SHA-256(附件原始字节)` | **持有字节的一方各自算**（公理二） |

字段各自先哈希再入投影：消除边界注入；`file_hash` 成为独立可复用的值。

### 30. 收件人哈希 `rh`

提交的 To ∪ Cc，`normalize`（小写、去显示名/注释、取 addr-spec）后字典序、`\n` 拼接，SHA-256，前缀 `sha256:`。
`Bcc` 不参与——SMTP 架构的固有限制，不做过度承诺。

### 31. 签名对象与头

签名对象（七行，**每行尾随 `\n`，含最后一行**）：

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

`bh`/`rh` 必须回显：接收方验签用的是它自己算的值，头里没有声称值就只能报 `signature_invalid`，
无法区分"内容被改"与"签名坏了"。**先比对回显得归因，再验签确认**——验签始终是权威。

字节成本实测 **353 字节**（`did` 62 + `s` 88 + `bh` 76 + `rh` 76 + `vc` 24 + `t` 12 + `v` 3 + 分隔 12），
折行 5 行，与 DKIM-Signature（300–600）同量级。

### 32. `DID-Auth-Results`

```text
DID-Auth-Results: result=verified; integrity=pass; address=verified; did-id="did:key:z6Mk…"
DID-Auth-Results: result=failed; integrity=fail; address=unknown; reason=body_hash_mismatch
```

`result=verified` 当且仅当 `integrity=pass` 且 `address=verified`。
`integrity=fail` 时跳过链上查询（省一次远程调用，且符合"完整性是地址验证前提"）。

### 33. 锚定类型的归属与本期范围

9 种 `AnchorType` 里目前**只有 `IssuedVc` 有实现**（§39）。其余 8 种在本项目里要不要做、谁做：

| 类型 | spec 语义（`payload_hash` 覆盖） | 谁实现 | 本期 | 依据 |
|---|---|---|---|---|
| `EmailBinding` | `(did, email, issuer_id)` | **gateway（Issuer）** | **必做** | 核心产物 |
| `DomainControl` | `(domain, issuer_id)` | **gateway（Issuer）** ＋ **域名所有者发 DNS TXT** | **必做**（紧随 EmailBinding） | 规范 §4.3.3 要求在每次 EmailBinding 写入/续期**之前**完成控制权校验；不做则退化为"发行方自称"，并失去 §4.3.4 的域名易主撤销能力 |
| `DidDocument` | `(did, document_hash)` | 身份链 / DID 注册服务 | **不需要** | aimail 全程 `did:key`：公钥内嵌、离线解析、无可锚的文档。gateway 的 `participant_did` 虽是 `did:web`，其文档锚定属身份服务商职责 |
| `Warrant` | `(subject_did, warrant_type, issuer_id)` | 签发权证的机构 | 不需要 | aimail 无权证语义 |
| `Delegation` | `(delegate_did, delegator_did, scope)` | **chain-owner-sdk** | 不需要（间接用到） | 委托关系由 chain-owner-sdk + `DelegationAuthorization-v1` 表达；aimail 只作为被代理的发行方 |
| `OrgMembership` | `(did, org_id, issuer_id)` | 组织目录 / HR | 不需要 | 无组织模型；地址↔DID↔agent 一对一（§41） |
| `IssuedVc` | 某张 VC 全文 | 签发 VC 的一方 | **本期不做**（有意） | `EmailBindingVC` 不再锚：`EmailBinding` 已承诺绑定内容，再锚是同一事实的第二次承诺。触发条件见 §5（选择性披露） |
| `GatewayAccess` | spec 未定义 preimage；有 `GatewayAccess-v1` 签名对象 | gateway | **签名对象复用，锚定不做** | §36 |
| `Custom` | 需链主 `register_anchor_type` | 链主 | 不使用 | 无需求 |

### 34. DomainControl：为什么只能由 gateway 做，以及一条外部依赖

规范 §4.3.3 的三步校验发生在**每次 EmailBinding 写入/续期之前**，执行者只能是创建 EmailBinding 的一方
——也就是 gateway（`Issuer`）。两组能力：

1. **写**：`create_anchor(DomainControl, blake2b:…, valid_until)` / `renew_anchor` / `revoke_anchor`；
2. **读+校验**：解析 `_email-verification.<domain>` TXT → 取 `chain:<domain_anchor_id>` → 查锚定 →
   校验 `anchor_type == DomainControl`、`issuer_did ==` 自己的 participant DID、`revoked == false`、
   `valid_until == 0 || now <= valid_until`。

**引导顺序（不能反）**：

```text
① gateway 为域创建 DomainControl 锚定                        → anchor_id
② 域名所有者在 DNS 发布：
     _email-verification.<domain>. IN TXT "ev-anchor=chain:<anchor_id>"
③ 之后每次创建/续期 EmailBinding，gateway 重做上面的读+校验
```

**这是一条新的外部接入要求。** 目前 gateway **没有任何域名归属的 DNS 验证**：`system_domains` 只是内部表，
注册靠管理员操作（`http.rs:599` 的"domain anchor"是内部地址记录，不是 DNS）。因此本期新增：

- 每个接入域名的所有者必须发布那条 TXT（**运营动作，不是代码**）；
- 域名易主后，新所有者改写/删除 TXT 会让后续 EmailBinding 校验失败（规范 §4.3.4 的机制）；
- 已存在的旧 EmailBinding 在 `valid_until` 到期前仍有效——"低频验证 + 有效期"的**有界窗口**。

**不做的代价**：失去 (a) 域控制权的独立证据、(b) 域名易主后撤销旧锚定的依据；对外部接收方而言
"地址真实性"退化为"发行方可信"。

### 35. 两个必须的工程约束

**① 类型与 preimage 必须人工配对——代码不会替你检查。**
`create_anchor(anchor_type, payload_hash: &str, ..)` 里类型是调用方传的标签。若有人把
`AnchorType::EmailBinding` 传给 `issue_sd_credential`，chain-sdk 会老实存下 `blake2b(VC JSON)`，
而按行式 preimage 重建的接收方会**永久** `anchor_hash_mismatch`。对策：
我方是这两种类型的**唯一写入方**；写入前校验该类型已由链主 `register_anchor_type` 注册，且与模块枚举编译期绑定。

**② SDK 不校验锚定归属，"谁能吊销"必须由服务端兜住。**
`revoke_anchor` 门禁**只有** `ensure_issuer()`（`mock.rs:484`）——SDK 层面任何已注册 Issuer
都能吊销任意 `anchor_id`。中央化锚定服务**必须**自行校验 `anchor.issuer_did == 请求者`（§37）。
**这也是"`IssuedVc` 必须由 issuer 锚、不能由持有方锚"的真实原因**：谁锚，谁就握住吊销权。

### 36. `GatewayAccess`：复用签名对象，不做锚定

did-sdk 里已有一个**从未被生产代码使用**的签名对象（`keyvault-core/src/signing_input.rs:54`）：

```text
GatewayAccess-v1\n{did}\n{agent_email}\n{challenge}
```

契约注明用途 "HTTP gateway auth"——正是 R34/R35 需要的形状。**直接复用，不另造格式**：
两端绑定都已导出（`bindings/python/src/lib.rs:402`、`bindings/node/src/lib.rs:716`），并有现成向量。
但**不需要**锚定上链：这是"本次会话里我是这个 DID 的控制者"的即时证明，传输层已有 API key + HMAC。

### 37. 对中央化锚定服务的接口要求（外部依赖）

`Chain` 后端存储读取仍是 `Unsupported`，全部走 `Centralized`。接口在 chain-sdk 契约 §5.2 已定义，
**本项目不新增任何接口**：

| 用途 | 接口 |
|---|---|
| 读 EmailBinding / DomainControl | `GET /anchors/{anchor_id}`（404 → `AnchorNotFound`） |
| 列锚定（域名易主后批量撤销用） | `GET /anchors?anchor_type={kind}&submitter_id={id}` |
| 写 / 续 / 撤 | `POST /anchors`、`POST /anchors/{id}/revoke`、`POST /anchors/{id}/renew` |
| 交叉校验发行方是否已注册参与者 | `GET /participants/by-did/{did}` |
| DID 解析（可选） | `GET /dids/{did}` |

服务端必须至少支持 `anchor_type` = `EmailBinding` 与 `DomainControl`；
并且**必须校验锚定归属**（`anchor.issuer_did == 请求者`，或等价策略），否则吊销边界形同虚设（§35-②）。

### 38. 端到端载荷（R20）

只对带 `did-signature` 的记录追加，普通邮件载荷不变：

```json
{
  "mail_id": "...", "from": "alice@example.com", "to": ["bob@x.com"], "subject": "...",
  "body": "（bodyproc 处理后的正文，给 LLM 用）",
  "did": {
    "signature": "v=1; did=\"did:key:z6Mk…\"; …",
    "body_signed": "（record.body 原文，仅用于验签，不进 LLM）",
    "rcpt_to_raw": "Bob <bob@x.com>, carol@y.com",
    "rcpt_cc_raw": "",
    "attachments": [{ "id": "att_1", "filename": "q3.pdf" }],
    "gateway_time": 1765144656,
    "results": "result=verified; integrity=pass; …"
  }
}
```

**刻意不含任何 hash**：附件哈希由 SDK 下载字节自己算（公理二），给了反而诱导误用。
`body_signed` 上限 1 MiB，超限置 `not_verifiable`（§51）——**不能改成"下发哈希"**，那又变成让 gateway 出题。

### 39. `EmailBinding` 的 `payload_hash`：每层一个原语

**先澄清一个容易误解的点：这里不存在"did-sdk 与 chain-sdk 不统一"的问题。**

- `did_sdk::anchor_payload_hash` 只服务 **`IssuedVc`**（对 VC 的 compact JSON 求 BLAKE2b-256，去 `proof`/`anchor_id`）；
- chain-sdk 的 `sd::payload_hash` 直接**委托**它——两者在"VC 锚定"上本来同一个实现；
- 但 `EmailBinding` 是完全不同的锚定类型，preimage 不是 VC JSON，而是四行明文；
- 而 `create_anchor` 收 `payload_hash: &str`，**chain-sdk 从不自己算哈希**。

所以 EmailBinding 用哪个摘要算法，与 did-sdk/chain-sdk 的既有约定没有交集，是本协议自己的选择。
**9 种类型里只有 `IssuedVc` 有实现，其余 8 种零实现**——所以这次选择实质上定义了整条锚点层的 preimage 规范。

**结论：按"层"划分原语，而不是全协议只用一种。**

| 层 | 原语 | 为什么 |
|---|---|---|
| **链上承诺**（`AnchorRecord.payload_hash`） | **BLAKE2b-256**，前缀 `blake2b:` | 链侧已全是 BLAKE2b（`did:peer:1` 用 blake2b256、SS58 用 blake2b512、`IssuedVc` 用 blake2b256）；同一层两种算法才是真不一致 |
| **邮件内摘要**（`bh` / `rh` / `name_hash` / `file_hash`） | **SHA-256**，前缀 `sha256:` | 永不落链，与 DKIM/TLS 同族，第三方零额外依赖 |

边界很硬：**不落链的用 SHA-256，落链的用 BLAKE2b。** 因此规范 §2.3/§2.4 不动，只改 §3.4/§4.1；
did-sdk 一行代码不用改。

两者关系：安全强度同级（碰撞 ~2^128、原像 ~2^256，均无实用攻击），差别在工程属性——
BLAKE2b 软件更快（64-bit 常见 2–3×），SHA-256 硬件加速几乎必有且生态普及。
注意 `BLAKE2b-256` **不是** BLAKE2b-512 截断，也**不是** BLAKE2s-256：摘要长度是参数，必须写明。

**EmailBinding 的精确定义**

```text
payload_hash = "blake2b:" + BLAKE2b-256( "EmailBinding-v1\n{did}\n{email}\n{issuer_did}\n" )
```

三条硬约束：四行、每行尾随 `\n`；任一字段含 `\n`/`\r` 必须拒绝（否则可构造 preimage 碰撞）；
不走 compact JSON（引入键序/浮点/Unicode 转义歧义，而三个字段都是不透明字符串）。

**复用结构，不复用构造。** `EmailBinding` 复用 `AnchorRecord` 的全部字段与生命周期，
但 preimage 是类型特定的四行明文——被承诺的对象性质不同（"凭证" vs "某地址归某 DID"的声明）。

### 40. `issuer_id` 的语义

**三个字段的权威定义**（chain-sdk 契约 §6.3 原文）：

| 字段 | 契约原文 | 含义 |
|---|---|---|
| `issuer_did` | semantic issuer = **trust-chain root** (the delegating party) | 语义上的发行方，对这绑定关系负责的一方 |
| `submitter_id` | actual submitter participant (governance audit) | 实际提交者，**仅供治理审计** |
| `delegated_by_did` | proxy/delegate DID; `None` for self-submission | 代理方 DID，透明审计用 |

配套规则：`create_anchor` **总是自提交**（`issuer_did = controller_did`、`delegated_by_did = None`）；
**委托锚定由 `chain-owner-sdk::submit_delegated_anchor` 提交**，chain-sdk 只读。

**preimage 必须用 `issuer_did`**：

| 候选 | 自提交 | 委托提交 | 换提交路径后 | 判定 |
|---|---|---|---|---|
| `issuer_did` | = `controller_did` | 仍是信任链根，**不变** | hash 不变 | **采纳** |
| `submitter_id` | = 自己 | **换成链主/代理商** | **hash 变化** | 淘汰 |
| `delegated_by_did` | `None` | 有值 | hash 变化 | 淘汰 |

`submitter_id` 淘汰要具体说：设服务商 X 签发 EmailBinding，后把提交权委托给链主 Y。若 preimage 用
`submitter_id`，同一条绑定的 `payload_hash` 会在两种提交路径间跳变——**续期或补提交后，所有已发出的 VC
里的 `anchor_id → payload_hash` 对不再成立**，接收方一律 `anchor_hash_mismatch`。

**两条推论**：① 验证顺序是"先查锚定、再重建哈希"（`issuer_did` 只能从记录读到）；
② **发行方轮换 `controller_did` 不影响旧锚定**——记录里冻结的是创建时的值。

**落地约定**：`issuer_did` 写发行方的 `did:web`（与 `ParticipantRecord.controller_did` 一致），
不要自造编号；这样验证方能交叉确认"发行方是已注册参与者"。**不要**对 `issuer_id` 做 DID 格式校验。

---

## Part VI 决策依据

### 41. agent 签名归属与确认模型

**agent 签名整体移到客户端**（与是否用 DID 无关，顺带修正"服务端代拼署名"的旧范式）：

| 项 | 现状 | 改为 |
|---|---|---|
| 正文尾部拼接 | `send.rs:100` 服务端 | **SDK 拼**（R4） |
| 值的存储 | `domain_addr_meta.agent_signature` | **客户端** `did/<地址>/agent-meta.json` |
| 值的下发 | 不需要 | 指令邮件（R39） |
| 对外暴露 | `whoami` 返回、`admin/agent-meta` 可写 | 两者下线该字段 |

好处：不再有"服务端 meta vs 本地"两个来源；DID 与非 DID 邮件走同一条正文组装路径。

**迁移必须分三步**（否则双尾部或空尾部）：① 发 SDK + 服务端临时幂等补丁（正文已以 `"\n\n-- \n"` 结尾则跳过）
→ ② 删除服务端拼接与存储 → ③ 下线 `whoami`/`agent-meta` 的字段。
破坏性影响：非 SDK 的直接 API 调用方不再自动获得署名尾部。

**确认模型**：安全员审批的是**绑定本身**（地址 ↔ DID ↔ agent 身份，一对一，等价于签发 VC）；
之后该地址发信只要符合系统规约即自动签名。地址:DID:agent 一对一，**不代表任何人或团队**。
对外话术：「该地址的 DID 身份经安全员审批」，**不能**说"每封邮件人工确认"。

### 42. 实现载体：编译产物而非三语言重写

判据是**漂移面 × 生命周期**。契约植入的最小集合不大（Ed25519/SHA-256/base58 是冻结标准），但**会变的部分**
（投影、头、附件规则）最容易静默漂移；而 VC 语义等另外 90% 必然回到 did-sdk，届时边界失控。

| 维度 | 编译产物（采纳） | 契约植入 |
|---|---|---|
| 语义漂移 | **零** | 靠向量兜，仍有漏网 |
| 客户端新增代码 | ~50 行胶水 × 2 | ~250–300 行 × 2 + parity 测试 |
| 一次性成本 | 发布工程 5 项 | 无 |
| 持续成本 | 版本锁 + 平台矩阵 | 改一次同步三仓 |
| 失败模式 | 平台无 wheel → `did_unavailable`（显式） | 静默分歧 → 对端验不过 |

依赖形态：基础包仍零依赖，`did` 作为可选 extra；无 extra 时 `send_mail` 照常工作，
`sign=True` 返回 `did_unavailable`，**不静默降级**。收件侧同理：无 extra 时策略退化为 `record`。

### 43. 白名单：保留机制，拒绝投递，不改写头

- 白名单/board 过滤**完全保留**；
- **To/Cc 头保持客户端提交的原样**，不改写、不裁剪；
- 对被过滤地址：**拒绝投递**（不进 SMTP 信封、不进 webhook），而不是从头里删掉。

三个边界：判定基准是**最终 To/Cc 头的 addr-spec 集合**（内部收件人进头但不进 `RCPT TO`）；
**显示名归一化允许**（`rh` 只覆盖 addr-spec）；persona 展开往返无损。

**必须补的闭环**：既有行为是被过滤地址"静默消失"，发件方以为都收到了。两条通道：
1. `/send` 响应 `undelivered: ["blocked@example.com"]`（同步，给调用方）；
2. 一封系统邮件给发信地址（durable，给 agent 的规则）：
   `Subject: __aimail_system__:did-undelivered`、`X-AIMail-System: did-undelivered`、
   `X-AIMail-Mail-Id: <原 id>`，正文列出被过滤地址与原因。

**这封系统邮件必须被 SDK 吞掉、不进 LLM**，且三条约束：豁免 DID 裁决；不受白名单影响（否则通知自己被过滤掉，
形成死循环）；不进 outbound 队列（避免递归）。

统一入口按 `X-AIMail-System` 分派三种：`did-approval`（写本地签名/VC）、`did-undelivered`、
`did-revoked`（标记 VC 失效）。

### 44. DKIM 只许加头

`OutboundTransform` 若改写正文，`bh` 必失效。`did-mail` 开启且记录含 `DID-Signature` 时，
对 transform 前后做正文一致性断言，变化则 `error!` 日志（不阻断），契约文档声明该约束。

### 45. 多 `From`：不支持签名，但必须逐地址判定

aimail 结构上产生不了多地址（`send.rs:78-95` 强制 `sender_base == api_key.email_address`；persona 只是前缀变体），
也没有看到有价值的场景 → 不实现多地址签名。

但**接收侧必须逐地址严格判定**（N19）：`From` 有 N 个地址而只有 M<N 个头时，缺头的地址判 `did_absent`，
整封不得因为某个地址验过就整体 `verified`。十几行代码的安全必需项——否则会把伪造邮件误判为已验证。

### 46. 字节约定：为什么以规范为准、让 did-sdk 适配

did-sdk 的 `signing_input` **11 个函数全部**用 `"…\n{末字段}"`（无尾随换行），并有
`assert!(!bytes.ends_with(b"\n"))` 守护；规范统一"每行 `\n` 结尾（含最后一行）"。
两边各自自洽，**没有一方是为兼容对方设计的**。

| 维度 | 尾随 `\n`（规范） | 无尾随（did-sdk 现状） |
|---|---|---|
| 密码学强度 | 等价 | 等价 |
| 字段自定界 | 每字段是完整一行，可 `writeln!` 流式写入 | 末字段隐含"不能拼接" |
| 实现错误面 | 小，无特判 | 大——漏/多一个 `\n` 静默失配 |
| 现有消费者 | 无（规范是新的） | **零**：did-sdk 未发布；旧 API 只在自测用；chain-sdk 从不产生/校验该头 |

头线格式同理：`k=v` 胜在**非 SDK 方能直接读**（MTA、日志、审计），与 DKIM-Signature、
Authentication-Results 同构，且比 `base64url(JSON)` 更短。

**结论**：规范更优且改造成本≈0（零外部消费者）→ did-sdk 做 v0.3.0 新增，旧格式保留并标 deprecated。

---

## Part VII 不变量与检验

这一部分是评审与测试的**唯一打勾清单**。每条都是可判定的断言，不是描述。

### 47. 字节层不变量

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N1** | 签名对象七行，每行（含末行）以 `\n` 结尾 | 向量 `bytes_hex` 以 `0a` 结尾；`len == Σ(line)+7` |
| **N2** | `Content-v1` 首行恒为 `Content-v1` | 向量 |
| **N3** | `body` 已含 agent 尾部，顺序是先拼尾、再哈希 | 向量带"含尾部"与"不含尾部"两例 |
| **N4** | 附件条目数 == `attachment_count`，`{i}` 从 1 连续 | 向量（0/1/2 附件） |
| **N5** | 头解析：先 unfold；unfold 后 ≤998 octets；拒绝重复参数；容忍未知参数 | 单测（构造超长/重复/未知参数） |
| **N6** | `bh`/`rh` 回显与签名对象第 6/7 行**逐字节相等**（同一来源，不许二次计算） | 单测断言同源 |
| **N7** | `rh` 大小写不敏感（全部小写化后才哈希） | 向量（混合大小写） |
| **N8** | 改任一头字段 → 验签失败；改 `bh`/`rh` 回显 → 归因为 `*_hash_mismatch` 而非 `signature_invalid` | 单测（逐字段篡改矩阵） |

### 48. 信任边界不变量

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N9** | 任何进入比对环节的 hash 都由验证方本地计算；**载荷里不出现 hash** | 代码审查 + 载荷 schema 断言 |
| **N10** | 收件方不采信 MTA 的 `bh`/`rh`/签名结论，只用于交叉校验 | 单测：篡改 `results` 不影响 SDK 自己的判定 |
| **N11** | 载荷缺字段 / 附件取不回 / 超长 → `not_verifiable`，**永不等于 `verified`** | 单测（三种降级各一例） |
| **N12** | 系统邮件（`X-AIMail-System`）只接受来自本地配置的网关地址 | 单测：伪造 from 的系统邮件被忽略 |

### 49. 生命周期不变量

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N13** | 锚定 `valid_until` 是有效期权威；VC 的 `expirationDate` 仅客户端自我约束 | 单测（两者冲突时以锚定为准） |
| **N14** | 撤销即时生效，不受 `valid_until` 约束 | 单测 |
| **N15** | 事实锚不可替换：`credentialStatus.anchorId` 在 VC 哈希内 | 单测（改 anchorId → VC 哈希变） |
| **N16** | 服务端拒绝 `revoke` 非本发行方的锚定 | 服务契约测试（不在 chain-sdk 能力内） |

### 50. 投递与路由不变量

| ID | 不变量 | 怎么检验 |
|---|---|---|
| **N17** | To/Cc 头不改写；被过滤地址拒绝投递并双通道回报 | e2e（含 `undelivered` + 通知邮件） |
| **N18** | `DID-Signature` 不剥不改；入口必剥外部 `DID-Auth-Results` | e2e（预置伪造结论） |
| **N19** | 多 `From` 逐地址判定：`From` 有 N 个地址、只有 M<N 个头 → 缺头者 `did_absent`，整封不得为 `verified` | 单测 + 向量 |
| **N20** | DSN / `MAIL FROM:<>` 不参与验证 | 单测（退信路径不产生 `DID-Auth-Results`） |
| **N21** | 双侧（MTA / SDK）独立算出的 `Content-v1` 逐字节相同 | 交叉校验用例；不一致即告警 |

### 51. 收件侧裁决策略（R31）

关键区分：**"没签名"与"签名验不过"是两件事**。前者是兼容性，后者是安全性。

| 情形 | 判定 | 处置 |
|---|---|---|
| 外部客户端不支持 DID | `did_absent` | **照常收** |
| `did` 非 `did:key` / 格式非法 | `did_unsupported` | 照常收 + 告警 |
| SDK 独立验签失败 | `signature_invalid` / `body_hash_mismatch` / `rcpt_hash_mismatch` / `timestamp_out_of_window` | **拒收** |
| 锚定基础设施不可用 | `chain_unavailable` | 照常收 + 标记 |
| 锚定判定失败 | `anchor_not_found` / `_type_mismatch` / `_hash_mismatch` / `_revoked` / `_expired` | **拒收** |
| 附件字节取不回 | `attachment_fetch_failed` | 照常收 + 标记（严格模式可拒） |
| 载荷缺 `body_signed`（旧 gateway） | `not_verifiable` | 照常收 + 告警 |
| 全部通过 | `verified` | 收 |

**"拒收"语义**：不进 LLM、不写常规 inbound 索引；落审计副本（`did/rejected/<mail_id>.json`）+ 日志事件。

**为什么按 `reason` 而不是按 `result`**：规范里 `result` 只有 `verified`/`failed`，而 `did_absent` 也落在
`failed`。按 `result` 一刀切会把所有不支持 DID 的邮件全拒——直接违反"不影响收邮件"（§51）。

**地址维度两种模式**：`delegated`（默认，复用 `results`，零网络）/ `selfcheck`（经 gateway 代理查锚定，一次网络）。
两者都不因 `chain_unavailable` 拒收。

### 52. 缺口补齐（按职责走查发现）

**① 时钟偏差会让 SDK 误拒。** R14 与 R23 都校验 ±300s，但两边时钟不同源；agent 主机漂 5 分钟就会
"网关通过、SDK 拒收"。处置：载荷带 `did.gateway_time`，SDK 以网关时间为基准校验窗口，同时算
`|local_now - gateway_time|`，超 `clock_skew_warn_secs`（默认 60s）告警但不拦截。

**② 一条安全漏洞：可伪造的"系统邮件"。** R21 靠 `X-AIMail-System` 识别指令邮件（改 agent 签名、受理撤销）。
**任何外部发件人都能伪造这个头**，一旦采信，攻击者就能改掉本地 agent 签名。三条约束：
只接受来自本网关地址的这类邮件（N12）；系统邮件必须经 MTA 入口以便审计；内容最小化（只认已登记的字段）。

**③ 多地址 → 每地址一把密钥。** 布局是 `$AIMAIL_HOME/did/<address>/{seed,agent-meta.json,vc/}`，
不是单文件。"一对一"约束的是"一个地址对应一把 DID"，不是"一个 agent 只有一把"。

**④ 过期权威：锚定说了算**（N13）。

**⑤ DSN / 退信不参与验证**（N20）。

**⑥ `did:key` 没有轮换。** 换 DID = 换身份 = 重新绑定；私钥丢失同理。必须写进用户文档，
否则会被当成"可以重置密码"。

**⑦ 边界情形清单（进向量）**：0 附件 / 空正文 / 只有 HTML 无 plain / 超长 subject / CRLF 与 LF 混用 /
文件名含 `\x00` 与换行 / QP 与 base64 两种编码的同内容 / 附件被重排 / 附件被改名 / 多 `From` 部分缺头 /
载荷缺 `body_signed`。

### 53. 入站管线顺序（pysdk 与 tssdk 必须一致）

```text
1. 系统邮件识别（X-AIMail-System / __aimail_system__）→ 处理并吞掉
   └ 豁免 DID 裁决（这类邮件由 gateway 发出，没有 agent 私钥）
2. ping / pong 拦截 → 吞掉
3. DID 独立验签 + 裁决 → 拒收（留审计副本，吞掉）或放行
4. 常规 preprocess（bodyproc 结果已在 payload["body"] 里）→ 交给 LLM
```

顺序不能调换：系统邮件放行到第 3 步会被当作 `did_absent`（污染裁决统计）；
DID 裁决若晚于第 4 步，拒收就成了"已经进过 LLM 再拦截"，等于没拦。

---

## Part VIII 代码映射

**规则：这里不出现 Part IV 之外的新职责。** 映射时若发现需要新职责，先补 Part IV。

### 54. did-sdk（v0.3.0）

| 职责 | 落点 |
|---|---|
| R5/R6/R7/R8 的密码学与编码 | `keyvault-core::signing_input::did_signature_mail_v1`（七行，尾随 `\n`）+ `Content-v1` 组装 + 头编解码 + Ed25519 |
| R1 密钥 | 已有 `FileKeystore`（`client-did/`），加"按地址分目录"的封装 |
| R2 VC 钱包 | 已有 `DVCS` 信封 |
| R23/R26/R27/R28 验签原语 | 新增 `verify_mail_signature(...)`（gateway 与 SDK 共用同一实现） |
| 发布工程 | `abi3-py39`；补 `x86_64-apple-darwin` / `aarch64-unknown-linux-gnu` / Node triple；PyPI + npm publish |

### 55. aimail-gateway

| 职责 | 落点 |
|---|---|
| R12–R17 | 新模块 `src/core/did/{mod,signature,content,results}.rs`；接在 `process_data_end_post` 的**退信分支之后、MIME 解析之前** |
| R15 附件字节 | 复用 `walk_mime_parts` 产出的 `MimeAttachment`（已有 `content`/`filename`） |
| R16 原始 To/Cc | `receiver.rs:893-903` 增保留未过滤的 `to`/`cc` 原始头 |
| R18 锚定查询与生命周期判定 | **`crates/mail-anchor`**（`AnchorStore` trait + 生命周期判定）；gateway 只提供 `ChainSdk`(Verifier) 的 trait 实现 + TTL 缓存 + 剩余 <24h 强制穿透 |
| R18/R37 `payload_hash` preimage | **`crates/mail-anchor`**（`email_binding::preimage` / `domain_control::preimage`，§39） |
| R19 | `src/core/did/results.rs` + 写入记录头 |
| R20 | `records.rs:110-140` 增 `did` 对象（`body_signed` = `self.body`） |
| R9 | `send.rs:745-771`：不改写头；被过滤地址拒绝投递 + `undelivered` 回报 |
| R10 | `mx_deliverer.rs:26` 白名单加 `DID-Signature` |
| R11 | `mx_deliverer.rs:286` / `sender.rs:352` 后加正文一致性断言 |
| R34–R40 | 新 `src/core/api/did_binding.rs`（独立端点，不动 `activation`/`email_verify`）；业务逻辑调 `crates/mail-anchor`，端点本身只做鉴权/入参/落库 |
| R38 VC 组装 | **`crates/mail-anchor`** 的 `VcIssuer` trait；gateway 用 did-sdk `Server` 实现该 trait |
| R35 挑战签名 | **复用 `GatewayAccess-v1`**（§36） |
| R36a/R36b | **`crates/mail-anchor`**：`domain_control` 的 preimage + TXT 解析 + 控制权核对；gateway 提供 `DnsResolver`（`hickory-resolver` 已在依赖，目前只用于 MX）与 `AnchorStore` 实现 |
| R39 指令邮件 | 复用 `send` 内部路径；`X-AIMail-System` 头；内部投递不受白名单影响 |
| R41 | `Metrics` 增六个计数器 |
| feature | `did-mail = ["dep:chain-sdk", "dep:did-sdk"]`，默认关闭 |

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

### 56. pysdk

| 职责 | 落点 |
|---|---|
| R1–R8 | 新 `pysdk/aimail_did.py`（薄）：惰性导入 `did_sdk`、`build_header()`、`DidWallet`（按地址目录） |
| R5 附件哈希 | 对**本地文件字节**求哈希 |
| R21–R33 | `aimail_base.py` 入站链路：`handle_system_mail()` → `handle_ping_pong()` → `handle_did_verdict()` |
| R25 附件下载 | 复用已有 `GatewayClient.download_attachment()`（`aimail_tools.py:355`） |
| R42 | `aimail did status` |
| 打包 | `[project.optional-dependencies] did = ["did-sdk-client>=0.3,<0.4"]`；基础 `dependencies = []` 不变 |

### 57. tssdk

| 职责 | 落点 |
|---|---|
| R1–R8 | 新 `core/did-signature.ts`、`core/did-wallet.ts`（薄，动态 `import('@did-sdk/client')`） |
| R25 附件下载 | **新增** `GatewayClient.downloadAttachment(id)`（当前缺失） |
| R21–R33 | `core/preprocess.ts` 三级管线（§53） |
| R42 | CLI/工具层状态命令 |
| 打包 | `optionalDependencies: { "@did-sdk/client": "^0.3.0" }`；`dependencies: {}` 不变 |

### 58. 组件边界：仓内独立 crate（已定）

`EmailBinding` + `DomainControl` 的语义**不能**放进 did-sdk（违反 R1：无链访问），
也**不能**放进 chain-sdk（它声明"不是钱包、不含 VC 存储/治理"），埋在 gateway 里又不可复用、难单测。
所以这一层独立存在。

| 层 | 归属 | 内容 |
|---|---|---|
| 邮件层（跨语言） | **did-sdk v0.3.0** | `Content-v1`、头编解码、签名/验签、`GatewayAccess-v1` |
| 锚点层（Rust only） | **`crates/mail-anchor`**（gateway 仓内 workspace member） | `EmailBinding`/`DomainControl` 的 preimage、DNS TXT 解析、生命周期判定、VC 组装 |
| 接线层 | **gateway** | HTTP、SMTP、SQLite、feature 门控、指标 |

#### 58.1 crate 形状

```text
aimail-gateway/
  Cargo.toml                 ← 新增 [workspace] members = [".", "crates/mail-anchor"]
  crates/mail-anchor/
    Cargo.toml               ← 依赖：did-sdk、chain-sdk、sha2/blake2、serde；**禁** axum / rusqlite / mailin / reqwest
    src/lib.rs
    src/email_binding.rs     preimage 构造 + 校验（§39）
    src/domain_control.rs    preimage + TXT 解析 + 控制权核对（§34）
    src/lifecycle.rs         revoked / valid_until / max_anchor_validity_days（§43）
    src/vc.rs                EmailBindingVC 组装（§40 的 VC 结构）
    src/ports.rs             AnchorStore / DnsResolver / VcIssuer 三个 async trait
```

三个 trait 是**I/O 边界**，由 gateway 实现：

| trait | gateway 侧实现 | 测试侧实现 |
|---|---|---|
| `AnchorStore` | chain-sdk `Centralized`（或 `Chain`，将来） | chain-sdk `Mock`（`MockStateHandle` 共享状态） |
| `DnsResolver` | `hickory-resolver`（已在依赖） | 固定应答的桩 |
| `VcIssuer` | did-sdk `Server`（`assemble_vc_with_proof`） | 固定密钥的桩 |

#### 58.2 硬边界（三条）

1. **`Content-v1` / `DID-Signature` 头 / Ed25519 验签不得进这个 crate**——那是三语言共用的字节契约，
   必须在 did-sdk，否则又出现两个 Rust 实现（§42）。
2. crate 内**不得出现** `axum` / `rusqlite` / `mailin` / `reqwest`；所有 I/O 走三个 trait。
3. gateway 主 crate 加载它，而不是复制其逻辑——`src/core/did/` 只保留
   "从 MIME 重建 `Content-v1`"（R15/R16，需要原始 MIME）与"注入头"（R19）这类**接线**职责。

#### 58.3 收益与提取时机

收益：① 发行方与验证方共用同一份 preimage 实现，preimage 漂移在物理上不可能；
② 可用 `Mock` + 假 DNS 做全流程测试，不依赖真链、SMTP、SQLite。

现在**不发布**（不推 crates.io）。触发抽成独立仓的条件：出现第二个消费者
（第二套 MTA、域名管理控制台、合规审计工具，或 chain-owner-sdk 需校验委托锚定）。

---

## Part IX 执行面

### 59. 分期

| 期 | 内容 | 影响面 | 回滚 | 验收 |
|---|---|---|---|---|
| **P0** 契约 | 契约文档 + 向量 + 本仓镜像；`did-mail` feature 骨架；`crates/mail-anchor` 骨架（三个 trait + preimage 空实现） | 无 | 删文件 | 三仓读通向量；crate 可 `cargo test` 空跑 |
| **P1** did-sdk v0.3.0 | abi3、3 个 target、PyPI、npm、`Content-v1` + 新头 | 独立仓 | tag 回退 | 7 平台可装；向量全绿；依赖树无 `subxt` |
| **P2** 接收侧记录 | R12–R20（`policy=record_only`），原始 To/Cc 保留 | 入站头多一行 | `enabled=false` | N1–N8、N20；附件篡改/重排/改名必失配 |
| **P3** 地址真实性 | R18 接 `Centralized` + 缓存 + 穿透 + 超时 + 指标 | 每封多一次 HTTP（≤1.5s） | 同上 | 六个 address reason + 缓存命中/穿透 |
| **P4** 发件侧 | R4–R11；payload `did` 对象；`undelivered` + 通知邮件 | 出站组装、收件人回报、入站载荷均变 | `sign=False`；幂等补丁可留 | 端到端：SDK 签 → 纯转发 → 回灌 → **SDK 自己验出 verified** |
| **P5** 移除服务端签名 | 删 `send.rs:100`、下线存储字段与 `whoami`/`agent-meta` 字段 | **破坏性** | 保留列与逻辑 | 双尾部/空尾部回归；非 SDK 调用方文档 |
| **P6** 收件侧独立验签 | R21–R33（先 `record` 再 `enforce`）、R30 交叉校验、§52① 时钟 | agent 可能拒收（默认 `enforce`） | 策略切 `record`/`off` | N9–N12、N21；§51 全情形；**拒收必须由 SDK 自身验签触发** |
| **P7** 发行侧 | R34–R40，含 R36a/R36b | 仅新增管理端点 + **域名接入运营要求**（发 TXT） | 端点级移除 | 绑定→`DomainControl`→`EmailBinding`→VC→落盘→发信 verified；缺 TXT 时创建失败 |
| **P8** 策略与运营 | `policy=reject_failed`、灰度、看板 | 可拒绝投递（默认关） | 配置回退 | 灰度开关 + 指标快照 |

**每期门禁**：gateway `fmt` / `clippy -D warnings` / `cargo test`（无 feature 零回归）/
`cargo test --features did-mail` / `build --release --features did-mail`；
pysdk `pytest` + `import aimail` + **`dependencies = []` 不得变**；
tssdk `pnpm test` + `pnpm build` + **`dependencies: {}` 不得变**；
did-sdk `fmt`/`clippy`/`test --locked --all-features`/绑定测试。

**首个嵌入者门禁**（aimail 是 did-sdk 第一个使用者）：CI 只从 registry 装
（`pip install agent-aimail[did]` / `npm i agent-aimail`），跑签名回路 + 7 平台 import/require 冒烟；
不装 extra 时必须报 `did_unavailable`。

### 60. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| P1 拖延 | P4/P6 无法落地 | P1 独立可交付；P2/P3 不依赖它 |
| **P5 破坏性变更** | 非 SDK 调用方失去自动署名尾部 | 三步迁移（§41）；过渡期幂等补丁 |
| **伪造系统邮件** | 攻击者改本地 agent 签名 | §52② 三条约束 + N12 |
| **时钟偏差** | SDK 误拒合法邮件 | §52① 以网关时间为基准 + 偏差告警 |
| 附件被中间 MTA 重命名/重排 | 验签失败（这是期望行为） | 契约写明"改附件即视为篡改"；reason 带附件维度 |
| 收件侧误判 | 合法邮件被拒 | 先 `record` 灰度；`chain_unavailable`/取附件失败不拒 |
| 附件下载延迟 | 大附件拖慢预处理 | 正文先验、附件后验；失败降级 `not_verifiable` |
| `body_signed` 体积/隐私 | 载荷带被剥掉的引用历史 | 上限 1 MiB；不进 LLM；**不改发哈希** |
| 多地址只验一个就整体通过 | 伪造邮件被误判已验证 | N19 列为 P2 必测 |
| **域名所有者不发 TXT** | `DomainControl` 校验不过 → EmailBinding 无法创建 | 接入流程列为必做步；返回 `domain_control_required`；提供一键打印待发布记录 |
| DKIM 改正文 | 必失效 | 出站断言 + 文档约束 |
| 两侧算法漂移 | 全量误判 | 同一份向量三端共用 + N21 交叉校验 |
| 跨仓版本锁 | 升级需两侧同步 | 写进 `scripts/check-versions.sh` |

### 61. 对规范的修订清单

| 规范条款 | 现状 | 需要改成 | 依据 |
|---|---|---|---|
| §2.1 字段表 | 只有 `v/did/t/s/vc` | 增 `bh`、`rh` | §31 |
| §2.3 正文哈希 | DKIM simple/relaxed 规范化 MIME 正文 | `Content-v1` 里的 `body_hash` | §29 |
| §2.3 附件 | **未覆盖** | 增附件条款：各字段先求哈希再入投影 | §29 |
| §2.4 收件人哈希 | 取自 SMTP 会话 `RCPT TO` | 取自客户端提交的可见 To∪Cc | §30 |
| §2.2 签名对象 | 七行，尾随 `\n` | **不变**（did-sdk 适配） | §46 |
| §5.3 reason 表 | 完整性项依赖头里不存在的值 | 增补 `bh`/`rh` 后自洽 | §31 |
| §6 3.i–3.k 顺序 | 先验签再比对第 6/7 行 | 先比对回显得归因，再验签确认 | §31 |
| §9.1 约束 2 | 「签名前必须用户确认」 | 「身份绑定须人工审批（签发 VC）；发信签名由 VC 授权，不逐封确认」 | §41 |
| §9.3 接收方能力 | 只要求 MTA 验证并注入 | 增：**最终消费者应能独立复验**，协议须明确"验证可用输入"清单并规定服务商原样投递 | §38、§13 |
| §5.1 整体结论 | 只有 `verified`/`failed` | 增 `not_verifiable` 语义，或明确"缺输入 ≠ 通过" | §51 |
| §1.1-5 / §8.2 | 只约束"不得代签" | 增：**服务商也不得代拼正文尾部** | §41 |
| §3.3 `IssuedVc` preimage | 五行明文 `(issuer_did, subject_did, vc_type, vc_hash, delegate_issuer_id)` | **与代码不一致**：`anchor_payload_hash` 是 BLAKE2b over VC 的 compact JSON，且已被 `vp_sd` 与 `sd.rs:352` 依赖。本期不用，启用前须二选一并同步 | §39、§5 |
| §3.4 / §4.1 摘要算法 | 锚点层 SHA-256 明文行 | **锚点层改 BLAKE2b-256**（与链侧一致），邮件层 §2.3/§2.4 仍 SHA-256 | §39 |
| §3.2 `issuer_id` | 未指明对应哪个链上字段 | 明确 `≡ AnchorRecord.issuer_did`，并注明"先查记录再重建" | §40 |
| §2.5 多 `From` | 每地址一个头实例 | 保留条款；注明 aimail 不产生多地址签名，接收方须逐地址判定 | §45 |

### 62. 决策状态：全部锁定

| # | 决策 | 结论 |
|---|---|---|
| 1 | **原语按层划分**（§39） | 链上承诺 `blake2b:`；邮件内摘要 `sha256:`。规范只改 §3.4/§4.1，did-sdk 不改代码 |
| 2 | **`issuer_id ≡ AnchorRecord.issuer_did`**（§40） | 淘汰 `submitter_id`（随代理跳变 → 续期后旧 VC 全失配）与 `delegated_by_did`（自提交时为空） |
| 3 | **锚定范围**（§33） | 做 `EmailBinding` + `DomainControl`；`IssuedVc` 不锚、`GatewayAccess` 只用签名对象、其余 5 种属别的角色职责 |
| 4 | **组件边界**（§58） | 锚点层做成 gateway 仓内独立 crate `crates/mail-anchor`（不发布），I/O 走三个 trait |

**随之接受的两条外部前置**（不是代码）：

- 每个接入域名的所有者必须发布 `_email-verification.<domain> TXT "ev-anchor=chain:<anchor_id>"`（§34）；
- 中央化锚定服务由你方提供，接口见 §37（本项目无新增），且**必须校验锚定归属**（§35-②）。

**方案已无待确认项。** 下一步是 P0（§59）：契约文档 + 字节向量 + `did-mail` feature 骨架 + crate 骨架，
不改任何现有行为。
