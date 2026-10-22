# -*- coding: utf-8 -*-
"""宿主检测 —— "给定宿主 home,回答这里实际能跑哪些宿主"。

移植自 tssdk `src/hosts/detect.ts`,语义逐条对齐:
  · 多信号 lazy、never throws —— 信号缺失只是"未检测到",不是错误;
  · 候选根 = [home, home/<home_dir>]:调用方给宿主根(~/.hermes)或父目录(~);
  · 信号来源(与 registry 同真源):
      ① marker 在位(host 特征目录,如 ~/.hermes/hermes-agent)
      ② 已装证据(host 的 aimail 指针文件 <root>/<POINTER_FILE> 存在)

与 CLI detect.rs 的分工:CLI 做"装配期"探测(未装态也要定位 home);
本层做"运行时"识别(入口加载时确认 declared 与实际环境一致)。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from . import registry

# 指针文件名 = 契约字面量,单真源(门禁棘轮:禁裸写该字面量)。
# 双形态:pip 布局 core 在 aimail 包内;仓库 dev / bundle 布局 core 已挂 sys.path。
try:
    from aimail.aimail_contract import POINTER_FILE as _POINTER_FILE
except ImportError:  # pragma: no cover - 布局分支
    from aimail_contract import POINTER_FILE as _POINTER_FILE


def _candidate_roots(home: str, host: str) -> List[str]:
    """候选根:[home, home/<home_dir>](父目录语义,与 CLI candidate_roots 一致)。"""
    roots = [home]
    home_dir = (registry.get_host(host) or {}).get("home_dir", "")
    if home_dir:
        nested = os.path.join(home, home_dir)
        if os.path.isdir(nested):
            roots.append(nested)
    return roots


def _signals_present(home: str, host: str) -> Dict[str, bool]:
    """该宿主在 home 下的信号集合(marker / 已装指针)。"""
    spec = registry.get_host(host) or {}
    out = {"marker": False, "installed": False}
    for root in _candidate_roots(home, host):
        marker = spec.get("marker", "")
        if marker and os.path.exists(os.path.join(root, marker)):
            out["marker"] = True
        if os.path.isfile(os.path.join(root, _POINTER_FILE)):
            out["installed"] = True
        if out["marker"] and out["installed"]:
            break
    return out


def detect_host(home: str) -> Optional[str]:
    """在 home 里识别出**恰好一个** python 宿主时返回其 id;
    零个或多个命中 ⇒ None(歧义/空,由 activate 判 consistent,不抛)。"""
    hits = [h for h in registry.host_ids() if _signals_present(home, h)["marker"]]
    return hits[0] if len(hits) == 1 else None


def detect_hosts_present(home: str) -> List[str]:
    """home 里**全部**命中的宿主 id(多宿主共存时不丢信息)。"""
    return [h for h in registry.host_ids() if _signals_present(home, h)["marker"]]
