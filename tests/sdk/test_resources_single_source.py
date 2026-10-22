"""SDK 资源单一真源契约(2026-09-25 改造; 2026-10-11 取消仓级物化脚本)。

约定:
  * 真源 = 仓库根 ``resources/{board,skills}`` —— git 跟踪, 唯一可编辑处。
  * 各 adapter 包内 ``resources/`` 是**各包 prepack 自己暂存**的生成物
    (``tssdk/packages/agent-aimail/scripts/stage-resources.mjs --dest .``):
    必须被 git 忽略、不得入库;存在时**必须与真源逐字节一致**(否则就是"只改了一份"的漂移)。
  * pysdk 不落副本: 仓库检出里 SDK 直接读仓根 ``resources/``
    (``_resources_release.resources_root``);wheel 自包含由 pyproject 的目录级
    force-include(源=仓根 ``resources/``)保证;产物层指纹校验在 L2 check-tarball.sh。

本测试只读、无外部依赖(不钉开发机绝对路径 —— 仓根由本文件位置推导)。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANON = REPO / "resources"
# 只列**包内**生成物: pysdk 不再需要副本(仓库检出直接读真源)。
# 2026-10-22 单包合并: 原 3 适配宿主包各自暂存一份 ⇒ 收敛为 agent-aimail 单包内的一份。
TARGETS = {
    "agent-aimail": REPO / "tssdk" / "packages" / "agent-aimail" / "resources",
}
EXPECTED_FILE_COUNT = 8  # 6 board role prompts(role_prompt/) + 2 skills


def _fingerprint(d: Path) -> dict:
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip()


def test_canonical_source_exists_and_is_complete():
    for sub in ("board", "skills"):
        assert (CANON / sub).is_dir(), f"真源缺 {sub}/: {CANON}"
    fp = _fingerprint(CANON)
    assert len(fp) == EXPECTED_FILE_COUNT, (
        f"真源文件数 {len(fp)} != 契约 {EXPECTED_FILE_COUNT};"
        f"增删资源请同步本断言与 pyproject/check-tarball 口径: {sorted(fp)}")
    for sub in ("role_prompt",):
        assert (CANON / "board" / sub).is_dir(), f"真源缺 board/{sub}/"
    for f in ("SKILL.md", "DESCRIPTION.md"):
        assert (CANON / "skills" / f).is_file(), f"真源缺 skills/{f}"


def test_generated_copies_are_not_tracked_by_git():
    """生成物绝不入库(否则又变回"N 份副本"的老问题)。"""
    rels = [str(t.relative_to(REPO)) for t in TARGETS.values()]
    tracked = _git("ls-files", "--", *[r + "/" for r in rels])
    assert tracked == "", (
        "包内生成物被 git 跟踪(应 gitignore + `git rm -r --cached`):\n"
        + "\n".join(tracked.splitlines()[:5]))


def test_generated_copies_are_gitignored():
    for name, d in TARGETS.items():
        rel = str(d.relative_to(REPO)) + "/board"   # 判规则(路径不存在也算)
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", rel],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0, f"{name}: {rel} 未被 .gitignore 忽略"


def test_existing_copies_match_canonical():
    """存在即必须一致 —— 未暂存(clean clone)不算失败, 由各包 prepack / 门禁暂存。"""
    canon = _fingerprint(CANON)
    present = {n: d for n, d in TARGETS.items() if d.is_dir()}
    for name, d in present.items():
        fp = _fingerprint(d)
        extra = sorted(set(fp) - set(canon))
        missing = sorted(set(canon) - set(fp))
        diff = sorted(k for k in set(canon) & set(fp) if canon[k] != fp[k])
        assert not (extra or missing or diff), (
            f"{name} 包内生成物与真源漂移: extra={extra} missing={missing} diff={diff}\n"
            f"  → 跑该包自己的 prepack: node tssdk/packages/agent-aimail/scripts/"
            f"stage-resources.mjs --dest tssdk/packages/agent-aimail/resources")


def test_sdk_reads_the_canonical_dir_in_a_checkout():
    """pysdk 侧闭环: 仓库检出里资源根就是仓根真源(不依赖任何副本/物化步骤)。"""
    sys.path.insert(0, str(REPO / "pysdk"))
    try:
        import _resources_release as R  # noqa: PLC0415 — 仅为触发仓库态解析
    finally:
        sys.path.pop(0)
    root = Path(R.resources_root()).resolve()
    assert root == CANON.resolve(), (
        f"仓库检出里 resources_root()={root} 应指向真源 {CANON.resolve()}")
    for d in (R.resources_board_dir(), R.resources_skills_dir()):
        assert Path(d).is_dir(), f"{d} 不存在(SDK 应能直接读真源)"
    assert (Path(R.resources_skills_dir()) / "SKILL.md").is_file(), "真源 SKILL.md 不可见"
