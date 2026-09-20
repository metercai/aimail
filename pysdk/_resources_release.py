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

import hashlib
import json
import os
import shutil
import sys

# 双形态自举:core 目录(含本模块与 aimail_base.py)即 _CORE
_CORE = os.path.dirname(os.path.abspath(__file__))

# 发布清单: 记录"本 SDK 上次发布的内容 hash", 用来区分"用户个性化过"与"用户没动过"
_MANIFEST = ".aimail-resources.json"

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


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(dst_dir: str) -> dict:
    try:
        with open(os.path.join(dst_dir, _MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 — 缺/坏清单等价于"无记录"
        return {}


def _save_manifest(dst_dir: str, data: dict) -> None:
    tmp = os.path.join(dst_dir, _MANIFEST + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(dst_dir, _MANIFEST))


def release_resources(system_id: str, board_root: str | None = None) -> dict:
    """释放 board 资源到 ~/.aimail/systems/{sid}/board/(幂等)。

    判定口径(审计 D6, 2026-09-21): **按内容**而非 mtime。
      · 目标缺失            → 释放
      · 目标内容 == 包内      → 跳过(已同版)
      · 目标内容 == 上次发布 hash(用户没动过) → 覆盖为包内新内容(SDK 升级生效)
      · 否则(用户个性化过/无记录) → 跳过, 绝不覆盖用户内容
    mtime 判定会漏发: SDK 升级解包时间戳早于目标文件、或用户 touch 过目标,
    新内容就永远发不出去。清单记在目标目录的 .aimail-resources.json。
    """
    src_root = board_root or resources_board_dir()
    board_dir = os.path.join(agentmail_home(), "systems", system_id, "board")
    copied = 0
    updated = 0
    skipped = 0
    for src_name, dst_name in _DIR_MAP:
        src_dir = os.path.join(src_root, src_name)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(board_dir, dst_name)
        os.makedirs(dst_dir, exist_ok=True)
        manifest = _load_manifest(dst_dir)
        dirty = False
        for fname in sorted(os.listdir(src_dir)):
            if not fname.endswith(".md"):
                continue
            src = os.path.join(src_dir, fname)
            dst = os.path.join(dst_dir, fname)
            src_hash = _sha256(src)
            if os.path.exists(dst):
                dst_hash = _sha256(dst)
                if dst_hash == src_hash:
                    skipped += 1
                    continue
                recorded = manifest.get(fname)
                if recorded and dst_hash == recorded:
                    shutil.copy2(src, dst)
                    manifest[fname] = src_hash
                    updated += 1
                    dirty = True
                    continue
                skipped += 1        # 用户个性化过(或无记录): 绝不覆盖
                continue
            shutil.copy2(src, dst)
            manifest[fname] = src_hash
            copied += 1
            dirty = True
        if dirty or not os.path.exists(os.path.join(dst_dir, _MANIFEST)):
            _save_manifest(dst_dir, manifest)
    return {"board_dir": board_dir, "copied": copied, "updated": updated, "skipped": skipped}


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
