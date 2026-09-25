"""SDK 资源单一真源契约(2026-09-25 改造)。

约定:
  * 真源 = 仓库根 ``resources/{board,skills}`` —— git 跟踪, 唯一可编辑处。
  * 4 处分发点(``pysdk/resources`` 与 ``tssdk/packages/{dsh,openclaw,pi}-aimail/resources``)
    是 ``scripts/materialize-resources.sh`` 的**生成物**: 必须被 git 忽略、不得入库;
    存在时**必须与真源逐字节一致**(否则就是"只改了一份"的漂移)。
  * npm 产物自包含由各包 ``prepack`` 保证; wheel 自包含由 pyproject 的目录级
    force-include(源=仓根 ``resources/``)保证;产物层指纹校验在 L2 check-tarball.sh。

本测试只读、无外部依赖(不钉开发机绝对路径 —— 仓根由本文件位置推导)。
"""
import hashlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CANON = REPO / "resources"
TARGETS = {
    "pysdk": REPO / "pysdk" / "resources",
    "dsh": REPO / "tssdk" / "packages" / "dsh-aimail" / "resources",
    "openclaw": REPO / "tssdk" / "packages" / "openclaw-aimail" / "resources",
    "pi": REPO / "tssdk" / "packages" / "pi-aimail" / "resources",
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
        "物化产物被 git 跟踪(应 gitignore + `git rm -r --cached`):\n"
        + "\n".join(tracked.splitlines()[:5]))


def test_generated_copies_are_gitignored():
    for name, d in TARGETS.items():
        rel = str(d.relative_to(REPO)) + "/board"   # 判规则(路径不存在也算)
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", rel],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0, f"{name}: {rel} 未被 .gitignore 忽略"


def test_existing_copies_match_canonical():
    """存在即必须一致 —— 未物化(clean clone)不算失败, 由门禁/CI 先跑物化步骤。"""
    canon = _fingerprint(CANON)
    present = {n: d for n, d in TARGETS.items() if d.is_dir()}
    for name, d in present.items():
        fp = _fingerprint(d)
        extra = sorted(set(fp) - set(canon))
        missing = sorted(set(canon) - set(fp))
        diff = sorted(k for k in set(canon) & set(fp) if canon[k] != fp[k])
        assert not (extra or missing or diff), (
            f"{name} 物化产物与真源漂移: extra={extra} missing={missing} diff={diff}\n"
            f"  → 跑 scripts/materialize-resources.sh 重新物化")
