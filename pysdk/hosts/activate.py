# -*- coding: utf-8 -*-
"""宿主激活判定 —— declared vs detected 的 verified/consistent 语义。

移植自 tssdk `src/hosts/activate.ts`,语义逐条对齐:

  · exactly-one signal  ⇒ verified=True(环境恰好是声明的那个宿主)
  · declared 命中 0     ⇒ consistent=False(装错宿主),**warn only, never raise**
    (tssdk 原文:"never throw on mismatch — the host may be mid-bootstrap")
  · 多宿主共存          ⇒ 仍按 declared 激活,但 consistent 反映歧义

入口调用点(与 tssdk "activate on entry load" 同构):
  hermes:  aimail_hermes.py 模块加载/注册入口
  deerflow: manage.py 注册入口
"""
from __future__ import annotations

import os
import sys
from typing import Dict, Optional

from . import detect, registry

_WARN_PREFIX = "[aimail-hosts]"


def _warn(msg: str) -> None:
    # 识别层永不阻塞宿主:诊断走 stderr(tssdk console.warn 同语义)
    print(f"{_WARN_PREFIX} {msg}", file=sys.stderr)


def activate_host(declared: str, home: str) -> Dict[str, object]:
    """按 declared(调用方显式声明的宿主 id)在 home 里复验。

    Returns:
      {
        "declared":   str,   # 声明值(原样)
        "detected":   str|None,  # home 里恰好命中一个宿主时的 id
        "present":    [str],     # home 里全部命中的宿主 id
        "known":      bool,      # declared 是否在注册表
        "verified":   bool,      # 恰好一个信号且 = declared
        "consistent": bool,      # declared ∈ present(装错宿主 ⇒ False)
      }

    never raises:未知 declared / 检测空 ⇒ 返回结构 + warn。
    """
    declared = str(declared or "").strip()
    home = str(home or "").strip()
    known = registry.get_host(declared) is not None
    if not known:
        _warn(
            f"unknown declared host {declared!r} "
            f"(known: {', '.join(registry.host_ids())})"
        )

    present = detect.detect_hosts_present(home) if home else []
    det = detect.detect_host(home) if home else None

    verified = known and det is not None and det == declared
    consistent = known and (declared in present)

    if not consistent:
        _warn(
            f"declared host {declared!r} not detected at {home!r} "
            f"(present: {present or 'none'}) — check --home / host layout"
        )
    return {
        "declared": declared,
        "detected": det,
        "present": present,
        "known": known,
        "verified": verified,
        "consistent": consistent,
    }
