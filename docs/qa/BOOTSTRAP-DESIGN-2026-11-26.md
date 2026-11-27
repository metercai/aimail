# SDK 自举方案讨论（L3 地基）：镜像事实 → 脚本方案 → 待定项

- 日期：2026-11-26 · 状态：**讨论稿，未改代码**
- 目的：让"激活 prompt → agent 自助装 SDK"在五类宿主 + 三平台上都可复现、可归因、无歧义

---

## 1. 镜像事实（本机实测，`docker run --entrypoint bash`）

| 镜像 | 用户 | python (`python3`) | `python3 -m pip` | uv | node/npm/pnpm | 宿主 CLI | SDK 预装 | LLM 配置 |
|---|---|---|---|---|---|---|---|---|
| `aimail-host-dsh` | root(0) | 3.12 `/usr/local` | ✓ | ? | ✓/✓/✓ | `dsh` | **absent** | `/root/.dsh/profiles/web/cordis.patch.yml` |
| `aimail-host-pi` | root | 3.12 `/usr/local` | ✓ | ? | ✓/✓/✓ | `pi` | **absent** | `/root/.pi/agent/models.json` |
| `aimail-host-openclaw` | **node(1000)** | 3.13 `/usr/bin` | **✗ No module named pip** | ✗ | ✓/✓/✓ | `openclaw` | **absent** | `/app/.openclaw/openclaw.json` |
| `aimail-host-hermes` | root | 3.14 `/opt/hermes/.venv` | **✗ No module named pip** | ✗ | ✓/✓/**✗** | `/opt/hermes/bin/hermes` | **absent** | `/opt/data/.hermes/config.yaml` |
| `aimail-host-deerflow` | root | 3.12 `/usr/local` | ✓ (pip 25.0.1) | ✓ | `/usr/bin/node`、npm、**无 pnpm** | **无**（后端服务） | **absent** | `/app/config.yaml` |

镜像都是自己生成的分层（`FROM` 官方基础镜像 + `prepare.sh` 写 LLM 端点/模型 + `verify.sh --offline` 验），`CMD ["sleep","infinity"]`，所以 **LLM 是"端点+模型已烘焙、key 运行时由 `LLM_API_KEY` 注入"**（hermes 的 `config.yaml` 里是 `api_key_env: "LLM_API_KEY"`）。L2 夹具用 `docker exec` 驱动，不是 entrypoint。

### 1.1 两个直接推翻"单条命令"的发现

1. **hermes 的 venv 里没有 pip**，PATH 上也没有 pip。真正能装的是它的 **tools python**：
   ```
   /opt/hermes/tools/python-3.14.9+20261021-linux-x64/bin/python3 -m pip   # pip 26.2.2 ✓
   ```
   所以之前配方里的 `pip install agent-aimail` 在 hermes 镜像里**根本跑不起来**（宿主机上它跑了，但落到 miniconda —— 同一个坑的两面）。
2. **openclaw 跑在 uid 1000 且没有 python pip**：它是 JS 宿主，必须走 `openclaw plugins install`（npm），任何 pip 方案对它都无意义。

### 1.2 容器内网络

`getent hosts pypi.org` 在容器里解析不到 ⇒ 本地容器 loop **不能依赖 PyPI**，必须从**本地产物**装（L2 夹具正是挂 `/opt/sdk` 里的 wheel）；CI runner 有网，装注册表版本。

---

## 2. 每宿主的"真实"安装目标（按上表修正）

| 宿主 | 安装命令（修正后） | 自检命令（同一解释器！） | 为什么 |
|---|---|---|---|
| dsh | `dsh plugin --profile web add agent-aimail` | `dsh plugin list \| grep aimail` | node 宿主，插件归 dsh 管 |
| pi | `pi install npm:agent-aimail` | `grep -o '"@aimail[^"]*"' ~/.pi/agent/settings.json` | node 宿主 |
| openclaw | `openclaw plugins install agent-aimail --force --accept-capabilities` | `openclaw plugins list \| grep -i aimail` | uid 1000、无 pip |
| hermes | `"$(ls -d ~/.hermes/tools/python-*/bin/python3 \| head -1)" -m pip install agent-aimail` | 同一 `PY` 跑 `-c "import importlib.metadata…"` | venv 无 pip，tools python 才有 |
| deerflow | `python3 -m pip install agent-aimail`（或 `uv pip install --system`） | `python3 -c "import importlib.metadata…"` | 后端就是系统 python |

**结论：`install` 必须是"能定位解释器的一行"**，hermes 甚至要"先 glob 再装"——这已经不是"一条静态命令"能表达的了。

---

## 3. 方案：网关托管一个自举脚本（sh + ps1），端点只负责指路

### 3.1 为什么选脚本（回答"能不能执行 shell"）

能自举的 agent 一定能执行 shell（它本来就要跑 `pip`/`npm`）。脚本比静态命令多解决三件事：
1. **解释器定位**（hermes 的 tools python glob、macOS 的 `python3` vs `pyenv`）；
2. **装完自检**（同一解释器 verify，避免"装到别的 env 里还以为成功"——这正是我在宿主上踩到的）；
3. **可更新的逻辑**：新增宿主/换包管理器只改网关，不改邮件、不改 prompt。

代价是跨平台：Linux/macOS 用 POSIX `sh`，Windows 用 PowerShell。所以**两脚本同源生成**：

```
GET /api/v1/agent-setup/bootstrap.sh    → POSIX sh（linux/darwin）
GET /api/v1/agent-setup/bootstrap.ps1   → PowerShell（windows）
```
两者都由**同一张配方表**生成（`agent_setup.rs` 里现在那张），保证不漂移；输出统一为机器可读：
```
AIMAIL_BOOTSTRAP ok host=hermes version=0.4.0 python=/opt/hermes/tools/python-…/bin/python3
AIMAIL_BOOTSTRAP fail host=hermes reason=install-failed detail=…
```

### 3.2 提示词只留一行（保持你要的简短 + 泛化）

```
If `aimail_address` is unavailable, GET {gateway_url}/api/v1/agent-setup/install?host=<host>&os=<linux|darwin|windows>
and run the command it returns — or, if you prefer one command, download and run its `script_url`
(prints `AIMAIL_BOOTSTRAP ok|fail …`), then retry `aimail_address`.
```
- prompt 增量为 0（宿主清单、命令细节全在网关）；
- agent 有两条路：直接跑 `install`（可解释），或跑脚本（含自检，成功率高）。

### 3.3 宿主名歧义怎么消（回答第 2 问）

三步，从"硬"到"软"：
1. **端点不带 host** → 返回**探测表**：每宿主给 `markers`（环境变量 / 已知目录 / 可执行文件名），例如
   `hermes: env HERMES_HOME | path ~/.hermes | cmd hermes`。agent 只需检查自己看得到的东西；
2. **回填式探测**：`?probe=env:HERMES_HOME` 允许 agent 只报"我看到了哪个 marker"，由网关判定 `host`（agent 不必理解产品名）；
3. **兜底**：`host` 未知 → 404 里带完整清单（现在就实现了）。

不用"agent 自报名字"作为主路径——我刚实测到它会报自己的**机器名**（`llmlab`）。

### 3.4 离线/内网怎么装（L3 本地 loop 的现实约束）

容器无 DNS ⇒ 本地 loop 用**网关托管产物**：
```
GET /api/v1/agent-setup/sdk/agent_aimail-<ver>-py3-none-any.whl   (或 npm tarball)
```
脚本优先用 `AIMAIL_SDK_SOURCE` 环境变量 / `?source=` 指定的本地产物，其次才是注册表。CI（有网）走注册表。这样**同一套脚本**在本地与 CI 都能自举，且版本由网关/夹具指定，不随机漂移。

---

## 4. 五环境验证的顺序（方案确认后执行）

| # | 宿主 | 本地 loop（容器，离线） | CI（有网） |
|---|---|---|---|
| 1 | dsh | `SDK_KIND=none` + 网关托管 npm tarball | 注册表 |
| 2 | pi | 同上 | 注册表 |
| 3 | openclaw | 同上（npm，无需 python） | 注册表 |
| 4 | hermes | 网关托管 wheel + tools python | 注册表 |
| 5 | deerflow | 网关托管 wheel + 系统 python | 注册表 |

