#!/usr/bin/env python3
"""run-s1-local.py — 场景 1（单 Agent 专属地址）上线后回归，本地门。

为什么在本地：aimail-advanced 闭源，不能进 GitHub Actions runner。
所以场景 1 的"发布后回归"不走 L3 云矩阵，只在本机（linux）上用两个典型宿主
（hermes / dsh）跑真实用户链。

被测物（全部用**已发布**产物，除闭源 advanced 用本地构建件并记录 sha256）：
  · CLI        = cli-v0.1.56 的 linux-amd64 发布资产（--aimail-bin）
  · SDK        = aimail install 时装入宿主的那一份（agent-aimail 0.1.56 / @aimail/mail-core 0.1.55）
  · 网关       = aimail-advanced 1.0.2（--adv-bin，闭源，只本地）
  · 宿主       = 真实 hermes / dsh（official install，隔离 HOME）

用户链（与 docs/agent-self-setup.md 一致，prompt 由产品 apply 链生成，不手写）：
  1. 公开申请链 email-verify → apply-address → 模板 A 邮件 → prompt 行 + shared_a-… 激活码
  2. 宿主侧：aimail agent setup --host <h> --apply（装适配器/SKILL）
  3. 宿主自己的 SDK 门：activate_address_code(code, addr)（py）/ activateAddressCode（ts）
  4. 断言 binding 七字段 + 0600/0700 + .agentmail 指针  # contract-allowed: 文档说明需要写出指针文件名
  5. 发信（宿主门 send_mail）→ 入站投递到该地址 → pull 取回唯一串
  6. 服务端：agent 作用域 + webhook_url = NULL

用法：
  python3 tests/l3/run-s1-local.py --host hermes --adv-bin ../aimail-advanced/target/release/aimail-advanced \
      --aimail-bin /path/to/aimail-0.1.56-linux-amd64 [--evidence /tmp/s1-local]
退出码：0 PASS · 1 FAIL · 2 CANNOT JUDGE（缺二进制/宿主）
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests" / "agent-env"))
sys.path.insert(0, str(REPO / "pysdk"))

from aimail_contract import BINDING_FILE, POINTER_FILE  # noqa: E402  契约单源


def sh(cmd: list[str], env: dict | None = None, timeout: int = 900, log: Path | None = None) -> tuple[int, str]:
    """跑命令；给了 log 就边跑边落盘（长任务可见进度，失败可诊断）。"""
    e = dict(os.environ)
    if env:
        e.update(env)
    if log is None:
        p = subprocess.run(cmd, capture_output=True, text=True, env=e, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    buf: list[str] = []
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=e, bufsize=1)
        assert p.stdout is not None
        for line in p.stdout:
            fh.write(line)
            fh.flush()
            buf.append(line)
        p.wait(timeout=timeout)
    return p.returncode, "".join(buf)


class Result:
    def __init__(self, host: str):
        self.host = host
        self.cases: list[dict] = []

    def rec(self, cid: str, desc: str, ok: bool, evidence: str = "") -> None:
        self.cases.append({"id": cid, "desc": desc, "ok": bool(ok), "evidence": evidence[:400]})
        print(f"  [{'PASS' if ok else 'FAIL'}] {self.host}-{cid}: {desc}" + (f" :: {evidence[:160]}" if evidence else ""))

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if not c["ok"])


def find_python(host_home: Path) -> str:
    cands = [host_home / "hermes-agent" / ".venv" / "bin" / "python3",
             host_home / "hermes-agent" / "venv" / "bin" / "python3"]
    cands += [Path(p) for p in glob.glob(str(host_home / "tools" / "*" / "bin" / "python3"))]
    cands += [Path(p) for p in glob.glob(str(host_home / "*" / ".venv" / "bin" / "python3"))]
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, choices=["hermes", "dsh"])
    ap.add_argument("--adv-bin", required=True)
    ap.add_argument("--aimail-bin", required=True)
    ap.add_argument("--evidence", default="/tmp/s1-local")
    ap.add_argument("--work", default="")
    ap.add_argument("--prepare", action="store_true", help="用 agent-env/prepare.sh 现场安装(CI 口径)")
    ap.add_argument("--host-src", default="", help="宿主源安装目录(默认 ~/.hermes 或 ~/.dsh)")
    a = ap.parse_args()

    adv_bin = Path(a.adv_bin).resolve()
    cli = Path(a.aimail_bin).resolve()
    for p, what in ((adv_bin, "advanced 二进制"), (cli, "已发布 CLI")):
        if not (p.is_file() and os.access(p, os.X_OK)):
            print(f"CANNOT JUDGE: {what} 不存在/不可执行: {p}")
            return 2

    ev = Path(a.evidence) / a.host
    ev.mkdir(parents=True, exist_ok=True)
    work = Path(a.work or f"/tmp/s1-local-{a.host}-{uuid.uuid4().hex[:8]}")
    work.mkdir(parents=True, exist_ok=True)
    host_home = work / "host-home" / (".hermes" if a.host == "hermes" else ".dsh")

    r = Result(a.host)
    meta = {
        "host": a.host,
        "advanced_bin": str(adv_bin),
        "advanced_sha256": hashlib.sha256(adv_bin.read_bytes()).hexdigest(),
        "cli_bin": str(cli),
        "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
        "cli_version": sh([str(cli), "version"])[1].strip()[:80],
        "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "work": str(work),
    }
    print(f"══ 场景1 本地回归 / {a.host}")
    print(f"   CLI: {meta['cli_version']} ({(meta['cli_sha256'])[:12]}…)")
    print(f"   advanced: {(meta['advanced_sha256'])[:12]}…  work={work}")

    # 夹具来自 advanced 仓的 A1 夹具（真二进制、per-run 端口、录制型 sink）
    adv_repo = Path(os.environ.get("ADV_REPO", REPO.parent / "aimail-advanced"))
    sys.path.insert(0, str(adv_repo / "tests" / "SDK" / "agent-level" / "lib"))
    try:
        from gateway_fixture import Fixture  # noqa: E402
    except Exception as e:  # pragma: no cover
        print(f"CANNOT JUDGE: 无法导入 gateway_fixture ({adv_repo}): {e}")
        return 2

    fx = Fixture(work=str(work / "gw"), adv_bin=str(adv_bin))
    rc = 1
    try:
        fx.start()
        admin = (Path(fx.work) / "aimail.db.admin_key").read_text().strip()
        r.rec("S1-00", "夹具网关起来(advanced 真二进制)", True, fx.gw)
        r.rec("S1-00b", "admin key 落盘", bool(admin), admin[:8] + "…")

        # 1) 宿主环境：每次都是**干净的一份**。
        #    CI 用 prepare.sh 现场安装；本地回归不重复下载 3.6G 的宿主树 ——
        #    改为从本机已有的官方安装做一次干净快照拷贝（源目录只读，不污染）。
        #    状态（bindings/config）全在新拷贝里，跑完即弃。
        if a.prepare:
            rc_p, out = sh(["bash", str(REPO / "tests" / "agent-env" / "prepare.sh"), a.host,
                            "--home", str(host_home), "--no-verify"], timeout=3600,
                           log=ev / "prepare.log")
            r.rec("S1-01", f"{a.host} 官方安装(prepare.sh)", rc_p == 0, out[-300:])
        else:
            src = Path(a.host_src or (Path.home() / (".hermes" if a.host == "hermes" else ".dsh")))
            if not src.is_dir():
                print(f"CANNOT JUDGE: 宿主源目录不存在: {src}（用 --host-src 指定或用 --prepare）")
                return 2
            rc_c, out_c = sh(["bash", "-c",
                              f"mkdir -p {host_home} && "
                              + (f"cp -a {src}/hermes-agent {host_home}/ && cp -a {src}/config.yaml {host_home}/"
                                 if a.host == "hermes" else f"cp -a {src}/. {host_home}/")],
                             timeout=1800, log=ev / "prepare.log")
            r.rec("S1-01", f"{a.host} 干净快照（源 {src}，只读拷贝）", rc_c == 0, out_c[-200:])

        # 2) 场景 1 = 地址级：没有 aimail install/系统激活。
        #    第一步是"拿到工具"——aimail agent setup --host <h> --apply（装适配器/SKILL/SDK）。
        (work / "host-home").mkdir(exist_ok=True)
        host_home.parent.mkdir(parents=True, exist_ok=True)
        py_hint = find_python(host_home) if a.host == "hermes" else ""
        env = {"HOME": str(work / "host-home"), "AGENT_HOME": str(host_home),
               "AIMAIL_HOME": str(work / "aimail-home")}
        if py_hint:
            env["PATH"] = str(Path(py_hint).parent) + ":" + os.environ.get("PATH", "")
        rc_q, out_q = sh([str(cli), "agent", "doctor", "--questions"], env=env, timeout=600,
                         log=ev / "doctor-questions.log")
        r.rec("S1-02", "agent doctor --questions 出题(prompt 面)", rc_q == 0 and len(out_q) > 40, out_q[:200])

        rc_s, out_s = sh([str(cli), "agent", "setup", "--host", a.host, "--apply"], env=env, timeout=1800,
                         log=ev / "setup-apply.log")
        applied = '"applied": true' in out_s or "'applied': True" in out_s
        r.rec("S1-03", "agent setup --host … --apply 装上工具", rc_s == 0 and applied, out_s[-300:])

        # 3) 产品真实 apply 链 → prompt 行 + 激活码（不手写 prompt）
        info = fx.apply_address(agentname=f"{a.host}s1")
        addr, code, prompt_line = info["address"], info["code"], info["prompt_line"]
        r.rec("S1-04", "apply 链产出 prompt 行 + shared_a 激活码", bool(prompt_line and code),
              f"addr={addr} code={code[:14]}… prompt_line={prompt_line[:90]}")
        (ev / "prompt.txt").write_text(info["prompt"])

        # 4) 宿主自己的 SDK 门：激活并落盘（persist 变体 = agent 自服务链）
        if a.host == "hermes":
            py = find_python(host_home)
            r.rec("S1-05", "找到宿主解释器", bool(py), py or "未找到 hermes python")
            if not py:
                raise SystemExit(2)
            # 走 agent 真正会调的那个工具（agent_toolset 里的 handler），不是测试自造的入口
            (ev / "door.log").write_text(
                sh([py, "-c", (
                    "import json\n"
                    "from aimail.aimail_agent import agent_toolset\n"
                    "tool = next(t for t in agent_toolset() if t['name'] == 'activate_address_code')\n"
                    f"res = tool['handler']({{'code': {code!r}, 'address': {addr!r}, 'gateway_url': {fx.gw!r}}})\n"
                    "print(json.dumps(res, ensure_ascii=False, default=str))\n"
                )], env={"HOME": str(work / "host-home"), "AIMAIL_HOME": str(work / "aimail-home")})[1])
        else:
            node = shutil.which("node") or ""
            r.rec("S1-05", "找到 node", bool(node), node)
            if not node:
                raise SystemExit(2)
            pkg = next(iter(sorted(host_home.rglob("node_modules/@aimail/mail-core/package.json"))), None)
            r.rec("S1-05b", "找到宿主内的 @aimail/mail-core", pkg is not None, str(pkg))
            if pkg is None:
                raise SystemExit(2)
            mc = pkg.parent
            script = ev / "door.cjs"
            script.write_text(
                "const {GatewayClient, activateAddressCodePersist} = require(%r);\n"
                "(async () => {\n"
                "  const c = new GatewayClient(%r, '', 30000);\n"
                "  const res = await activateAddressCodePersist(c, %r, %r, {agentId: %r, gatewayUrl: %r});\n"
                "  console.log(JSON.stringify(res));\n"
                "})().catch(e => { console.error(String(e)); process.exit(1); });\n"
                % (str(mc), fx.gw, code, addr, f"{a.host}s1", fx.gw))
            (ev / "door.log").write_text(
                sh([node, str(script)], env={"HOME": str(work / "host-home"), "AIMAIL_HOME": str(work / "aimail-home")})[1])
        door_log = (ev / "door.log").read_text()
        sid = ""
        api_key = ""
        try:
            _j = json.loads(door_log.strip().splitlines()[-1])
            sid = str(_j.get("system_id") or "")
            api_key = str(_j.get("raw_key") or _j.get("api_key") or "")
        except Exception:
            sid, api_key = "", ""
        if not api_key:
            _b = next(iter(sorted(Path(work / "aimail-home").rglob(BINDING_FILE))), None)
            if _b:
                api_key = str(json.loads(_b.read_text()).get("api_key", ""))
        r.rec("S1-06", "宿主 door(agent_toolset handler) 激活成功", '"raw_key"' in door_log or '"ok": true' in door_log or '"success"' in door_log,
              door_log[-300:])
        (ev / "door.json").write_text(door_log)

        # 5) 服务端断言：agent 作用域 + webhook_url = NULL
        cols = [c["name"] for c in fx.db("PRAGMA table_info(api_keys)")]
        sel = "category, scopes" + (", webhook_url" if "webhook_url" in cols else "")
        rows = fx.db(f"SELECT {sel} FROM api_keys WHERE domain_addr=?", (addr,))
        row = rows[0] if rows else {}
        r.rec("S1-07", "服务端: agent 作用域",
              "agent" in str(row.get("scopes", "")), str(row))
        if "webhook_url" in cols:
            r.rec("S1-08", "服务端: webhook_url 为 NULL", row.get("webhook_url") in (None, ""),
                  str(row.get("webhook_url")))
        else:
            r.rec("S1-08", "服务端: api_keys 无 webhook_url 列(该列在绑定表)", True, str(cols))

        # 6) binding 七字段 + 权限（产品落盘结构）
        bindings = list(Path(work / "aimail-home").rglob(BINDING_FILE)) + \
                   list(host_home.rglob(BINDING_FILE))
        binding = bindings[0] if bindings else None
        if binding:
            data = json.loads(binding.read_text())
            need = ["agent_id", "api_key", "domain", "email", "expires_at", "gateway_url", "system_id"]
            missing = [k for k in need if k not in data]
            mode = oct(binding.stat().st_mode & 0o777)
            r.rec("S1-09", "binding 含文档七字段", not missing, f"missing={missing} path={binding}")
            r.rec("S1-10", "binding 权限 0600", mode in ("0o600", "0o400"), mode)
        else:
            r.rec("S1-09", f"找到 {BINDING_FILE}", False, f"searched {work} and {host_home}")
            r.rec("S1-10", "binding 权限 0600", False, "no binding")

        # ── 7) manager 往返 → 身份名片 + 邮件签名（激活的真正终点）──────────────
        # 契约见 docs/agent-self-setup.md §3：
        #   agent 把 persona/signature/current_time 三条发给安全员；
        #   安全员回 "approve persona" + persona:/signature: 两段 → 网关落库；
        #   agent 收到 "[AIMail] Persona applied" 闭环信；此后外发带签名、入站带 my_profile。
        def _run_door_script(name: str, body_py: str, body_ts: str) -> str:
            if a.host == "hermes":
                f = ev / f"{name}.py"
                f.write_text(body_py)
                return sh([py, str(f)], env={"HOME": str(work / "host-home"),
                                             "AIMAIL_HOME": str(work / "aimail-home")}, log=ev / f"{name}.log")[1]
            f = ev / f"{name}.cjs"
            f.write_text(body_ts)
            return sh([node, str(f)], env={"HOME": str(work / "host-home"),
                                           "AIMAIL_HOME": str(work / "aimail-home")}, log=ev / f"{name}.log")[1]

        persona = f"{a.host} agent: owns inbound triage for its own mailbox; read+reply only."
        signature = f"-- {a.host}-s1 @ aimail scenario-1 gate"
        now_h = time.strftime("%Y-%m-%d %H:%M:%S")
        draft_body = f"persona: {persona}\nsignature: {signature}\ncurrent_time: {now_h}"
        mgr = info["applicant"]

        cfg_literal = json.dumps({"gateway_url": fx.gw, "api_key": api_key, "email": addr,
                                  "system_id": sid, "agent_id": f"{a.host}s1"})
        py_cfg = (
            "import json\n"
            "import aimail.aimail_tools as _T\n"
            f"_CFG = {cfg_literal!r}\n"
            "def _loader():\n"
            "    return json.loads(_CFG)\n"
            # 适配层注入点：直接改 loader 所在模块的全局，避免两套模块身份(aimail_base / aimail.aimail_base)
            "_T._load_profile_config.__globals__['_CONFIG_LOADER'] = _loader\n"
        )
        py_send = py_cfg + (
            "from aimail.aimail_agent import agent_toolset\n"
            "t = {x['name']: x for x in agent_toolset()}\n"
            f"res = t['send_mail']['handler']({{'to': {mgr!r}, 'subject': 'identity card draft', 'body': {draft_body!r}}})\n"
            "print(json.dumps(res, ensure_ascii=False, default=str))\n"
        )
        ts_send = "" if a.host != "dsh" else (
            "const m = require(%r);\n"
            "const t = m.agentTools().find(x => x.name === 'send_mail');\n"
            "t.handler({systemId: %r, email: %r}, {to: %r, subject: 'identity card draft', body: %r})\n"
            "  .then(r => console.log(JSON.stringify(r)))\n"
            "  .catch(e => { console.error(String(e)); process.exit(1); });\n"
            % (str(mc), sid, addr, mgr, draft_body)
        )
        out_send = _run_door_script("draft", py_send if a.host == "hermes" else "", ts_send)
        _flat = out_send.replace(" ", "").lower()
        r.rec("S1-11", "agent 用 send_mail 把 persona/signature 草稿发给安全员",
              '"success":true' in _flat, out_send[-250:])

        # 安全员批准（真实 manager 指令信）
        fx.smtp_send(sender=mgr, to=addr, subject="re: identity card draft",
                     body=f"approve persona\npersona: {persona}\nsignature: {signature}")
        notice = ""
        deadline = time.time() + 30
        while time.time() < deadline and not notice:
            rows = fx.db("SELECT subject, body FROM emails WHERE subject LIKE ? "
                         "ORDER BY rowid DESC LIMIT 1", ("%Persona%",))
            if rows:
                notice = rows[0].get("body") or ""
            else:
                time.sleep(1.0)
        try:
            (ev / "emails-dump.json").write_text(json.dumps(
                fx.db("SELECT id, direction, status, subject, sender, recipients FROM emails "
                      "ORDER BY rowid DESC LIMIT 30"),
                ensure_ascii=False, indent=1))
        except Exception as _e:
            (ev / "emails-dump.json").write_text(f"dump failed: {_e}")
        r.rec("S1-12", "agent 收到 [AIMail] Persona applied 闭环信", bool(notice), notice[:200])
        r.rec("S1-13", "闭环信回显 persona + signature",
              persona[:20] in notice and signature[:20] in notice, notice[:200])

        meta = fx.db("SELECT agent_persona, agent_signature, manager_address FROM domain_addr_meta "
                     "WHERE email_address=?", (addr,))
        meta = meta[0] if meta else {}
        r.rec("S1-14", "服务端已落实身份名片(persona)", persona[:20] in str(meta.get("agent_persona", "")),
              str(meta)[:200])
        r.rec("S1-15", "服务端已落实邮件签名(signature)",
              signature[:20] in str(meta.get("agent_signature", "")), str(meta)[:200])

        # 生效后外发：正文必须自带签名
        py_send2 = py_cfg + (
            "from aimail.aimail_agent import agent_toolset\n"
            "t = {x['name']: x for x in agent_toolset()}\n"
            f"res = t['send_mail']['handler']({{'to': {mgr!r}, 'subject': 'signed outbound probe', 'body': 'hello from %s'}})\n"
            "print(json.dumps(res, ensure_ascii=False, default=str))\n" % a.host
        )
        ts_send2 = "" if a.host != "dsh" else (
            "const m = require(%r);\n"
            "const t = m.agentTools().find(x => x.name === 'send_mail');\n"
            "t.handler({systemId: %r, email: %r}, {to: %r, subject: 'signed outbound probe', body: 'hello from %s'})\n"
            "  .then(r => console.log(JSON.stringify(r)))\n"
            "  .catch(e => { console.error(String(e)); process.exit(1); });\n"
            % (str(mc), sid, addr, mgr, a.host)
        )
        _run_door_script("signed", py_send2 if a.host == "hermes" else "", ts_send2)
        # 网关在 send 路径 2b 把 agent_signature 追加到正文（"\n\n-- \n{sig}"），
        # 直接读网关落库的正文——这是"发出去的信里到底有没有签名"的事实。
        got = ""
        deadline = time.time() + 30
        while time.time() < deadline and not got:
            rows = fx.db("SELECT body FROM emails WHERE subject LIKE ? ORDER BY rowid DESC LIMIT 1",
                         ("%signed outbound probe%",))
            if rows:
                got = rows[0].get("body") or ""
            if not got:
                time.sleep(1.0)
        r.rec("S1-16", "外发邮件正文自动带签名(网关 2b 追加)", signature[:20] in got, got[-250:])

        rc = 0 if r.failed == 0 else 1
    except SystemExit as e:
        rc = int(e.code or 2)
    except Exception as e:  # noqa: BLE001
        r.rec("S1-99", "未预期异常", False, f"{type(e).__name__}: {e}")
        rc = 1
    finally:
        try:
            fx.stop()
        except Exception:
            pass
        (ev / "cases.json").write_text(json.dumps({"meta": meta, "cases": r.cases}, ensure_ascii=False, indent=2))
        print(f"── {a.host}: {len(r.cases)} cases, {r.failed} fail → {'PASS' if rc == 0 else ('CANNOT JUDGE' if rc == 2 else 'FAIL')}")
        print(f"   evidence: {ev}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
