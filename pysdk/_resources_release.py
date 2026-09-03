#!/usr/bin/env python3
"""_resources_release — SDK 资源的本地配置目录展开(python 版)。

架构:资源(role_prompt/role_soul × en/zh + skills)是公共种子,随 SDK
分发;安装/启动时释放到 ~/.aimail/systems/{sid}/board/ 供运行时读取
(pysdk 与 tssdk 运行时同路径)。只补缺失/更新的文件,绝不覆盖用户已在
配置目录个性化过的内容。

与 tssdk mail-core release-resources.ts、cli 旧 release-board-resources.sh
同语义;本模块为 pip/repo 双形态的 python 实现。
"""
from __future__ import annotations

import os
import shutil
import sys

# 双形态自举:core 目录(含本模块与 aimail_base.py)即 _CORE
_CORE = os.path.dirname(os.path.abspath(__file__))

# 源子目录(包内 resources/board) → 配置目录目标子目录
_DIR_MAP = (
    ("role_prompt_en", "role_prompt"),
    ("role_prompt_zh", "role_prompt_zh"),
    ("role_soul_en", "role_soul"),
    ("role_soul_zh", "role_soul_zh"),
)

_AIMAIL_HOME = os.path.join(os.path.expanduser("~"), ".aimail")


def agentmail_home() -> str:
    return os.environ.get("AIMAIL_HOME", "") or _AIMAIL_HOME


def resources_board_dir() -> str:
    """包内 resources/board 目录(repo:pysdk/resources/board;pip:aimail/resources/board)。"""
    return os.path.join(_CORE, "resources", "board")


def release_resources(system_id: str, board_root: str | None = None) -> dict:
    """释放 board 资源到 ~/.aimail/systems/{sid}/board/(幂等)。"""
    src_root = board_root or resources_board_dir()
    board_dir = os.path.join(agentmail_home(), "systems", system_id, "board")
    copied = 0
    skipped = 0
    for src_name, dst_name in _DIR_MAP:
        src_dir = os.path.join(src_root, src_name)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(board_dir, dst_name)
        os.makedirs(dst_dir, exist_ok=True)
        for fname in sorted(os.listdir(src_dir)):
            if not fname.endswith(".md"):
                continue
            src = os.path.join(src_dir, fname)
            dst = os.path.join(dst_dir, fname)
            if os.path.exists(dst):
                if os.path.getmtime(dst) >= os.path.getmtime(src):
                    skipped += 1
                    continue
            shutil.copy2(src, dst)
            copied += 1
    return {"board_dir": board_dir, "copied": copied, "skipped": skipped}


def release_all_systems(board_root: str | None = None) -> list:
    """对 ~/.aimail/systems/ 下全部已有系统展开(单系统机器亦覆盖)。"""
    systems_root = os.path.join(agentmail_home(), "systems")
    if not os.path.isdir(systems_root):
        return []
    out = []
    for ent in sorted(os.listdir(systems_root)):
        p = os.path.join(systems_root, ent)
        if os.path.isdir(p):
            try:
                out.append(release_resources(ent, board_root))
            except Exception:  # noqa: BLE001
                pass
    return out


if __name__ == "__main__":
    # 便捷:python _resources_release.py [system_id...]
    sids = sys.argv[1:] or sorted(
        d for d in os.listdir(os.path.join(agentmail_home(), "systems"))
        if os.path.isdir(os.path.join(agentmail_home(), "systems", d))
    ) if os.path.isdir(os.path.join(agentmail_home(), "systems")) else []
    for sid in sids:
        r = release_resources(sid)
        print(f"{sid}: {r['board_dir']} (copied {r['copied']}, kept {r['skipped']})")