判据（每宿主同一套）：
- **A**：turn 日志里出现 `GET /api/v1/agent-setup/install`（或脚本 URL）；
- **B**：宿主**自己的发现面**能看到 SDK（`dsh plugin list` / `pi` settings / `openclaw plugins list` / `hermes plugins list` / deerflow extension registry），**不是**看某个 pip site-packages；
- **C**：重启后 `aimail_address` 可用；
- 记录：宿主版本、SDK 版本、解释器路径、turn 日志。

---

## 5. 需要 owner 拍的两点

1. **允许"下载脚本再执行"这条路吗？**
   - 方案 A（推荐）：端点返回 `install`（可解释的一行）+ `script_url`（可下载可摘读），prompt 让 agent 优选脚本；
   - 方案 B：只给 `install` 一行，脚本仅作内部工具、不暴露给 agent（更保守，但 hermes 的解释器 glob 要写进 prompt，变长）。
2. **Windows 的 L3 范围**：deerflow 在 Windows 上不现实（后端服务），是否 Windows 只覆盖 dsh/pi/openclaw 三个 node 宿主？（脚本的 `.ps1` 仍会提供，但只在这三个宿主上验。）

---

## 6. 四个决策的落地（2026-11-26 追加）

### 6.1 镜像的坑：已修 + 已升版本

| 项 | 处理 |
|---|---|
| **hermes venv 没有 pip**（`python3 -m pip` → "No module named pip"） | `Dockerfile.hermes` 增加 `RUN /opt/hermes/.venv/bin/python -m ensurepip --upgrade`；重建后实测 `pip 26.2.2` 就位，`pip install agent-aimail` 这条通用命令在 hermes 上成立 |
| openclaw 没有 python pip | **不修**：它是 node 宿主，SDK 走 npm（`openclaw plugins install agent-aimail`）。这条写进配方，避免再有人用 pip 试它 |
| 容器无 DNS（`getent hosts pypi.org` 空） | 本地 loop 从**网关托管的产物**装（wheel / npm tarball），CI 走注册表；脚本读 `AIMAIL_SDK_SOURCE` |
| **agent 版本**（pins.env） | dsh `0.2.2-rc.1 → 0.2.2-rc.5`、pi `1.5.2 → 1.6.1`、openclaw `2026.11.5 → 2026.11.10`、hermes `0.23.2 → 0.23.4`（基础镜像 digest 同步 repin，upstream `8fc03744`） |
| 重建工具 | 新增 `tests/agent-env/docker/rebuild-hosts.sh`（与 `host-images.yml` 同参数）；**离线兜底**：镜像仓库 HEAD 被墙时，用本地 tag 但**先校验本地 digest == pins 里的 digest**，不匹配就拒绝构建（不偷偷换底板） |

hermes 已重建并验证：`Hermes Agent v0.23.4` + `pip 26.2.2`。dsh/pi/openclaw 的 npm 重建在本机后台进行（镜像仓库代理间歇 403，已用上面的兜底路径）。

### 6.2 Windows / macOS / Linux 怎么区分（回答"HTTP 能不能辨识 OS"）

**HTTP 本身不能可靠辨识 OS。** 具体：
- 没有"OS"头；`User-Agent` 只反映 **HTTP 客户端库**（`python-requests/x`、`undici`、`node`），而同一个库在三个 OS 上完全相同；
- `Accept-Language`/`Sec-CH-UA-Platform` 只有浏览器会发，CLI agent 不发；
- 结论：**不能**用 HTTP 判定 OS，最多把它当"提示"（如 UA 里出现 `Windows` 极少见）。

**可行的判定顺序**（脚本/agent 侧，都是确定性信号）：
1. 脚本自身：跑的是 `sh` 还是 PowerShell —— **这本身就是 OS 判定**（Windows 上没有 POSIX sh；`cmd` 也跑不了 ps1）。所以"分两种脚本"天然消除了歧义，不需要 HTTP 参与；
2. agent 报 `os`：`uname -s`（Darwin/Linux）或 `$env:OS -eq 'Windows_NT'`，一次调用即得；
3. 端点把 `os` 当作**可选**参数：不传时返回 `{sh_url, ps1_url}` 两个入口 + 一句"按你的 shell 选"。

对应 prompt 增量只有一句：`... or run the script for your shell (its URL is in `scripts`)` —— 不写 OS 判断逻辑给 agent 猜。

### 6.3 openclaw 用 tssdk → npm（已并入配方）

| 宿主 | SDK 形态 | 安装 |
|---|---|---|
| dsh / pi / openclaw | **tssdk（npm）** | 宿主插件命令 / `npm i -g`，**与 pip 无关** |
| hermes / deerflow | pysdk（PyPI wheel） | `python -m pip install`（hermes 用其运行时 venv，现已补 pip） |

### 6.4 Windows 覆盖范围

除 **deerflow**（后端服务，Windows 不现实）外全覆盖：`dsh` / `pi` / `openclaw` / `hermes` 均上 Windows 矩阵；`hermes` 的 Windows 安装命令走 `py -3 -m pip`（`bootstrap.ps1` 里处理 py launcher 的解析）。

---

## 7. owner 追加拍板（2026-11-26）：只给一个 URL，OS 由脚本"试错"确定

> **本节被 §8 修正**：`?host=` 参数已取消（agent 不该知道宿主类别），脚本自己判定宿主与环境。

> owner：「很多工具的自举脚本都能自适应 OS，比如在脚本里先假定一个 OS 并执行特定命令，失败后主动跳转到另一套入口，这样通过失败的后处理来自适应 OS，只给一个 url 入口，避免让 agent 瞎猜。」

这**取代**了 §6.2 的"两个脚本入口 + agent 自己选"结论：端点只暴露**一个** URL，平台差异全部由脚本内的"先试 A、失败转 B"消化。

```
GET /api/v1/agent-setup/bootstrap?host=<host>            → POSIX sh（默认）
GET /api/v1/agent-setup/bootstrap?host=<host>&shell=ps1  → PowerShell 孪生体（同一个 URL 的变体，不是第二个入口）
```

`install` 端点返回的 `bootstrap`（一行 `curl -fsSL '…' | sh`）就是 agent 要执行的东西；prompt / 契约里不再出现"按你的 shell 选"。

### 7.1 脚本里的四级自适配（全部是"先假定、失败换下一个"）

| # | 步骤 | 假定 → 失败后转到 | 判定来自 |
|---|---|---|---|
| 1 | shell 方言 | 先按 POSIX 跑；`uname -s` 命中 `MINGW*/MSYS*/CYGWIN*/Windows*` ⇒ 把同一个 URL 加 `&shell=ps1` 交给 PowerShell 孪生体（`pwsh` → `powershell` → `cmd /c curl+powershell` 三级级联） | 运行环境本身，不是 HTTP |
| 2 | 宿主 | `?host=` → `$AIMAIL_HOST` → 环境标记（`HERMES_HOME` …） → PATH 上的宿主 CLI；都不中 ⇒ `fail unknown_host` 带清单 | 网关配方表的 `markers` 列 |
| 3 | 解释器 | `$AIMAIL_PYTHON` → 宿主自己的运行时（hermes venv / tools python、deerflow venv） → `python3` → `python`；每个都拿 `-c 'import sys'` 试跑 | 试跑成功才算数 |
| 4 | 安装器 | pip → 没有 pip 则 `ensurepip` → 再没有则 `uv pip --python`；node 宿主 `npm -g` → 全局前缀不可写（EACCES）则 `--prefix ~/.local` | 每一步的真实退出码 |
| 5 | 产物来源 | `$AIMAIL_SDK_URL`（本地 wheel/tgz，容器无 DNS 时必用；本地文件自动加 `--no-index`） → 否则公共注册表 | 文件是否存在 |

统一输出（机器可读，L3 判定直接解析）：

```
AIMAIL_BOOTSTRAP ok host=hermes kind=python version=0.4.0 tool=/opt/hermes/.venv/bin/python3
AIMAIL_BOOTSTRAP fail host=openclaw code=install_failed detail=…
```

### 7.2 实跑结果（本机 2026-11-26，`aimail-host-*:latest` + 网关 v1.0.20 未发布二进制）

