# 激活码逻辑复用审计（base gateway ↔ advanced）

日期：2026-11-23  
范围：`aimail-gateway/src/core/api/activation.rs`、`aimail-advanced/src/advanced/api/address.rs`

域名来源与三种地址类型的现状见 `docs/qa/ACTIVATION-DOMAIN-AUDIT.md`。

---

## 1. 结论

**advanced 不能直接调用 base 的 `activate_address_code` 完成地址码激活。**  
直接复用会丢掉 advanced 已有的并发/续期语义，属于行为回退。

但可以安全复用 base 的**纯逻辑**，本轮已落地两项：

1. `address_activation_prompt()`：激活邮件 prompt 的唯一真源，base/advanced 共用。
2. `generate_key_material()`：agent key 生成 + `seal::store_hash` 存储哈希策略，base/advanced 共用。
3. `generate_activation_code()`：激活码随机格式的唯一实现，base/advanced 共用。
4. `valid_agentname()`：agentname 规则唯一实现，base/advanced 共用。

事务体（`act_in_tx`）仍保留在 advanced，原因是它必须在一个 SQLite 写事务内完成 advanced 特有的检查与写入。这是**服务/事务策略**的保留，不是工具函数重复。

---

## 2. 为什么不能直接调用 base `activate_address_code`

| 差异 | base | advanced |
|---|---|---|
| 原子性 | 异步多步：lookup → claim CAS → upsert → domain → manager/whitelist → delete | **一个 `rusqlite` 写事务**：所有步骤全有或全无 |
| 并发双码同地址 | 只防同一 code 被兑换两次；两个不同 code 可并发写同一地址 | 事务内 anti-double-active + UNIQUE upsert；明确区分 new / renewal / rotation |
| 共享域裸域校验 | 无 advanced 的 `system_domains` bare-domain 冲突检查 | 有：`shared_addr_*` + bare domain 必须同 system |
| 续期/轮换 | 没有 active-key 检查；upsert 会覆盖 | 有：active key + 新 code = renewal/rotation，保留地址语义 |
| 返回 | `(raw_key, key_id)` | `raw_key/system_id/expires_at/manager_address/kind` |
| 配置 | 无 validity_days | 按 shared domain 的 `validity_days` 写 expiry |

因此 advanced 顶部注释保留：

```text
activation flow deliberately does NOT call base activate_address_code
```

这不是重复代码的“偷懒”，而是 base 的 async 多步 API 无法在 advanced 的事务边界内复用。

---

## 3. 本轮已消除的重复

### 3.1 邮件 prompt

base 新增：

```rust
pub fn address_activation_prompt(address, gateway_url, code) -> String
```

base 的 `address_activation_email` 与 advanced 的 `send_address_code_email` 都调用它。  
以后改文案只改这一处，不会再出现 base/advanced 文案漂移。

### 3.2 激活码生成

base 新增 `generate_activation_code(prefix)`，base 的 `random_code()` 只是它的薄包装；advanced 删除本地同名实现，直接 `pub(crate) use` base 函数。

### 3.3 agent key 生成 + 存储哈希

base 新增：

```rust
pub fn generate_key_material() -> (raw_key, stored_hash, prefix)
```

- base 两处 key 创建（地址码、系统码）改为调用它；
- advanced `act_in_tx` 改为调用它；
- 存储哈希统一走 `seal::store_hash`：admin key 已加载时 sealed，未加载时降级 plaintext 并 warning；
- `verify_api_key` 同时接受 sealed 和历史 plaintext，兼容旧数据。

这消除了此前 base 写 plaintext、advanced 写 sealed 的潜在漂移。

### 3.4 agentname 规则

base 的 `apply_address_public` 与 advanced 的 `open_apply` 共用 `valid_agentname()`；advanced 不再维护第二套正则/长度规则。

---

## 4. 仍需保留的重复（有意）

`act_in_tx` 内部以下逻辑仍为 advanced 专属，不能简单抽成 base async helper：

- `activation_codes` 的 unclaimed/unfrozen/unexpired 查询；
- `system_domains` bare-domain 冲突；
- active-key 计数与 new/renewal/rotation 判定；
- `api_keys` 的 `ON CONFLICT(system_id, domain_addr)` upsert；
- `domain_addr_meta` manager 保留规则；
- `whitelists` 的 effective-manager upsert；
- 同一事务内删除 code。

这些是 advanced 的**事务策略**，不是通用工具函数。把它们搬进 base 会迫使 base 引入 advanced 的配置/返回类型，反而扩大耦合。

---

## 5. 后续防漂移建议

1. 文案/格式：只允许通过 `address_activation_prompt()`，禁止再复制字符串。
2. key 生成：只允许通过 `generate_key_material()`，禁止在激活路径手写 `rand/hex/store_hash`。
3. 激活码/agentname：只允许通过 `generate_activation_code()` / `valid_agentname()`，禁止再写第二套格式规则。
3. 任何 base/advanced 激活语义变更，必须同时跑：
   - base `cargo test --lib`
   - advanced `cargo test`
   - L2 `s1 s2 s3`（至少 dsh）验证 address-code → binding → welcome
4. 若未来要把事务体上收，先设计一个 base 的**同步事务 API**（接受 renewal/bare-domain policy），不能在 async handler 里直接调用 base 多步函数。

---

## 6. 本轮验证

```text
base:     cargo test --lib 416 passed
          cargo fmt --all --check / clippy -D warnings PASS
advanced: cargo test 146 passed
          cargo fmt --all --check / clippy -D warnings PASS
```

prompt 的精确断言在 base `activation_status_tests::email_templates_are_english_and_advanced_aligned`。
