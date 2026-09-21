"""dsh 检查适配器 vs 0.1.14 插件的 session_id 语义(2026-09-21 修正)。

背景: dsh 插件按"每封入站一个一次性 session"工作 —— 入站时
`updateAgentConfig({session_id})` 临时绑定,一轮跑完 `whenIdle()` 里解绑。
所以绑定文件里的 `session_id` 是**瞬态**的,闲时必然缺失。

此前 `_dsh_list_agents` 把 `session_id` 当发现条件、L3 `session` 检查也要求它,
于是一个**正常绑定**的 dsh agent 在闲时被报成
`✗ discovery: no agents found` + `✗ session: session_id=(缺)`
—— 假红会让人以为要重装。判据应只认 email(+api_key)/preset。
"""
import json

import check_status as cs


def _binding(tmp_path, **fields):
    d = tmp_path / "sys1" / "agent.tang_aimail.token.tm"
    d.mkdir(parents=True, exist_ok=True)
    aj = d / "agentmail.json"
    payload = {"email": "agent.tang@aimail.token.tm", "api_key": "k", "preset": "mail"}
    payload.update(fields)
    aj.write_text(json.dumps(payload))
    return aj


def test_dsh_binding_without_session_id_is_still_discovered(tmp_path, monkeypatch):
    _binding(tmp_path)
    monkeypatch.setattr(cs, "SYSTEMS_DIR", tmp_path)
    monkeypatch.setattr(cs, "_resolve_system_id", lambda: "sys1")

    agents = cs._dsh_list_agents()

    assert len(agents) == 1, "无 session_id 的正常绑定必须仍被发现"
    assert agents[0]["email"] == "agent.tang@aimail.token.tm"
    assert agents[0]["session_id"] == ""


def test_dsh_binding_with_session_id_still_reports_it(tmp_path, monkeypatch):
    _binding(tmp_path, session_id="db98eacdb07542729bb3103fa73d1e0e")
    monkeypatch.setattr(cs, "SYSTEMS_DIR", tmp_path)
    monkeypatch.setattr(cs, "_resolve_system_id", lambda: "sys1")

    agents = cs._dsh_list_agents()

    assert len(agents) == 1 and agents[0]["session_id"].startswith("db98eacd")


def test_dsh_session_check_accepts_transient_session_id(tmp_path):
    aj = _binding(tmp_path)

    c = cs.Check()
    cs._dsh_check_config(c, {
        "name": "agent", "email": "agent.tang@aimail.token.tm",
        "session_id": "", "config": aj,
    })
    rec = {r["check"]: r for r in c.checks}

    assert rec["session"]["pass"] is True, "preset 在即可; 闲时缺 session_id 不是缺陷"
    assert rec["name_apikey"]["pass"] is True
    assert rec["webhook"]["pass"] is False  # 缺 webhook_url/secret 仍要报


def test_dsh_session_check_fails_without_preset(tmp_path):
    aj = _binding(tmp_path, preset="")

    c = cs.Check()
    cs._dsh_check_config(c, {
        "name": "agent", "email": "agent.tang@aimail.token.tm",
        "session_id": "", "config": aj,
    })
    rec = {r["check"]: r for r in c.checks}

    assert rec["session"]["pass"] is False, "preset 缺失是真缺陷(绑定没落全)"