| 场景 | 命令要点 | 结果 |
|---|---|---|
| hermes 常规 | `AIMAIL_HOST=hermes AIMAIL_SDK_URL=<wheel>` | ✅ `ok … version=0.4.0 tool=/opt/hermes/.venv/bin/python3` |
| 解释器级联 | `AIMAIL_PYTHON=/nope/python3`（故意坏） | ✅ 跳过坏解释器，落到 venv |
| pip 缺失 | `python -m venv --without-pip` + `AIMAIL_PYTHON=<该 venv>` | ✅ `has no pip; trying ensurepip` → 装成 |
| 不报宿主 | 无 `AIMAIL_HOST` | ✅ 自检为 `host=hermes` |
| 未知宿主 | `AIMAIL_HOST=llmlab`（agent 常报机器名） | ✅ `fail … code=unknown_host` + 五宿主清单，可一轮自纠 |
| openclaw（node, uid 1000） | `AIMAIL_SDK_URL=<npm tgz>` | ✅ 全局前缀 `EACCES` → 自动转 `--prefix /app/.local` → `ok … version=0.6.0 tool=/app/.local/lib/node_modules/agent-aimail`；随后 `openclaw plugins install`（无 DNS）失败**只告警不中断** |

**过程中发现并修掉的两个真缺陷**（都是"没实跑就发现不了"的）：

1. `HOST='${AIMAIL_HOST:-hermes}'` 用了单引号 ⇒ 变量永不展开，脚本第一句就以 `host=${AIMAIL_HOST:-hermes}` 报 `unknown_host`。已改为双引号，并在单测里断言"既烘焙进去、又仍可被环境变量覆盖"。
2. node 宿主一律 `npm install -g` ⇒ 在 uid 1000 的 openclaw 容器里 `EACCES: /usr/local/lib/node_modules` 直接失败。已加"全局前缀失败 ⇒ 私有前缀 + PATH"的级联（ps1 孪生体同步）。

单测：`agent_setup` 8 项（含 `sh -n` 语法校验、占位符零残留、每宿主 `markers`/解释器表齐全）。网关全量 **439 passed**。

### 7.3 仍未验的部分（诚实边界）

- **Windows 分支只做了静态校验**（`sh -n` 通过 + 分支逻辑审查），本机无 Windows，`.ps1` 未实跑；CI 的五宿主 × Windows 矩阵是它第一次真跑的地方。
- `openclaw plugins install` 这一步在无 DNS 的本地容器里必然失败（它要连 clawhub）；脚本把它降级为告警是正确的，但"注册成功"这条只会在 CI 有网时被证。

---

## 8. 修正（2026-11-26，owner 复核后）：一个**无参数** URL，宿主判定也进脚本

> owner：「没必要传 `host=` 这些参数，而是直接运行在 gw 上的 sdk 自举脚本，在自举脚本里先解决 OS 判别（通过试错方式），再解决宿主判定（即识别是哪个 agent），再找到宿主的环境目录（pip 别安装错环境），最后执行该宿主的 sdk 安装指令。这些都在这个自举脚本里解决。在激活 prompt 里是对 OS / 宿主类别 / 宿主环境 / 宿主的 sdk 安装指令都无感的，只知道在安装 sdk 而已。」

### 8.0 补正（owner 追问两条）：`curl … | sh` 不是通用的；解释性括注删掉

**问 1：`curl -fsSL '<url>' | sh` 在 Windows / macOS / Linux 都通用吗？——不通用，三处都会失手。**

| 环境 | 事实 | 后果 |
|---|---|---|
| **Windows 原生**（cmd / PowerShell） | 没有 `sh`（除非 Git Bash / WSL）；且 **Windows PowerShell 5.1 把 `curl` 别名到 `Invoke-WebRequest`**，`-fsSL` 根本不是它的参数 | 命令直接报错，必须走 `iwr -useb '<url>?shell=ps1' \| iex` |
| **Windows + Git Bash / WSL** | 有 `sh`，但它是 POSIX 模拟层 | 脚本自己 `uname` 命中 `MINGW*/MSYS*/CYGWIN*` 后转交 `.ps1`，这条路能用 |
| **macOS** | 自带 `curl` 与 `/bin/sh` | ✅ 通用；`python3` 不一定有（要 Xcode CLT） |
| **最小 Linux 镜像** | **deerflow 宿主镜像既没有 `curl` 也没有 `wget`**（实测）；hermes/openclaw 只有 `curl`，dsh/pi 两者都有 | `curl … \| sh` 在这一类镜像里跑不起来 |

所以**"一条 fetch 命令打天下"是假的**。处理：URL 只有一个，**取法按能力分档**，由 agent 按自己环境挑（payload 里三条并列）：
```
bootstrap         curl -fsSL '<url>' | sh
bootstrap_ps1     iwr -useb '<url>?shell=ps1' | iex
bootstrap_python  python3 -c "import urllib.request as u;open('/tmp/aimail-bootstrap.sh','wb').write(u.urlopen('<url>').read())" && sh /tmp/aimail-bootstrap.sh
```
（`exec(u.urlopen(...).read())` 是错的——那会把 shell 脚本当 **Python** 执行；必须落盘再 `sh`。第一版就写错了，实跑才发现。）

实测：deerflow 镜像（无 curl/wget）用 `bootstrap_python` 完整跑通 → `AIMAIL_BOOTSTRAP ok host=deerflow … tool=/app/backend/.venv/bin/python3`；hermes 镜像用 `bootstrap` 跑通。

**问 2：括号里那句"It works out the platform, the runtime and the host's own install command itself; its last line says ok or fail"有必要吗？——没有，已删。**

理由：agent 拿到的是**命令**，不是说明书；脚本自己会打印 `AIMAIL_BOOTSTRAP ok|fail`，解释只会稀释指令、还暗示"你可以不跑"。prompt 里只保留**意图 + 一个可执行命令 + 失败后的兜底动作**。

### 8.1 prompt 收敛到一句（对四者全无感）

```
If `aimail_address` is unavailable or unregistered, install the AIMail SDK with
`curl -fsSL '{gateway_url}/api/v1/agent-setup/bootstrap' | sh`
(POSIX; on Windows use `iwr -useb '{gateway_url}/api/v1/agent-setup/bootstrap?shell=ps1' | iex`),
then try `aimail_address` again for activation and binding. …
```

prompt 里不再出现 `host=`、不再出现宿主名、不再出现 `pip`/`npm`、不再出现解释器路径、**也不解释脚本怎么工作**。契约 §6 同步（v2.6）。

### 8.2 脚本内的判定链（四级，全部"先假定、失败换下一个"）

| 级 | 判什么 | 证据顺序（强 → 弱） |
|---|---|---|
| 1 | **OS / shell 方言** | 先按 POSIX 跑；`uname -s` ∈ `MINGW*/MSYS*/CYGWIN*/Windows*` ⇒ 把同一 URL 加 `&shell=ps1` 交给 PowerShell 孪生体（`pwsh` → `powershell` → `cmd /c curl` 级联） |
| 2 | **哪个宿主** | ①运行时环境变量（`HERMES_HOME`/`DSH_HOME`/`PI_HOME`/`OPENCLAW_HOME`/`DEERFLOW_HOME`）②**当前进程的祖先进程命令行**（`/proc/<pid>/cmdline` 上溯 5 层，即"我跑在谁里面"）③宿主 CLI 在 PATH 上 ④全局 npm 包里有没有该宿主的包 ⑤宿主专属路径是否存在（`/opt/hermes`、`/app/backend/langgraph.json` …）⑥某个解释器里能否 import 宿主模块 |
| 3 | **宿主的运行环境** | 每宿主自带解释器候选，**逐个试跑 `import sys`**：hermes=`/opt/hermes/.venv/bin/python3` → `/opt/hermes/tools/python-*/bin/python3`；deerflow=`/app/backend/.venv/bin/python{,3}`；node 宿主用 `npm root -g` 定位 |
| 4 | **安装器 / 产物** | pip → `ensurepip` → `uv pip --python`；npm `-g` → 全局前缀不可写则 `--prefix ~/.local`；`$AIMAIL_SDK_URL`（本地 wheel/tgz）优先于注册表，本地文件自动加 `--no-index` |

判定失败时输出**它看到了什么**，便于 agent 一轮自纠：

