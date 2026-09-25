"""交付脚本调用的 CLI 命令面必须存在(消费者侧覆盖)。

事故背景(2026-09-25 实测): 2026-09-23 B 裁决把独立子命令 `payload` / `ensure-system`
并入 `aimail install --payload …` / `aimail install --system-only`,但
`pysdk/deer-flow/install-mcp.sh` / `install-skill.sh` 仍调 `aimail payload …` —— 因
platforms.json 的 deerflow spawn 步是 `on_error=warn`,坏掉只降级成**一行警告**:
deer-flow 装完既没有 `mcpServers.aimail` 块、也没有 skills/public/aimail/SKILL.md,
而门禁全绿(旧边界测试只查"pysdk 是否引用 CLI 程序路径",不查"调用的子命令是否还在")。

本测试把"交付的 shell 脚本调用的 CLI 子命令必须存在于 CLI 子命令表"变成判决项:
命令面一改名/删除, 消费者立刻红, 而不是静默降级。

范围与判据(有意收窄, 避免误报):
  * 扫描面 = 交付的 **shell 脚本**(pysdk/**/*.sh、scripts/**/*.sh)——真调用面;
    .py/.md 里的 `aimail <token>` 多是散文(提示语/文档), 不参与判决。
  * 只认**命令位**(行首 / 控制操作符后 / `$(` / `if|then|…` 之后)的调用形态,
    字符串内的散文不计。
  * 整行注释与行内 ` #` 注释先剥掉。
  * 子命令表取 CLI **运行时** choices(argparse), 静态解析仅兜底; 两者都拿不到即
    报"无法判定"(不假装通过)。
"""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ANCHORS = {"install", "uninstall", "check", "version"}   # 子命令表必须含这些(防空集假通过)
SCAN_GLOBS = ("pysdk/**/*.sh", "scripts/**/*.sh")
SKIP_PARTS = ("node_modules", "__pycache__", ".git/")

# 命令位: 行首 / ; & | ( ` / shell 关键字之后
CMD_POS = r'(?:^|[;&|(`]|\b(?:if|then|else|elif|do|!)\s+)\s*'
# 命令词: 裸名 aimail 或路径形态 cli/aimail(可带引号与 $SRC/ 前缀)
CMD_WORD = r'"?(?:\./)?(?:\$SRC/|"\$SRC/)?cli/aimail"?|(?<![\w./-])"?' + 'aimail' + r'"?'
INVOKE_RE = re.compile(CMD_POS + r'(?:' + CMD_WORD + r')\s+([a-z][a-z-]*)', re.M)


def cli_subcommands() -> set:
    out = set()
    try:
        r = subprocess.run(["python3", str(REPO / "cli" / "aimail"), "__probe__"],
                           capture_output=True, text=True, timeout=60)
        m = re.search(r"choose from ([^\n]+)", r.stderr or "")
        if m:
            out = set(re.findall(r"'([a-z][a-z-]*)'", m.group(1)))
    except Exception:  # noqa: BLE001
        pass
    if not out:  # 兜底: 静态取顶层 sub.add_parser("x")
        src = (REPO / "cli" / "aimail").read_text(errors="ignore")
        out = set(re.findall(r'^\s*sub\.add_parser\(\s*"([a-z][a-z-]*)"', src, re.M))
    return out


def code_lines(path: Path):
    for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        yield i, line.split(" #", 1)[0]


def test_cli_subcommand_table_is_available():
    subs = cli_subcommands()
    assert subs, "拿不到 CLI 子命令表(运行时与静态两条路都失败) ⇒ 无法判定, 不假装通过"
    missing = ANCHORS - subs
    assert not missing, f"子命令表异常(缺 {missing}); 提取器可能已失效: {sorted(subs)}"


def test_delivered_scripts_only_call_existing_subcommands():
    subs = cli_subcommands()
    offenders, checked = [], 0
    for pattern in SCAN_GLOBS:
        for path in REPO.glob(pattern):
            rel = str(path.relative_to(REPO))
            if any(k in rel for k in SKIP_PARTS):
                continue
            for i, code in code_lines(path):
                for token in INVOKE_RE.findall(code):
                    checked += 1
                    if token not in subs:
                        offenders.append(
                            f"{rel}:{i}: 调用了不存在的子命令 '{token}' → {code.strip()[:90]}")
    assert checked > 0, "一个 CLI 调用点都没扫到(锚定式可能已失效) ⇒ 不假装通过"
    assert not offenders, (
        "交付脚本调用了 CLI 上不存在的子命令(命令面改名后消费者没跟上; "
        "这类坏掉常被 on_error=warn 降级成警告 ⇒ 必须在这里拦):\n" + "\n".join(offenders))
