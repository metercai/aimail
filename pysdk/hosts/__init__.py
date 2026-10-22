# -*- coding: utf-8 -*-
"""aimail.hosts — Python 宿主识别层(移植 tssdk hosts 三件套,2026-10-21 合并方案)。

共同主体架构下的宿主单真源:

  registry.py   HOSTS 表(hermes / deerflow)—— 加新 python 宿主 = 加一条目
  detect.py     在宿主 home 里找"实际装了哪些宿主"(多信号、lazy、never throws)
  activate.py   declared vs detected 的 verified/consistent 判定(注册入口复验)

对齐纪律(单真源,禁漂移):
  · marker / home_dir 与 CLI 宿主表(cli/src/hosts/{hermes,deerflow}.rs)逐字段一致
  · 语义与 tssdk src/hosts/{registry,detect,activate}.ts 一致:
      exactly-one signal ⇒ verified; mismatch ⇒ warn only, never raise
  · CLI 的宿主表仍是**平台知识**的 CLI 侧真源(检测/指针/安装步);本层是 SDK
    侧的**运行时识别**真源 —— 两侧字段必须一致,改一侧必须同步另一侧。
"""
from . import detect, registry, activate  # noqa: F401

__all__ = ["detect", "registry", "activate", "activate_host", "HOSTS", "host_ids", "get_host"]

from .registry import HOSTS, host_ids, get_host  # noqa: F401,E402
from .activate import activate_host  # noqa: F401,E402