```
AIMAIL_BOOTSTRAP fail host=unknown code=unknown_host detail=cannot tell which host this is; saw: nothing; re-run with AIMAIL_HOST=<dsh pi openclaw hermes deerflow>
```

`AIMAIL_HOST` 仍保留为**人工覆盖**（调试用），但 prompt 不教它。

### 8.3 五宿主实跑（本机，**零参数**、无 `AIMAIL_HOST`、容器无 DNS）

| 镜像 | 命中证据 | 落点（宿主自己的环境） | 结果 |
|---|---|---|---|
| `aimail-host-hermes` | 路径 `/opt/hermes` | `/opt/hermes/.venv/bin/python3` | ✅ `ok … version=0.4.0` |
| `aimail-host-openclaw` | PATH `openclaw` | `/app/.local/lib/node_modules`（全局前缀 EACCES → 私有前缀） | ✅ `ok … version=0.6.0` |
| `aimail-host-pi` | PATH `pi` | `/usr/local/lib/node_modules` | ✅ `ok … version=0.6.0` |
| `aimail-host-dsh` | PATH `dsh` | `/usr/local/lib/node_modules` | ✅ `ok … version=0.6.0` |
| `aimail-host-deerflow` | 路径 `/app/backend/langgraph.json` | `/app/backend/.venv/bin/python3`（**该 venv 无 pip ⇒ ensurepip 顶上**） | ✅ `ok … version=0.4.0` |

deerflow 这一条最能说明"宿主环境"为什么必须由脚本解决：镜像里系统 `python3` 有 pip，但**真正跑宿主的是 `/app/backend/.venv`，且它没有 pip**；装到系统 python 里宿主永远看不见。

### 8.4 本轮修掉的判定缺陷

1. `A || B && C` 优先级坑：deerflow 双环境变量那条在 `A` 成立时**不会**赋值 `HOST`（已用 `{ …; }` 括起）。
2. 祖先进程匹配用裸子串会把 `pip install` 的 `pi` 命中成 pi 宿主 ⇒ 改为每个宿主显式 `proc_tokens`（如 `pi-coding-agent`、`/opt/hermes`），并对命令行两侧补空格。
3. 路径证据原先只 `-d`（目录），deerflow 的 `langgraph.json` 是文件 ⇒ 改为 `-e`。
4. `HOST='${AIMAIL_HOST:-x}'` 单引号导致变量永不展开（上一轮已修，单测钉住"可覆盖"）。

单测 8 项（含 `sh -n` 语法校验、占位符零残留、`bootstrap` 指令必须不含 `host=`）；网关全量 **439 passed**。

### 8.5 仍未验

- Windows `.ps1` 分支只有语法级校验（无 Windows 机器），CI 的五宿主 × Windows 矩阵是它第一次真跑；
- 无 DNS 容器里 `dsh plugin add` / `pi install npm:` / `openclaw plugins install` 必然连不上，脚本按"告警不中断"处理，**注册成功**只在有网 CI 上才会被证；
- 多宿主共存时按证据强度取第一个（`saw:` 会列出全部），未做"选错则重试下一个"。

---

## 9. 补正（owner 第三次复核）：不许让 agent 猜平台，给它一条**探测命令**

> owner：「这种表达是让 agent 自己去随机选择吗，agent 也不知道自己是 POSIX 还是 Windows。所以需要教给 agent 一个明确的探测 OS 的指令，然后根据探测结果（成功或失败）来选择执行哪个自举请求。」

对。`(POSIX; on Windows use …)` 对 agent 等于抛硬币——它既不知道自己的 shell 是哪种，也无从判断。改成**先探测、按结果分支**，探测本身就是一个可执行、结果可观测的命令：

```
probe:  sh -c 'echo sh-ok'
```

| 探测结果 | agent 该做什么 |
|---|---|
| 打印 `sh-ok`（有 POSIX shell） | `curl -fsSL '<url>' \| sh`；没有 curl → `wget -qO- '<url>' \| sh`；两者都没有（deerflow 就是） → 用宿主的 python3 取回文件再 `sh /tmp/aimail-bootstrap.sh` |
| 命令失败 / 报 “sh 不是可识别的命令”（无 POSIX shell） | `powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -useb '<url>?shell=ps1' \| iex"` |

要点：
1. **探测的是"我有没有 sh"，不是"我是什么 OS"**——这是 agent 唯一能确定性回答的问题；OS 名字（Windows/Linux/macOS）反而要它去推断。
2. 分支动作放同行，失败即换另一条，**没有"选择"这一步**；两个分支都不需要 agent 知道宿主类型。
3. Git Bash / WSL 的 Windows agent 探测会成功 → 走 POSIX 分支 → 脚本内部 `uname` 命中 `MINGW*/MSYS*/CYGWIN*` 后自己转交 `.ps1`。所以 Windows 上两条路都到得了同一个终点。
4. 探测命令已进 payload（`probe` 字段）和 prompt；单测钉住三件事：prompt 必须含探测串、必须含 `"iwr -useb` 可执行分支、**不得**含转义残留的反斜杠。

实测：hermes 容器 `sh -c 'echo sh-ok'` → `sh-ok`（POSIX 分支成立）；把 `sh` 挪出 PATH 后同一命令报 `sh: not found`（Windows 分支即此情形），说明判据是"命令能不能跑"，与平台名字无关。Windows 分支本身仍只有语法级校验（本机无 Windows）。

---

## 10. 定稿（owner 给表达，2026-11-26）：以 `uname -s` 能否执行为判据

采纳 owner 的表达，替换掉我上一版的 `sh -c 'echo sh-ok'` 探测（同一个思路，但更短、判据更硬）：

```
If `aimail_address` is unavailable or unregistered, install the AIMail SDK:
if `uname -s` can be executed in the current shell,
  run `curl -fsSL '{gateway_url}/api/v1/agent-setup/bootstrap?shell=sh' | sh`;
otherwise,
  run `powershell -NoProfile -ExecutionPolicy Bypass -Command
      "iwr -Uri '{gateway_url}/api/v1/agent-setup/bootstrap?shell=ps1' -UseBasicParsing | iex"`
```

为什么这版更好：
1. **判据即事实**：`uname` 是 POSIX 的一部分，能执行就说明当前 shell 是 POSIX；不能执行就只能是 Windows。不需要比较输出、不需要解析、不需要 agent 知道 OS 名字。
2. **两个分支各自点名要哪个孪生体**（`?shell=sh` / `?shell=ps1`），URL 对称，端点只需按 `shell` 返回对应脚本——不再依赖"默认给 sh"的隐含约定。
3. **PowerShell 侧用 `iwr -Uri … -UseBasicParsing`**：`-UseBasicParsing` 在 Windows PowerShell 5.1 上是必需的（5.1 无 Edge 引擎时 `iwr` 依赖 IE 解析），`-Uri` 也避免位置参数的歧义。`iwr -useb` 只在部分环境成立，换掉了。
4. 句子从 4 行缩到 2 行，仍然一个 URL、一个判断、两个动作。

实测：
- `GET /api/v1/agent-setup/bootstrap?shell=sh` → `content-type: text/x-shellscript`，首行 `#!/bin/sh`；
- `GET …?shell=ps1` → 首行 `# AIMail bootstrap (Windows) …`；
- hermes 容器按 payload 原样执行 `uname -s`（输出 `Linux`）+ `curl … ?shell=sh | sh` → `AIMAIL_BOOTSTRAP ok host=hermes … tool=/opt/hermes/.venv/bin/python3`；
- `uname` 不在 PATH 时该命令非零退出 ⇒ 正对应 Windows 分支的条件。

payload 仍保留 `bootstrap_wget` 与 `bootstrap_python`（deerflow 镜像既无 curl 也无 wget，实测需要）；prompt 正文不提它们，避免变长。单测新增：prompt 必须含 `` `uname -s` can be executed``、必须同时含 `?shell=sh` 与 `?shell=ps1`、Windows 分支必须含 `-UseBasicParsing`。

---

## 11. 镜像重建落地（2026-11-26 夜）：三镜像已是最新，openclaw 底座换新

### 11.1 做法：`setsid` 脱离会话（前两次失败的真正原因）

