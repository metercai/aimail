"""宿主 peer 范围判据(2026-10-11)—— S-1 类漂移的通用闸。

背景(dsh × 6 格,L3):`dsh-aimail` 的宿主 `peerDependencies` 钉在旧线
(`@deepseek-ai/dsh-agent: ^0.1.0-rc.6`),而 `tests/agent-env/pins.env` 钉的宿主
(`DSH_VERSION`)走 `next` 线(0.2.0-rc.2)⇒ dsh 的兼容闸直接拒装插件,平台装不进去。
同类风险对 pi / openclaw 同样成立,只是恰好还满足。

判据:对每个"声明了宿主 peer 的包",断言 pins.env 钉的宿主版本**满足**该范围。
判定交给 semver(node;tssdk 的 pnpm store 里已有),不手写 semver —— prerelease
的"同 tuple 才放行"规则极易写错:本轮实测 `>=0.1.0-rc.6 <0.3.0` 就**不**匹配
`0.2.0-rc.2`,而 `^0.1.0-rc.6 || ^0.2.0-rc.2` 才匹配。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent

# 宿主 → (pins.env 的键, 该宿主**与本宿主同版本发布**的 peer 目标包集合)。
# 实测依据(2026-10-11,aimail-host-dsh 镜像):dsh 0.2.0-rc.2 自带
# dsh-agent/dsh-llm/dsh-tools 0.2.0-rc.2(同版本线)⇒ 可用宿主版本作目标版本。
# 2026-10-22: 5 包合并为单包 agent-aimail — peer 集合收敛到单一 manifest。
# 单包同时带三宿主的 peer, 同一包名不能做 dict key ⇒ 表改成 list。
# 每个宿主 peer 仍按各自 pin 判同版本线; 未列出的 peer 必须登记 _EXCLUDED_PEERS。
_HOSTS = [
    ("agent-aimail", "DSH_VERSION", {
        "@deepseek-ai/dsh-agent", "@deepseek-ai/dsh-llm", "@deepseek-ai/dsh-tools"}),
    ("agent-aimail", "PI_VERSION", {"@earendil-works/pi-coding-agent"}),
    ("agent-aimail", "OPENCLAW_VERSION", {"openclaw"}),
]

# 无法用"宿主版本"作目标版本的 peer:必须在此显式登记原因(新 peer 不许静默漏检)。
_EXCLUDED_PEERS = {
    # cordis 的版本与 dsh 宿主版本**不同线**(dsh 0.2.0-rc.2 自带 cordis 4.0.4),
    # 离线拿不到它的真值 ⇒ 本轮人工核过一次(`^4.0.1` 满足 4.0.4,故 dsh 未点它);
    # 若要自动判,需要一个"宿主 → 各兄弟包版本"的离线真源(另议)。
    "@deepseek-ai/cordis": "版本与宿主版本不同线(实测 dsh 0.2.0-rc.2 自带 cordis 4.0.4)",
}


def _pins() -> dict:
    pins = {}
    for line in (_REPO / "tests" / "agent-env" / "pins.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            pins[k.strip()] = v.strip()
    return pins


def _semver_dir() -> Path | None:
    """pnpm store 里的 semver(CI: sdk-gates 先跑 `pnpm install --frozen-lockfile`)。"""
    cands = []
    store = _REPO / "tssdk" / "node_modules" / ".pnpm"
    if store.is_dir():
        cands += sorted(store.glob("semver@*/node_modules/semver"))
    cands += sorted((_REPO / "tssdk" / "node_modules").glob("semver"))
    for c in cands:
        if (c / "package.json").is_file():
            return c
    return None


def _satisfies(version: str, rng: str, semver_dir: Path) -> bool:
    js = "const s=require(process.env.SV);console.log(s.satisfies(process.env.V,process.env.R)?'1':'0')"
    out = subprocess.run(
        ["node", "-e", js],
        env={**os.environ, "SV": str(semver_dir), "V": version, "R": rng},
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise AssertionError(f"semver 判定不可用(node rc={out.returncode}): {out.stderr.strip()[:200]}")
    return out.stdout.strip() == "1"


@pytest.fixture(scope="module")
def semver_dir() -> Path:
    d = _semver_dir()
    if d is None:
        pytest.skip("semver 未安装——先在 tssdk 跑 `pnpm install`(CI 的 sdk-gates 会跑)")
    if not _has_node():
        pytest.skip("node 不可用")
    return d


def _has_node() -> bool:
    try:
        return subprocess.run(["node", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def test_judge_itself_detects_the_s1_condition(semver_dir):
    """负向自证:判据必须能红 —— 旧声明(钉旧线)对 0.2.0-rc.2 判 false。"""
    assert not _satisfies("0.2.0-rc.2", "^0.1.0-rc.6", semver_dir), "判据恒真=白装"
    assert _satisfies("0.2.0-rc.2", "^0.1.0-rc.6 || ^0.2.0-rc.2", semver_dir)


def test_host_peers_accept_the_pinned_host_version(semver_dir):
    pins = _pins()
    bad, unchecked = [], []
    # 单包同时带多宿主 peer: 属于任一宿主行的 peer 在该行迭代里接受, 其余行跳过
    # (旧 5 包时代每包只带自己宿主的 peer, 无需此并集)。
    union = set()
    for _, _, peers in _HOSTS:
        union |= set(peers)
    for pkg, pin_key, same_line in _HOSTS:
        pj = json.loads((_REPO / "tssdk" / "packages" / pkg / "package.json").read_text(encoding="utf-8"))
        host_ver = pins.get(pin_key, "")
        assert host_ver, f"pins.env 缺 {pin_key}"
        for peer, rng in (pj.get("peerDependencies") or {}).items():
            if peer in same_line:
                if not _satisfies(host_ver, rng, semver_dir):
                    bad.append(f"{pkg}: {peer} {rng} 不接受 {pin_key}={host_ver}")
            elif peer in union:
                continue  # 其他宿主行的 peer, 在它的行迭代里接受
            elif peer not in _EXCLUDED_PEERS:
                unchecked.append(f"{pkg}: {peer} {rng}")
    assert not unchecked, ("新增了未分类的宿主 peer —— 要么纳入同版本线判定,"
                           f"要么登记到 _EXCLUDED_PEERS 并写明原因: {unchecked}")
    assert not bad, "宿主 peer 不接受 pins.env 钉的宿主版本(S-1 类): " + "; ".join(bad)
