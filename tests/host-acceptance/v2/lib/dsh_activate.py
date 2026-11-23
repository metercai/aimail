import json
import sys
import threading
import uuid
import urllib.request
import http.client
import http.cookiejar


def _prompts(text):
    """A path means: sequential mode (one prompt per line, same session)."""
    import os
    if os.path.isfile(text):
        with open(text, encoding="utf-8") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    return [text]


def main() -> int:
    url, cwd, text = sys.argv[1], sys.argv[2], sys.argv[3]
    prompts = _prompts(text)
    gap = float(__import__("os").environ.get("DSH_TURN_GAP_SECS", "60"))
    base = url.split("?")[0].rstrip("/")
    host = base.split("//", 1)[1]
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(url, timeout=20).read()
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)

    def rpc(method, args, timeout=60):
        msg = {"type": "client-request", "rpcId": str(uuid.uuid4()), "method": method,
               "payload": {"args": args}}
        req = urllib.request.Request(f"{base}/api/{method}", data=json.dumps(msg).encode(),
                                     headers={"Content-Type": "application/json", "Cookie": cookie},
                                     method="POST")
        with op.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")

    created = rpc("session/create", {"request": {"cwd": cwd}})
    sid = (created.get("result") or {}).get("value", {}).get("sessionId")
    if not sid:
        print(json.dumps({"ok": False, "create": created})[:400])
        return 2
    rpc("session/initializeDefaultModel", {})

    # keep a state stream open (the UI does this); the agent may only consume while subscribed
    def stream_state():
        try:
            msg = {"type": "client-request", "rpcId": str(uuid.uuid4()),
                   "method": "session/observeState",
                   "payload": {"args": {"request": {"sessionId": sid}}}}
            conn = http.client.HTTPConnection(host, timeout=40)
            conn.request("POST", "/api/session/observeState", body=json.dumps(msg),
                         headers={"Content-Type": "application/json", "Cookie": cookie})
            resp = conn.getresponse()
            while resp.read(4096):
                pass
        except Exception:
            pass

    threading.Thread(target=stream_state, daemon=True).start()

    def stream_control():
        try:
            msg = {"type": "client-request", "rpcId": str(uuid.uuid4()),
                   "method": "session/control", "payload": {"args": {}}}
            conn = http.client.HTTPConnection(host, timeout=60)
            conn.request("POST", "/api/session/control", body=json.dumps(msg),
                         headers={"Content-Type": "application/json", "Cookie": cookie})
            resp = conn.getresponse()
            while resp.read(4096):
                pass
        except Exception:
            pass

    threading.Thread(target=stream_control, daemon=True).start()
    # Drive the agent: session/follow with activate=true over /api/remote.mux.
    # (SessionFollowRequest.activate — "Request background activation; omission leaves the read passive")
    try:
        sys.path.insert(0, "/opt/regression")
        import ws_mux as m
        import threading as _th
        _s, _rest = m.handshake(host.split(":")[-1], cookie)
        m.send_text(_s, json.dumps({"type": "open", "streamId": "act1",
                                    "endpoint": "session/follow",
                                    "payload": {"args": {"request": {
                                        "address": {"kind": "session", "sessionId": sid},
                                        "activate": True}}}}))
        _th.Thread(target=lambda: m.read_frames(_s, __import__("time").time() + float(__import__("os").environ.get("DSH_READ_SECS", "60"))), daemon=True).start()
        __import__("time").sleep(5)  # let the opening snapshot land before prompting
        print("mux: follow(activate) sent")
    except Exception as e:
        print("mux: failed", e)

    sel = rpc("session/selectModel", {"request": {"sessionId": sid, "provider": "xiaomi", "model": "mimo-v2.6-flash"}})
    print("selectModel:", json.dumps(sel)[:200])
    cat = rpc("session/modelCatalog", {})
    print("catalog:", json.dumps(cat)[:500])
    for _i, _text in enumerate(prompts):
        prompted = rpc("session/prompt", {"request": {"requestId": str(uuid.uuid4()), "sessionId": sid,
                                                      "mode": "queue",
                                                      "content": [{"type": "text", "text": _text}]}})
        print(json.dumps({"ok": True, "turn": _i, "sessionId": sid,
                          "prompt": prompted.get("result")})[:400], flush=True)
        if _i < len(prompts) - 1:
            __import__("time").sleep(gap)   # let the turn (and its tool call) finish
        # snapshot the binding state after every turn (fixture evidence, not product code)
        try:
            import glob as _g, json as _j, os as _o
            _home = _o.environ.get("AIMAIL_HOME", "/root/.aimail")
            _paths = sorted(_g.glob(_home + "/systems/*/*/agentmail.json"))
            _state = {"turn": _i, "bindings": len(_paths),
                      "paused": (_j.load(open(_paths[0])).get("paused") if _paths else None)}
            _outdir = _o.environ.get("DSH_STATE_DIR", "")
            if _outdir:
                _j.dump(_state, open(f"{_outdir}/state_{_i}.json", "w"))
            print("state:", _j.dumps(_state), flush=True)
        except Exception as _e:
            print("state: error", _e, flush=True)
    # keep the mux reader alive so tool/turn frames are observed (QA: long-window capture)
    import time as _t, os as _os
    for _ in range(int(_os.environ.get("DSH_KEEPALIVE_TICKS", "2"))):
        _t.sleep(10)
        print("tick", int(_t.time()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