前两次重建"跑了一小时什么都没有输出"，根因不是 docker 也不是网络：**后台进程随工具 shell 一起被杀**（`nohup … &` 仍在同一进程组）。这次改用

```
setsid nohup bash tests/agent-env/docker/rebuild-hosts.sh <hosts> > /tmp/log 2>&1 < /dev/null &
```

并用"构建客户端年龄 + 日志行数"两路轮询（`ps -eo etime,cmd | grep -F 'docker build --network host'`），每 60–150 秒看一次；dsh 全程 ~7 分钟，中间可看到 npm 的 `EBADENGINE`/`deprecated` 行，未再出现静默停滞。

### 11.2 结果

| 镜像 | agent 版本 | node | 内置校验门 | LLM 配置 | SDK |
|---|---|---|---|---|---|
| `aimail-host-dsh` (`cd94c3fc5e7c`) | **0.2.2-rc.5** ✓ | v22.23.2 | 15 passed / 0 failed | `cordis.patch.yml`：xiaomi provider、`apiKeyEnv: LLM_API_KEY`、`baseURL …/v1`、`mimo-v2.6-flash` | absent ✓ |
| `aimail-host-pi` (`3120a0d986d3`) | **1.6.1** ✓ | v22.23.2 | 14 passed / 0 failed | `~/.pi/agent/models.json`：`baseUrl …/v1`、`api: openai-completions`、`apiKey: __LLM_API_KEY__`、`mimo-v2.6-flash` | absent ✓ |
| `aimail-host-openclaw` (`21ceb122e358`) | **2026.11.10** ✓ | v24.22.1 | 12 passed / 0 failed | `/app/.openclaw/openclaw.json`：`baseUrl …/v1`、`model custom-api-xiaomimimo-com/mimo-v2.6-flash` | absent ✓ |

三镜像零参数自举复测（新版脚本、本地产物、无 DNS）：
```
AIMAIL_BOOTSTRAP ok host=dsh      kind=node version=0.6.0 tool=/usr/local/lib/node_modules/agent-aimail
AIMAIL_BOOTSTRAP ok host=pi       kind=node version=0.6.0 tool=/usr/local/lib/node_modules/agent-aimail
AIMAIL_BOOTSTRAP ok host=openclaw kind=node version=0.6.0 tool=/app/.local/lib/node_modules/agent-aimail
```

### 11.3 openclaw 先前失败的真因：底座不是最新，不是 pins 写错

第一次重建 openclaw 报 **`version drift: installed='2026.11.5' pinned=2026.11.10`**。查下来 pins 的 digest（`9c74476e…`）与本地底座**完全一致**——也就是说 pins 没错，是**那个 snapshot 本身只带 2026.11.5**。`docker pull ghcr.io/openclaw/openclaw:latest` 拿到新 digest `230afbdd…`，其内 `openclaw --version` = **2026.11.10**，于是：

- `tests/agent-env/pins.env`：`BASE_OPENCLAW` digest `9c74476e…` → `230afbdd…`（已两处校验：pull 后 `RepoDigests` 与 pins 一致）
- 重建通过：`✓ pinned version installed: openclaw 2026.11.10`，12 项校验全绿

教训：**"pins 里的 digest"不等于"上游最新"**，pins 只保证"可复现"；要判"是否最新"必须真的拉一次 `:latest` 比对里面 agent 的版本（`verify.sh` 的 pinned-version 门正是干这个的，所以它救了这次）。

### 11.4 记录在案、但本轮未改：node 22 vs dsh 要求的 node ≥24.9

`npm view @deepseek-ai/dsh@0.2.2-rc.5 engines` → `{ node: '>=24.9.0' }`（rc.1 也是同一条），而 `BASE_NODE` 是 `python3.12-nodejs22`（node v22.23.2）。实跑后果：`npm install -g` 打 `EBADENGINE` 警告但装成功，`dsh --version` 与内置校验门（15 项）全过，L2 也一直跑在这个组合上。要彻底对齐需把 `BASE_NODE` 换成 `python3.12-nodejs24`：**本机拉不下来**（`registry-1.docker.io` 连接被 reset；`dockerproxy.net` 代理拉 1.5GB 超时），CI runner 有 Docker Hub 可以一条命令换。留作已知项，不在这台机器上硬改。

---

## 12. prompt 驱动自举：五宿主实跑结果（2026-11-27）

判据（owner 2026-11-27：**路线不限，装上即成功**）：`B = SDK 落进宿主自己的运行环境`；`A` 只用来记录路线。
`A` 不得用"日志里出现自举 URL"——prompt 本身包含那条 URL，宿主回显 prompt 就会假绿（实测踩过，已修）。

| 宿主 | 结果 | 路线（A） | SDK 落点 | 证据 |
|---|---|---|---|---|
| **pi** | ✅ PASS | 网关自举脚本（agent 自己 `uname -s` → `curl '<url>?shell=sh' \| sh`） | `/usr/local/lib/node_modules/agent-aimail` 0.6.0 | `/tmp/boot-loop-pi.8LrhjX` |
| **openclaw** | ✅ PASS | 网关自举脚本 | `/app/.local/lib/node_modules/agent-aimail` 0.6.0 | `/tmp/boot-loop-openclaw.xOVs5u` |
| **dsh** | ✅ PASS | 宿主自带机制（`dsh plugin add`） | `/usr/local/lib/node_modules/agent-aimail` 0.6.0 | `/tmp/boot-loop-dsh.CDhWct` |
| **deerflow** | ❌ 结构上不可能 | — | — | `/tmp/boot-loop-deerflow.dajJCv` |
| **hermes** | ❌ 未打通 | — | — | 见 §12.2 |

### 12.1 deerflow：宿主设计上不给 agent 执行能力

deerflow 的编排 agent 的工具集是**固定的四项**：`present_files`、`ask_clarification`、`review_skill_package`、`list_uploaded_files`
（模型自己在回合里念出来的清单），**没有 shell、也没有可委派的执行子代理**（早期一次运行出现过 `do_task`，此配置下不再出现）。
逐层排除过的"可能修法"：

| 试过的 | 结果 |
|---|---|
| `sandbox.allow_host_bash: false → true`（镜像是 false） | 只是 sandbox 的开关，**不改变编排 agent 的工具清单** |
| prompt 补一句"若本会话没有 shell，就交给你能调用的执行类工具/子代理" | 模型据此明确回答"no shell execution capability"，仍无执行路径 |
| `python3 -m uvicorn app.gateway.app:app` + `deerflow_drive.py` 驱动（与 L2 夹具同法） | 回合正常跑、assistant 消息正常回，但只能回到"请重启宿主"这一结论 |

结论：**deerflow 这类"锁定宿主"无法靠 prompt 自举**——产品口径应写成"该宿主需预装 SDK"，或由 deerflow 侧开放执行工具（安全姿态变更，需 owner 决定）。

### 12.2 hermes：五道门，逐层剥到第五道

hermes 0.23.4 的 CLI 在容器里拒绝跑回合，按遇到的顺序：

1. `prepare.sh` 写的是 `model.provider: "custom"` —— hermes **不认识** `custom`；它认 `openai-api`（`hermes auth add openai-api …` 成功）。
2. `/opt/data/.hermes` 只有 `config.yaml`、没有 `profiles/` —— 补 `profiles/verifier/config.yaml` 后 `profile_mismatch` **消失**（这一步是真修复）。
3. 只有 `api_key_env` 不够 —— hermes 要求"已连接的 provider"；在 `$HERMES_HOME/.env` 写入 `LLM_API_KEY`/`OPENAI_API_KEY` 后，`not connected to any AI provider` **消失**。
4. 镜像缺可用的 `pnpm`（hermes 自检 `install out of sync (pnpm…)`；镜像的 npm 拦 install scripts，`--allow-scripts=pnpm` 也没落地，应改走 corepack）。
5. **最后一道**：`Error: Gateway inaccessible: service_manager_unavailable` —— CLI 需要一个在 s6 监管下运行的 hermes gateway（`HERMES_GATEWAY_NO_SUPERVISE=1` 与 `--no-supervise` 都没绕过）。

