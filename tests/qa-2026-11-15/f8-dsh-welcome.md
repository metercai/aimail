# F8｜DSH SSE 试点：welcome 闭环未完成（2026-11-16）

- 出具：Verifier
- 被测：aimail `2dc2e5c`（含 CLI 0.1.63）· gateway `a637edb` · advanced `47d1d8d`
- 结论：**HOLD（仅 DSH 试点）**；已发布制品不受影响

## 1 现象

L3 的 dsh 两格（dsh-aimail / dsh-plugin）稳定红在同一处：

```
✗ welcome closed loop failed (rc=1) — Gateway: http://127.0.0.1:18123
  ✗ No reply within 300s (~/.aimail/systems/<sid>/agent_l3.local/agentmail.log)
  ✗ discovery: no agents found for platform 'dsh' (system )
  ✗ platform-locatable: platform root not locatable (system_home missing/invalid)
```

## 2 已排除的变量

| 变量 | 处理 | 结果 |
|---|---|---|
| 旧 CLI（0.1.62，push 期安装逻辑） | bump 0.1.63 重新发布（含 2dc2e5c） | 仍红，排除 |
| L3 夹具仍在等 9099 | 改为"9099 必须关闭 + status.json mode=sse + 双配置 0600" | 通过（不再卡在 9099） |
| 我的补丁误伤 hermes | 还原 hermes 段后重跑 | hermes 三平台转绿（linux-amd64/arm64/windows） |

## 3 判定

```
1 hermes 路径全绿 ⇒ 已发布 SDK/CLI 对 push 模式有效
2 dsh 两格红在"首封 welcome 300s 无回复"，且新 CLI 复现
   ⇒ 不是夹具、不是旧 CLI，而是 **SSE 模式下 welcome/激活下发未闭环**
3 另有一条独立异常：discovery 的 system 为空（'dsh' (system )），
   疑似 pull-mode 注册后系统绑定未写回 —— 可能是 welcome 无回复的直接原因
```

## 4 需要开发回答（决定修法）

```
Q1 SSE 模式下 welcome/激活消息是否应通过同一条 SSE 流下发？
   是 → 服务端下发路径缺失，需修（试点核心流程）
   否 → 试点无法完成首次激活闭环，需重新定义试点范围
Q2 为何 `discovery: no agents found for platform 'dsh' (system )` 里 system 为空？
   是否 pull-mode 注册未写回 system 绑定（manifest/pointer）？
```

## 5 复现

```
publish.yml 触发（dry_run=false）→ 等 cli-release 产出 v0.1.63
L3: gh workflow run l3.yml --ref main -R metercai/aimail
观察 dsh-* cell 的 [5] welcome closed loop 段
run 参考：43834255951（含 2dc2e5c 的 CLI）
```

## 6 发版结论（不受 F8 影响）

```
gateway 1.0.7 · advanced 1.0.11 · tssdk agent-aimail 0.2.13 · pysdk 0.2.9 · CLI 0.1.63 —— 均已发布并核验
F8 仅阻塞"DSH SSE 试点通过"这一项签字
```

## 7 立场

```
不使用"放宽 L3 断言"的方式让 dsh 转绿；F8 澄清并修复后重跑 L3，再给试点签字
```
