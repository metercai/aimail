# 第 4 轮发版验收结论（验证方）：PASS

> 出具方：验证方　接收方：实现方 / owner
> 对象：`aimail` `8b6bb36`（代码 `86837dd`）· `aimailsdk` 0.1.56 · CLI `cli-v0.1.56` · npm 五包 · gateway `v1.0.4` · advanced `v1.0.2`
> 判定：**PASS，已发布**（dispatch 已执行，`dry_run=true` → `dry_run=false`）
> 签字证据口径：**L2 由验证方亲自跑**；L0/L1 仅作参考。

---

## 0. 一页结论

| 项 | 结果 |
|---|---|
| 发布 run | [39295538195](https://github.com/metercai/aimail/actions/runs/39295538195) @ `8b6bb36` — **26/26 job success**（含 L3 12 格） |
| dry-run 形状核对 | [39294119007](https://github.com/metercai/aimail/actions/runs/39294119007) — gate 5 ✓ / meta ✓ / cli-build 4 ✓（含 windows .exe）/ npm ✓（5 包 pack，包内 SKILL.md 在位）/ pypi-build ✓；cli-release、pypi、l3 按设计 skipped；未创建任何 tag |
| CLI 资产 | 4 个，逐个下载 + sha256 + 运行验证（见 §1） |
| PyPI | 0.1.56，provenance = `metercai/aimail` / `publish.yml` / `pypi-release` |
| npm | 五包全部上架到目标版本（见 §2） |
| 依赖序 | `check-versions.sh` **不带** batch 转绿（pending 已落地） |
| L3 | 12/12 格 PASS（4 平台 × hermes / dsh-aimail / dsh-plugin） |
| 未做 | gateway/advanced 部署（另需授权）；docker 五宿主回归；bridge 不在本批 |

---

## 1. CLI 资产（`cli-v0.1.56`，全部由验证方下载后实测）

| 资产 | 字节 | sha256 |
|---|---|---|
| `aimail-0.1.56-linux-amd64` | 7,098,760 | `34a8dc3691c6ecf156c78a5180324c8ff7500fabcbe19e37deedbb8b07fe80ca` |
| `aimail-0.1.56-linux-arm64` | 6,605,280 | `efb7ec83a6191ac3e7f091824ac43edd9045e2a4ea0d8c5c00e8cd0473de55b7` |
| `aimail-0.1.56-macos-arm64` | 5,925,904 | `cc57d4d0bd0e1ef79ae8b9818657365e8315b23928701c2fd13e2ea077d5768d` |
| `aimail-0.1.56-windows-amd64.exe` | 6,056,448 | `b067ef8f30b06947eae94fa8250f1e65ca9a25933a07c90c460eb92804ffb9c7` |

实测（下载自 GitHub Release，非 CI 缓存、非本机构建）：

```
$ ./aimail-0.1.56-linux-amd64 version          → aimail 0.1.56 (dev)
$ file …                                        linux-amd64: ELF 64-bit, static-pie linked
                                                linux-arm64: ELF 64-bit ARM aarch64, statically linked
                                                windows-amd64.exe: PE32+ (console) x86-64
$ HOME=<空> PATH=<bare venv python> \
  ./aimail-0.1.56-linux-amd64 agent setup --host hermes
                                                → rc=0，commands=1（真实 pip/uv 命令），reason=null   ← D8 门在发布产物上通过
$ ./aimail-0.1.56-linux-amd64 agent doctor --questions
                                                → rc=0，27 行
```

## 2. PyPI 与 npm

```
PyPI  aimailsdk 0.1.56
      wheel aimailsdk-0.1.56-py3-none-any.whl  upload_time 2026-10-19T17:41:16.138678Z
      sha256 2a8cd1b813eaf9fc…
      provenance: publisher=GitHub repo=metercai/aimail workflow=publish.yml environment=pypi-release
```

| 包 | 之前 | 现在 |
|---|---|---|
| `@aimail/mail-core` | 0.1.54 | **0.1.55** |
| `@aimail/mail` | 0.1.54 | **0.1.55** |
| `dsh-aimail` | 0.1.58 | **0.1.59** |
| `openclaw-aimail` | 0.1.57 | **0.1.58** |
| `pi-aimail` | 0.1.56 | **0.1.57** |

## 3. L2 证据（验证方亲跑，安装产物经真实门）

| L2 项 | 方式 | 结果 |
|---|---|---|
| PyPI wheel → 干净 venv | `pip install aimailsdk==0.1.56`（index=pypi.org） | import 落点 `…/site-packages/aimail/__init__.py`，`agent_toolset()` = **15** |
| npm tarball → 空项目 | `npm i @aimail/mail-core@0.1.55` | `agentTools()` = **15**，包内 `resources/skills/SKILL.md` 在位 |
| A1 l2-py（装后产物 × 通用 Python 宿主） | `tests/SDK/agent-level/gate.sh` | **14/14** |
| A1 l2-ts（装后产物 × 通用 Node 宿主） | 同上 | **14/14** |
| CLI 发布资产真实门（§1） | 干净 HOME + bare python | `agent setup --host hermes` rc=0；`agent doctor --questions` rc=0 |
| 依赖序 | `scripts/check-versions.sh`（不带 batch） | rc=0 `[L1] PASS`（含 skip 已上架 0.1.58/0.1.57） |

参考项（不计入签字）：CI `cli-gates` / `sdk-gates` 在 `8b6bb36` 上绿；A1 的 L1 29/13 亦绿。

## 4. L3（12 格，全部 success；抽两格核过内容）

```
4 平台 × 3 agent：linux-amd64 / linux-arm64 / macos-arm64 / windows-amd64
                  × hermes / dsh-aimail / dsh-plugin
抽检 windows-amd64/hermes    agent-env verify 16 passed 0 failed 0 skipped → L3 PASS
抽检 macos-arm64/dsh-plugin  agent-env verify 16 passed 0 failed 0 skipped → L3 PASS
```
已知条件性提示（非失败，符合设计）：windows 格 `route skipped for agent@l3.local: no bridge config …`
—— bridge 不在 L3 闭环依赖面。

## 5. tag 与版本口径（owner 2026-10-20 裁定）

- `aimail-advanced`：删除老产品线 tag `v1.0 / v1.1 / v1.3 / v1.4 / v1.5`（本地 + 远端），
  新建 **`v1.0.2` → `339069e`**（产品代码 `06cb540` + 验证方夹具修复；`aimail-advanced` 二进制 md5 仍 `c5075b2b`）。
- `aimail-gateway`：**`v1.0.4` → `24d220f`**。
- `aimail`：CI 自建 `cli-v0.1.56`。

## 6. 回滚

- CLI：重 dispatch `cli-v0.1.55`；`agent-install.sh --version cli-v0.1.55` 可钉住。
- PyPI：`aimailsdk==0.1.55` 仍在。
- npm：逐包回退；三个适配包必须与 `@aimail/mail-core` 同批回退（bundle 内 SKILL.md 语义一致）。
- gateway/advanced：tag 保留、Release 可下架；本批无 DB schema 变更。部署回滚命令见提交 §5（部署前须确认 `.prev-<oldver>` 存在）。

## 7. 未验证 / 不在本批

- **gateway 1.0.4 与 advanced 1.0.2 的部署**：未授权，未执行。部署前须：从 `v1.0.4` / `v1.0.2` 重建 →
  预快照（运行中二进制 hash、服务状态、健康基线）→ 原子替换 → 冒烟 → 具备回滚点。
- docker 五宿主回归：仅 hermes 镜像已 PASS，其余四平台待镜像重建。
- pi / dsh / openclaw / deerflow 真机冒烟（非容器）。
- `SDK/sdk-e2e`：需 `RELAY_BIN`/`BRIDGE_BIN`/`TEST_MANAGER`，未纳入。