前 4 道都已定位并有明确改法（provider 写 `openai-api`、构建期补 `profiles/<name>/`、运行期把 `LLM_API_KEY` 注入 `$HERMES_HOME/.env`、用 corepack 装 pnpm）；第 5 道是"CLI 必须在 s6 监管的 gateway 里跑"，需要把 L3 的驱动方式从 `docker exec hermes -z` 换成"经 gateway 的会话"（L2 夹具 phase 2 就是这么做的：`nohup hermes --yolo gateway run &` 之后走 webhook 入站）。

---

## 13. owner 决策（2026-11-27）+ hermes 镜像体检

### 13.1 决策

1. **deerflow 放弃 SDK 自举**（宿主无执行能力，见 §12.1）⇒ 该宿主在本议题下为 **N/A**，`boot-loop.sh --host deerflow` 现以退出码 2 明确标注 N/A，不再产出一条看起来"失败"的红。
2. **hermes 走 A：经其 gateway 的会话**驱动（不走 `docker exec hermes -z`）。

### 13.2 hermes 镜像是不是官方最新？——是

| 检查 | 结果 |
|---|---|
| `pins.env` 的 `BASE_HERMES` digest | `sha256:ef0650d5…` |
| 本地 base 镜像 `RepoDigests` | `sha256:ef0650d5…`（与 pins **逐字一致**） |
| 容器内版本 | `Hermes Agent v0.23.4 (2026.9.24) · upstream 8fc03744`，`Install method: docker` |
| 上游最新 release（GitHub `releases/latest`） | **`v0.23.4`**（published 2026-11-24） |

结论：我们跑的就是官方最新镜像（v0.23.4），pins 也没漂。

### 13.3 但镜像自身有一处上游缺口：pnpm

- 每次调用 `hermes` 都会先打印 `⚠ install out of sync (pnpm: not installed or outdated) — this docker-managed install must rebuild the artifact to fix`；
- 这不是我们构建引入的：**官方 base 镜像里就有**（`nousresearch/hermes-agent:latest` 直接跑也一样），L2 的 hermes 证据从 2026-11-23 起就带着这条告警；
- 试过 `hermes pm install`（构建期、有网）→ 装了 cua-driver 等，**pnpm 仍然缺失**；`npm i -g pnpm` 被镜像的 `allowScripts` 拦；容器内无 DNS 更装不了。
⇒ 这是官方 docker 镜像的自洽性缺口（它用 pnpm 管依赖，却不带 pnpm）。

### 13.4 这个缺口影响其它用例吗？——会，且不是本次重建造成的

| 观察 | 证据 |
|---|---|
| 纯 CLI 回合挂死：`hermes --yolo -z 'reply pong'` 只输出 pnpm 告警，然后到超时（**RC=124**） | 本机实测（`/opt/l2home` 接线，90s 超时） |
| **新旧镜像一样挂**：v0.23.2（11-18 构建的 `:native`）与 v0.23.4（今天重建）表现相同 | 同上对比跑 |
| 今天最近一次 L2 hermes 是红的，且日志里同样有那条告警 | `evidence/l2/20261126T101944Z-hermes-x/results.txt`：`RED S1 activation no binding written` |
| 但 11-23 同样的告警下 L2 曾 PASS 过（binding written） | `evidence/l2/20261123T134902Z-hermes-x/results.txt` |

⇒ 结论分两层：**告警本身不致命**（11-23 带着它也能过），但**现在这个镜像里 CLI 回合会挂到超时**，所以凡是通过 `hermes -z` 驱动 hermes 的用例（L2 的 hermes 单元、C17 的 hermes 观测）都受影响，必须在 hermes 上改用 A 方案（gateway 会话）后才谈得上绿。这也意味着 hermes 的 L2 结果目前不可信，不能拿 11-23 的绿灯当现状。

---

## 14. 版本口径更正（owner 追问）：`(2026.9.24)` 不是构建日期；我们跑的是 latest，不是 newest

### 14.1 三件不同的事，之前被我混为一谈

| 概念 | 事实（2026-11-27 实测） |
|---|---|
| 版本串里的 `(2026.9.24)` | 上游的**发布列车代号**（tag 列表里有 `v2026.9.24`、`v2026.9.21` …），**不是**构建日期也不是发布日期 |
| **稳定通道** `latest` | digest `sha256:ef0650d5…`，Hub 上 `last_updated=2026-11-24T08:17Z`；对应 release **v0.23.4**（2026-11-24 发布） |
| **预发布通道** `canary` | digest `sha256:a4bae507…`（2026-11-25），另有当日构建 `canary.20261126T064809Z-v0.23.4`（2026-11-26）；容器内 `v0.23.4+canary.20261125T064800Z · upstream 24353849` |

我们的 `BASE_HERMES` digest == 本地底座 digest == Hub 上 `latest` 的 digest，**逐字一致**。
⇒ 我们跑的是**官方最新稳定镜像**，但**不是"最新发布"**：最新发布是 canary（每天构建）。之前我说"官方最新"少了这个限定，这里更正。

### 14.2 canary 与 stable 的实测差异

| | stable `latest` (v0.23.4) | canary (20261125) |
|---|---|---|
| `hermes --version` | 打印 `install out of sync (pnpm…)` | **不再打印** |
| CLI 回合 | 挂到超时（RC=124），无输出 | 先报可操作的 provider 提示；配好 provider 后**同样无输出**（仍待解） |
| 上游 commit | `8fc03744` | `24353849` |

### 14.3 pnpm 缺口的正解（构建期可解，无需 canary）

`hermes pm install pnpm`（构建期、有网）会把 pnpm 装进 **hermes 自己的 store**：
```
✓ pnpm
InstalledPackage(path=/opt/data/tools/pnpm-12.15.0-linux-x64, version=12.15.0,
                 binary=/opt/data/tools/pnpm-12.15.0-linux-x64/pnpm)
```
之后 `pm` 自检满足，**`install out of sync (pnpm…)` 告警消失**（实测）。
对照：`npm i -g pnpm` / 独立二进制丢到 `/usr/local/bin` / `hermes pm install`（不带包名）**都不算数**——hermes 只认自己 receipt 里的 pnpm。

因此镜像修法（尚未落地）：`Dockerfile.hermes` 增加 `RUN /opt/hermes/bin/hermes pm install pnpm`。
稳定 vs canary 的选择：**建议留在稳定通道 + 补 pnpm**（canary 是预发布，不适合当回归底板），若要跟 canary 则需同时改 pins 与 CI。

---

## 15. 追问「hermes 是 Python agent，为什么要装 pnpm？」——不装，它是噪声

- **pnpm 与我们的 SDK 无关**：pypi SDK 走 `pip install agent-aimail`（wheel）。pnpm 是 **hermes 自己**的依赖——官方安装是 "docker-managed"，`pm.activate()` 管的是它的 **JS 工作区（root / web / ui-tui）与 pm 工具包**；镜像不带 pnpm，于是每次启动自检打印一句。
- **对照实验证明它不是阻塞点**：`hermes pm install pnpm` 装上后告警消失，但回合表现与不装时完全相同（canary 甚至本来就不打印告警）。⇒ 不为了它改镜像（owner 也确认留稳定通道，`BASE_HERMES` digest `ef0650d5…` 不动）。

### 15.1 我自己踩的三个坑（比 pnpm 重要得多，全部已修）

| # | 坑 | 现象 | 修法 |
|---|---|---|---|
| 1 | 临时探测容器用的是**默认 bridge（无 DNS）** | 模型域名解析不了 ⇒ 回合静默挂到超时，看起来像"hermes 挂死" | 一律 `--network host`（boot-loop 本来就用，我临时探测漏了） |
| 2 | `/work` 是 `mktemp -d`（**0700**），而 hermes 以 **uid 10000** 运行 | 读不到 prompt ⇒ `-z` 收到空串 ⇒ **静默掉进交互 TUI** | boot-loop 在写好 prompt 后 `chmod -R a+rX "$WORK"` |
| 3 | 把 prompt 拼进 `su -c "… -z \"$(cat …)\""` 的字符串里 | prompt 含反引号/引号，被二次解析 ⇒ 参数丢失 | 用**引号 heredoc 生成 runner 脚本**，prompt 只以文件路径出现在脚本里，运行时 `"$(cat file)"` |

### 15.2 hermes 回合的正确配方（实测：输出 `pong`）

provider 在 hermes 0.23.4 里是**三件套**，缺一不可：

| 项 | 缺了会怎样 |
|---|---|
| `config model.provider = openai-api` | `ProviderNotConfiguredError: No LLM provider configured` |
| `config model.base_url = <我们的端点>` | 内置 openai-api 去连 `api.openai.com` ⇒ `HTTP 403: Country, region, or territory not supported` |
| `$HERMES_HOME/.env` 里 `OPENAI_API_KEY` | 命令行上有这个环境变量**也不算**：`No usable credentials found for provider 'openai-api'` |

而且**生效的配置文件是 `/opt/data/config.yaml`**，`prepare.sh` 写的 `/opt/data/.hermes/config.yaml` 的 model 段 hermes 不读（这个错位让前面几轮修改全部无效）。

### 15.3 仍卡住的一步（诚实边界）

用**短提示词**（"reply with the single word pong"）回合正常返回 `pong`；换成我们的 **795 字节激活 prompt** 后，回合在 240s / 420s / 900s 内都没有任何输出（连 `install out of sync` 之外的行都没有），既没有自举痕迹也没有安装。⇒ hermes 的 CLI 单发回合能吃短 prompt、吃不下这条长 prompt，原因未定位。这正是 owner 选 A（经 gateway 会话）要解决的部分：把 prompt 作为**会话消息**投递，而不是作为 CLI 参数。

---

## 16. 结论：4 个可自举宿主全部通过（2026-11-27）

owner 决策：deerflow 不做自举；hermes 走 A（网关会话）。按此完成：

| 宿主 | 结果 | 路线 | SDK 落点 |
|---|---|---|---|
| **pi** | ✅ PASS | 网关自举脚本 | `/usr/local/lib/node_modules/agent-aimail` 0.6.0 |
| **openclaw** | ✅ PASS | 网关自举脚本 | `/app/.local/lib/node_modules/agent-aimail` 0.6.0 |
| **dsh** | ✅ PASS | 宿主自带 `dsh plugin add` | `/usr/local/lib/node_modules/agent-aimail` 0.6.0 |
| **hermes** | ✅ PASS | 网关自举脚本（经 gateway 会话投递 prompt） | `/opt/hermes/.venv/bin/python3` 0.4.0 |
| deerflow | **N/A**（owner 决定） | — | — |

hermes 的最后一环是**权限**：venv 属 root，宿主以 uid 10000 运行 ⇒ `pip install` 写
`/opt/hermes/.venv/lib/python3.14/site-packages/aimail` 被拒。修法两条（都已落地并重建验证）：
- `Dockerfile.hermes`: `RUN chown -R hermes:hermes /opt/hermes/.venv`（宿主的运行环境必须归宿主所有，否则自举无从谈起）
- 自举脚本 `record()`: 先 `mkdir -p` 痕迹目录（`~/.aimail` 不存在时痕迹会静默丢失，而它是 A 判据的唯一证据）

### 16.1 A 方案的实现（hermes 专用驱动）

`tests/l3/lib/hermes-gateway-turn.{sh,py}`：
1. `hermes gateway attach --json --start` → `{url, ticket, protocols}`；
2. 用 hermes 自己的 `GatewayClient` 连 `/api/ws`，JSON-RPC：`runtime.describe` → `session.create` → `prompt.submit`；
3. provider 三件套（`model.provider=openai-api`、`model.base_url`、`model.default`）+ `/opt/data/.env` 里的 `OPENAI_API_KEY`；
4. 事件流里可见 `tool.start/tool.complete`（`uname -s` → `Linux`、`curl …?shell=sh | sh`）与 `AIMAIL_BOOTSTRAP ok`。

顺带证明了 agent 的自纠能力：首轮 curl 失败后它自己把脚本下载到 `/tmp` 读源码、又去 `search_files` 掏 hermes 的 entry_points 机制。

---

## 17. 一次跑完的矩阵（`tests/l3/bootstrap-matrix.sh`）+ `.ps1` 真机解析

### 17.1 矩阵脚本

`tests/l3/bootstrap-matrix.sh`：自己起一个私有网关（临时端口 + 临时库），依次跑
`boot-loop.sh --host <h>`，最后打印一张矩阵并把证据落在 `evidence/bootstrap-matrix/<UTC>/<host>/`。
deerflow 按 owner 决定**跳过**（不是失败）。

本次实跑（2026-11-27T05:17Z，LLM 真实回合）：

```
──────── matrix (20261127T051751Z)
  PASS  pi        (路线：网关自举脚本, agent-aimail 0.6.0 -> /usr/local/lib/node_modules)
  PASS  openclaw  (路线：网关自举脚本, agent-aimail 0.6.0 -> /app/.local/lib/node_modules)
  PASS  dsh       (路线：网关自举脚本, agent-aimail 0.6.0 -> /usr/local/lib/node_modules)
  PASS  hermes    (路线：网关自举脚本, agent-aimail 0.4.0 -> /opt/hermes/.venv)
evidence: evidence/bootstrap-matrix/20261127T051751Z
```

值得记住的一点：**dsh 这次也走了网关自举脚本**（上次它是用宿主自带的 `dsh plugin add`）——
说明 prompt 把两条路都打开了，agent 自己挑；判据只看"SDK 有没有落进宿主环境"，所以两种都算过。

### 17.2 `.ps1` 不再是"只看一眼"

本机没有 `pwsh`，但可以拉官方镜像做**真实 PowerShell 运行时**校验：

| 检查 | 命令要点 | 结果 |
|---|---|---|
| 语法/AST 解析 | `[Parser]::ParseFile` | **PARSE OK（1016 tokens，0 错误）** |
| 未知宿主 | `AIMAIL_HOST=llmlab pwsh -File b.ps1` | `AIMAIL_BOOTSTRAP fail … code=unknown_host` + 五宿主清单 |
| 已知宿主但无解释器 | `AIMAIL_HOST=hermes pwsh -File b.ps1`（alpine 无 python） | `AIMAIL_BOOTSTRAP fail … code=no_python`，**干净失败不挂死** |

仍未验的只剩"Windows 上真的装成"——那需要 Windows 执行器（CI 矩阵）。

---

## 18. L2 全量复跑（2026-11-27）：**不全绿，按约定不发版**

命令：`run-matrix.sh --host all --sdk ts --stages "s1 s2 s3 s4 s5 s6 s7 s8 neg"`，`GW_BIN=` 指向未发版的本地网关（含锚点强制 v1.0.20）。

```
L2/L3 matrix: PASS=50 RED=3 GAP=8  → VERDICT: FAIL
  dsh       21 PASS / 1 GAP / 0 RED / 6 N/A   （GAP: S5 模型没调工具）
  pi        全 PASS（S7/S8 按契约 N/A）        （GAP: S5）
  openclaw  8 PASS / 3 GAP / 3 RED             （RED: S2 arming ×2、S4 C1）
```

### 18.1 已定位并修好的一个假 RED（fixture）

`S4 multi-address — second binding written on openclaw (max=2) — C1 violated` 是**误判**：
宿主适配器变量在宿主之间泄漏——`hosts/*.sh` 是被 `source` 进同一个 shell 的，pi 的
`HOST_ADDRESS_MODEL=single` 被 openclaw 继承，于是 openclaw 走了"单地址必须拒绝"的分支。
实测 openclaw 同时存在两个 binding（`b1=1, b2=2`，布局 `/app/.aimail/systems/*/*/agentmail.json`）⇒ 它是 **multi**。

两处修复：`hosts/openclaw.sh` 显式 `HOST_ADDRESS_MODEL=multi`；`run-matrix.sh` 每宿主前
`unset HOST_ADDRESS_MODEL HOST_CAN_ALLOCATE_SESSION HOST_DELIVERY_CONFIRMATION`。
复跑 openclaw：`PASS S4 multi-address two bindings coexist (max=2)` —— 假 RED 消除。

### 18.2 仍然不是绿的部分

| 类别 | 项 | 现状 |
|---|---|---|
| **RED** | openclaw `S2 arming` ×2 | 绑好之后 `openclaw gateway` 阶段没有出现任何 aimail/aimail-pull 行；下游 `S3 delivery`、`S4 reconcile` 均因此连带。夹具原先不保留 `gw2.log`，**无法区分"产品没 arm"还是"网关没起来"**——已补 `cp /tmp/gw2.log /out/`，下一轮即可定性 |
| **GAP** | dsh `S3 delivery` | journey 没走到邮件阶段（与 openclaw 同源：投递侧没起来） |
| **GAP** | dsh `S6` / openclaw `S6` | 会话证据读不到（`sessions1=1, key=-`）：夹具的会话根路径/解析与宿主不符 |
| **GAP** | dsh `S7` / `S8` | 驱动脚本存在且会写 `s7_gone.json`/`s8_anchor.json`，但 dsh 的 journey 只在 `S7=1/S8=1` 时跑，且 case 侧没有对应 hook 等它写完、容器先被 `host_stop` 删掉 |
| **GAP** | pi / openclaw `S5` | 模型整轮没调 `aimail_address`（pause/unbind），属模型行为/夹具提示，不是产品缺陷 |

**结论：L2 未全绿 ⇒ 不发版。** 下一步顺序：① 用新取证把 openclaw S2 定性（产品 or 夹具）→ 修 → ② dsh/openclaw 的 S6 会话根路径 → ③ S7/S8 hook 接线（等容器写完再停）→ ④ S5 提示词或重试策略 → ⑤ 再跑全量。

### 18.3 openclaw 的两条 RED 是夹具取证位置错了（已修）

`S2 arming` / `S3 delivery` 的判据是"host.log 里有没有 arm 行"，而 openclaw 的 arm 行由
**嵌入式回合**打印（`openclaw agent --local`），例如：

```
[aimail-pull] system l2opencl…   ← 在 /tmp/turn.log / rc 里，而不是网关日志
```

夹具只在 `/tmp/gw.log`、`/tmp/gw2.log` 里 grep ⇒ **最后一轮恰好带这行就绿，否则红**——这正是
同一镜像、同一代码下 `PASS S1+S2` 与 `RED S2` 交替出现的原因。

修法（`hosts/openclaw.sh`）：
- `_turn()` 把每轮输出追加到 `/tmp/turns.log`（持久）；
- `host.log` 改为从 `/tmp/turns.log` + 网关日志一起 grep，并把 `turns.log` 复制进证据；
- 网关启动由固定 `sleep 25` 改为**等 arm 行**（该镜像启动实测 ~96s）。

复跑 openclaw（`s1 s2 s3`）：**PASS=15 RED=0 GAP=0 → VERDICT: PASS**（S1 全项、S2 两行、S3 三行）。

### 18.4 基线 #2/#3 无效：LLM 账户余额耗尽（402）

基线 #2（全量）`PASS=23 RED=10 GAP=10`、基线 #3（pi+openclaw）`PASS=0 RED=10 GAP=4`，看着像是
产品大面积回归。实际根因在宿主的回合日志里：

```
402: {"code":"402","message":"Insufficient account balance","type":"insufficient_balance"}
```

直连复核（同一 key、同一端点）：`http=402 Insufficient account balance`。
⇒ 所有 agent 回合都无法调模型 ⇒ 模型不会调 `aimail_address` ⇒ S1 无 binding ⇒ S2/S3/S4 全部连带 RED。
**这两次基线不可用于任何判断，必须换 key/充值后重跑。**

同时，唯一有效的一次基线（#1，余额尚存时）里那 3 条 RED 已被证明并修好（openclaw 的取证位置），
修完实跑 `openclaw s1 s2 s3 → PASS=15 RED=0 GAP=0`。

### 18.5 已加但未能验证的夹具健壮性

`pi`、`openclaw` 的 bind 回合各加**一次有界重试**（模型没 dispatch 时再要求一次，并在日志里留
`RETRY:` 标记）。理由：S5 的 GAP "模型整轮 0 次工具调用" 与 #1 里偶发的 S1 无 binding 是同一种
模型行为抖动，真实用户也会重问一次。因余额耗尽，这次改动**尚未跑出绿**，等 key 恢复后需要验证。

---

## 19. L2 修复进展（2026-11-27 晚，余额恢复后）

### 19.1 又一枚系统性假红：`s5` 与投递类 stage 不能同批跑

`run-matrix` 只要 stage 里含 `s5` 就置 `LIFECYCLE=1`，而适配器在该分支**跳过白名单、S3 注入与网关启动**。
于是"同一批里既有 s5 又有 s3/s6/s7/s8"时，投递类义务必然红（表现：S3 `journey never reached the mail stage`、
S6 `acked=0`、S7/S8 `no evidence file`）。已加护栏：这种组合**直接 exit 2** 并打印两趟的正确命令。

### 19.2 驱动发信人没进白名单（`550 Sender not whitelisted`）

s6/s7/s8 驱动用 `s6-a@/s6-b@/s6-reply@/s7-*/s8-*` 发信，夹具只白名单了 `ext-sender@`，
所以三封驱动邮件全被拒 ⇒ `acked=[false,false,false]`、`s7_gone.json`/`s8_anchor.json` 根本没生成。
五个适配器改为**逐个 sender 循环调用**（一次调用里重复 `--sender` 无效：argparse 只保留最后一个）。

### 19.3 修完后的 dsh 结果（同一批 `s1 s2 s3 s4 s6 s7 s8 neg`）

```
PASS  S1 全项 · S2 两项 · S3 三项(sender whitelisted / emails=1 / queued then acked)
PASS  S4 两项(two bindings coexist / aggregate stream reconciled)
PASS  S7「delivery used a new session」「downgrade recorded as session-gone」
PASS  S8「anchor-moved recorded」「marker names the edge」「delivery to current anchor」
RED   S6「host did not accept every mail (acked=0)」      ← 驱动自己的 ack 判据
RED   S7「reply was NOT accepted after the downgrade」    ← 同上
RED   S7「thread root changed on re-attach; E2 lineage lost」← **真产品信号，待 dev 判**
RED   S8「reply was NOT accepted」
```

关键：`s6.json` 里 `sessions1=3`、`session1=345db2b2-…`（会话键已能读出 ⇒ 会话根路径回退是正确的一步），
但 `acked=[false,false,false]`。⇒ 宿主其实**已经把这批邮件拉进会话**，红的是驱动对"是否被接受"的判据
（它查 pending 行的方式与实际 ack 不同源）。下一轮先修判据，再判 `thread root changed` 是否真产品缺陷。

### 19.4 当前仍未绿

- dsh：S6 ack 判据、S7 lineage、S8 ack（见上）
- s5 需要单独一趟（`--stages "s5"`）
- pi / openclaw 这一趟还在跑

---

## 20. 投递类一趟的结果（2026-11-27 深夜）

### 20.1 dsh：**全绿**

`--host dsh --stages "s1 s2 s3 s6 s7 s8 neg"` → `PASS=32 RED=0 GAP=0 VERDICT: PASS`
（S6 三封全 ack / 回复复用同一会话 / 新 thread 新会话 / 会话数不增长；S7 重挂新会话且血缘保留；
S8 anchor-moved 记录且投到当前锚点会话）。关键修复：驱动的"接受"判据改为**持久信号**
（`emails` 行 `status=delivered` + 该地址 pending 归零），因为 SSE 拉取会在一个轮询间隔内完成 ack。

### 20.2 pi：该趟同样干净

`pi` 段 `PASS=21`，无 RED/GAP（S7/S8 按契约 N/A）。

### 20.3 openclaw：卡在 `410 CODE_GONE`，但**不是产品拒绝**

回合回复是 `HTTP 410 CODE_GONE（invalid or expired activation code）`。查网关库：
两条 openclaw 码 `claimed=0`、`claimed_at=NULL`、`expires_at` 在未来 ⇒ **网关从未收到过这个码**。
⇒ 模型在 `dispatch_action` 里把长码（`addr-f6f0-e77d-92e6-5dc7-9a5b-39f8`）抄错了，不是宿主拒绝。
（同一批的 pi/dsh 用同样提示词都能绑上，所以是 openclaw 元工具的参数转写问题。）

下一步：夹具在收到 410 时**重新签发一个新码并重试**（该地址可以多次签码），并在提示里要求模型
先逐字回显再 dispatch —— 目标是把"模型抄写长串"的不确定性从判据里去掉。

### 20.4 仍未做

- `--stages "s5"` 单独一趟（pause/unbind）
- openclaw 的 410 重试
- 三宿主两趟全绿 → 才发版
