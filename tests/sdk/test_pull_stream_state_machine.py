"""SSE-primary / long-pull fallback state machine (Phase 2, contract §0.3/0.4).

Pins:
1. _SseParser — incremental frame parsing (multi-chunk, multi-line data,
   comment ping, unknown events, bad data, CRLF);
2. decision functions — pure, three-client parity with bridge/ts
   (decide_next / stream backoff 1,2,4,8,16,30 / poll backoff cap 300 /
   parse_pull_mode);
3. mock server — stream delivers mail and acks; 404 ⇒ Poll; stalled
   stream (no ping) ⇒ Poll; AIMAIL_PULL_MODE=poll ⇒ no probing;
4. pull_list carries the timeout_ms hold; empty round sleeps, non-empty
   round does not.

No production socket: a threaded http.server on 127.0.0.1.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
from aimail_tools import _GatewayClient, _SseParser  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def _mail_frame(delivery_id=1, email="a@x", subject="hi"):
    payload = {"batches": [{"body": {"subject": subject},
                            "deliveries": [{"id": delivery_id, "email": email,
                                            "headers": {}}]}]}
    return ("event: mail\ndata: %s\n\n"
            % json.dumps(payload, separators=(",", ":"))).encode("utf-8")


class _State:
    """Per-server mock state (thread-safe enough for single-client tests)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stream_hits = 0
        self.stream_available = True
        self.stream_stall_after_header = False  # headers only, then silence
        self.stream_first_mail = None           # bytes sent on connection 1
        self.pending = []                       # batches served by POST /pending
        self.pending_consumed = 0
        self.ack_calls = []
        self.post_bodies = []                   # raw POST /pending bodies


def _make_server(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence
            pass

        def _send_json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path.rstrip("/") == "/api/v1/admin/pending":
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                with state.lock:
                    state.post_bodies.append(raw)
                    if state.pending_consumed < len(state.pending):
                        batches = state.pending[state.pending_consumed]
                        state.pending_consumed += 1
                    else:
                        batches = []
                self._send_json(200, {"batches": batches})
            elif self.path.rstrip("/") == "/api/v1/admin/pending/ack":
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                ids = list((json.loads(raw or b"{}") or {}).get("ids") or [])
                with state.lock:
                    state.ack_calls.append(ids)
                self._send_json(200, {"acked": len(ids)})
            else:
                self._send_json(404, {"error": "not found"})

        def do_GET(self):
            if self.path.rstrip("/") != "/api/v1/admin/pending/stream":
                self._send_json(404, {"error": "not found"})
                return
            with state.lock:
                state.stream_hits += 1
                first = (state.stream_first_mail is not None
                         and state.stream_hits == 1)
            if not state.stream_available:
                self._send_json(404, {"error": "stream not implemented"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            if state.stream_stall_after_header:
                time.sleep(30)  # silence: the client's read timeout fires
                return
            if first and state.stream_first_mail:
                self.wfile.write(state.stream_first_mail)
                self.wfile.flush()
            time.sleep(3.0)  # hold the (long-lived) connection, then close

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ── 1. SSE frame parser ───────────────────────────────────────────────────

def test_sse_ping_comment_is_a_ping():
    p = _SseParser()
    assert p.feed(b": ping\n\n") == [("ping", None)]


def test_sse_mail_frame_single_chunk():
    p = _SseParser()
    evs = p.feed(_mail_frame())
    assert len(evs) == 1 and evs[0][0] == "mail"
    assert evs[0][1][0]["deliveries"][0]["id"] == 1


def test_sse_frame_split_across_chunks():
    raw = _mail_frame(delivery_id=9)
    p = _SseParser()
    half = len(raw) // 2
    assert p.feed(raw[:half]) == []          # partial frame stays buffered
    evs = p.feed(raw[half:])
    assert evs and evs[0][0] == "mail"
    assert evs[0][1][0]["deliveries"][0]["id"] == 9


def test_sse_multiple_events_one_chunk():
    p = _SseParser()
    evs = p.feed(b": ping\n\nevent: mail\ndata: %s\n\n"
                 % json.dumps({"batches": []}).encode())
    assert [e[0] for e in evs] == ["ping", "mail"]
    assert evs[1][1] == []


def test_sse_multiline_data_joins_with_newline():
    p = _SseParser()
    frame = ("event: mail\ndata: {\"batches\": [\"x\ndata: 1\"]}\n\n")
    # the joined data is invalid JSON ⇒ skipped, but must not kill the stream
    assert p.feed(frame.encode()) == []
    evs = p.feed(_mail_frame(delivery_id=2))
    assert evs and evs[0][0] == "mail"


def test_sse_unknown_event_and_bad_data_are_skipped():
    p = _SseParser()
    assert p.feed(b"event: other\ndata: {\"batches\":[]}\n\n") == []
    assert p.feed(b"event: mail\ndata: not-json\n\n") == []
    evs = p.feed(_mail_frame(delivery_id=3))
    assert evs and evs[0][0] == "mail"


def test_sse_trailing_cr_on_lines_is_stripped():
    """Contract: frames are \n\n-delimited (gateway sends LF); a stray
    trailing CR on a line is tolerated (SSE line-ending leniency)."""
    p = _SseParser()
    payload = json.dumps({"batches": [{"body": {}, "deliveries": [
        {"id": 4, "email": "a@x", "headers": {}}]}]}, separators=(",", ":"))
    frame = ("event: mail\r\ndata: %s\n\n" % payload).encode("utf-8")
    evs = p.feed(frame)
    assert evs and evs[0][0] == "mail"
    assert evs[0][1][0]["deliveries"][0]["id"] == 4


# ── 2. decision functions (three-client parity vectors) ───────────────────

@pytest.mark.parametrize("override,probe,streak,want", [
    ("auto", "ready", 0, ("stream", 0)),
    ("auto", "ready", 3, ("stream", 0)),
    ("auto", "unavailable", 0, ("poll", 0)),
    ("auto", "unavailable", 5, ("poll", 0)),
    ("poll", "ready", 0, ("poll", 0)),      # kill switch beats everything
    ("poll", None, 9, ("poll", 0)),
    ("auto", None, 0, ("stream", 0)),
    ("auto", None, 1, ("stream", 1)),
    ("auto", None, 2, ("stream", 2)),
    ("auto", None, 3, ("poll", 0)),         # 3 consecutive drops ⇒ Poll
    ("auto", None, 4, ("poll", 0)),
    ("stream", None, 1, ("stream", 1)),     # forced: never downgrades
    ("stream", None, 3, ("stream", 4)),
    ("stream", None, 10, ("stream", 30)),   # capped
])
def test_pull_decide_next_mode(override, probe, streak, want):
    assert base.pull_decide_next_mode(override, probe, streak) == want


@pytest.mark.parametrize("streak,want", [
    (0, 0), (1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 30), (7, 30), (9, 30)])
def test_pull_stream_backoff_secs(streak, want):
    assert base.pull_stream_backoff_secs(streak) == want


@pytest.mark.parametrize("interval_s,failures,want", [
    (30, 0, 30), (30, 1, 60), (30, 2, 120), (30, 3, 240),
    (30, 4, 300), (30, 6, 300), (1, 0, 1), (1, 6, 64)])
def test_pull_poll_backoff_secs(interval_s, failures, want):
    assert base.pull_poll_backoff_secs(interval_s, failures) == want


@pytest.mark.parametrize("raw,want", [
    (None, "auto"), ("", "auto"), ("AUTO", "auto"), ("stream", "stream"),
    ("STREAM", "stream"), ("poll", "poll"), ("Poll ", "poll"),
    ("bogus", "auto"), ("streaming", "auto")])
def test_parse_pull_mode(raw, want):
    assert base.parse_pull_mode(raw) == want


def test_resolve_agent_pull_settings_exposes_mode():
    cfg = {"system_id": "shared_addr_x", "api_key": "k", "email": "a@x"}
    assert base.resolve_agent_pull_settings(cfg, env={})["mode"] == "auto"
    assert base.resolve_agent_pull_settings(cfg, env={"AIMAIL_PULL_MODE": "poll"})["mode"] == "poll"
    # master switch off wins: disabled regardless of mode
    s = base.resolve_agent_pull_settings(cfg, env={"AIMAIL_PULL": "0",
                                                   "AIMAIL_PULL_MODE": "stream"})
    assert s["enabled"] is False and s["reason"] == "disabled-by-config"


def test_contract_constants_match_gateway_single_source():
    """Contract values (gateway types.rs is the source; ratchet-pinned)."""
    assert base.PULL_HOLD_MAX_MS == 50_000
    assert base.PULL_HTTP_TIMEOUT_S == base.PULL_HOLD_MAX_MS // 1000 + 15 == 65
    assert base.PULL_STREAM_READ_TIMEOUT_S == 90
    assert base.PULL_STREAM_PING_TIMEOUT_S == 30
    assert base.PULL_REPROBE_INTERVAL_S == 600


# ── 3. mock server: state machine paths ───────────────────────────────────

def _client_for(srv):
    return _GatewayClient("http://127.0.0.1:%d" % srv.server_address[1], "")


def test_stream_primary_delivers_and_acks():
    state = _State()
    state.stream_first_mail = _mail_frame(delivery_id=77)
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        got, acked = [], []
        stop = threading.Event()

        def on_email(mail):
            got.append(mail["id"])

        orig_ack = c.pull_ack

        def spy_ack(ids):
            acked.append(list(ids))
            return orig_ack(ids)

        c.pull_ack = spy_ack
        th = threading.Thread(
            target=lambda: c.start_polling(on_email, interval=0.05,
                                           stop_event=stop), daemon=True)
        th.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not acked:
            time.sleep(0.01)
        stop.set()
        th.join(5)
        assert got == [77], got
        assert acked == [[77]], acked
        assert state.stream_hits >= 1
    finally:
        srv.shutdown()


def test_stream_404_falls_back_to_long_poll():
    state = _State()
    state.stream_available = False
    state.pending = [[{"body": {"subject": "poll"},
                       "deliveries": [{"id": 5, "email": "a@x", "headers": {}}]}]]
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        got = []
        stats = c.start_polling(got.append, interval=0.05, max_rounds=3)
        assert [m["id"] for m in got] == [5], got
        assert stats["acked"] == 1 and stats["pulled"] == 1
        assert state.stream_hits == 1, "one start probe, no per-round probing"
    finally:
        srv.shutdown()


def test_stalled_stream_downgrades_to_poll(monkeypatch):
    import aimail_tools as tools
    monkeypatch.setattr(tools, "PULL_STREAM_PING_TIMEOUT_S", 0.4)
    state = _State()
    state.stream_stall_after_header = True
    state.pending = [[{"body": {}, "deliveries":
                       [{"id": 6, "email": "a@x", "headers": {}}]}]]
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        got = []
        t0 = time.monotonic()
        stats = c.start_polling(got.append, interval=0.05, max_rounds=3)
        elapsed = time.monotonic() - t0
        assert [m["id"] for m in got] == [6], got  # delivered via the poll fallback
        assert stats["acked"] == 1
        assert state.stream_hits == 1    # connected once, then dropped
        assert elapsed < 20              # the 0.4s read timeout fired, not 30s
    finally:
        srv.shutdown()


def test_kill_switch_poll_never_probes():
    state = _State()
    state.pending = [[{"body": {}, "deliveries":
                       [{"id": 8, "email": "a@x", "headers": {}}]}]]
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        got = []
        stats = c.start_polling(got.append, interval=0.05, max_rounds=3,
                                mode="poll")
        assert [m["id"] for m in got] == [8] and stats["acked"] == 1
        assert state.stream_hits == 0, "kill switch: no stream probing"
    finally:
        srv.shutdown()


def test_stream_mode_unavailable_falls_back_to_poll_and_reprobes_at_cadence():
    """mode=stream + 404: one probe, then long-poll rounds deliver while the
    re-probe waits for its 600s cadence (bridge parity — a failed probe is
    NOT retried per round)."""
    state = _State()
    state.stream_available = False
    state.pending = [[{"body": {}, "deliveries":
                       [{"id": 8, "email": "a@x", "headers": {}}]}]]
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        got = []
        stats = c.start_polling(got.append, interval=0.05, max_rounds=3,
                                mode="stream")
        assert [m["id"] for m in got] == [8] and stats["acked"] == 1
        assert state.stream_hits == 1, "start probe only; re-probe at 600s cadence"
    finally:
        srv.shutdown()


def test_stream_mode_transport_drops_never_downgrade():
    """mode=stream + mid-stream transport drops: reconnect with backoff
    forever — the loop never enters the poll branch (kill-switch parity
    with the bridge: forced stream never downgrades)."""
    state = _State()
    state.stream_available = False
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        calls = []

        def fake_round(on_email, seen, stats, stop_event, ping_timeout):
            calls.append(1)
            return "error"

        c._stream_round = fake_round
        stop = threading.Event()
        th = threading.Thread(
            target=lambda: c.start_polling(lambda m: None, interval=0.05,
                                           mode="stream", stop_event=stop),
            daemon=True)
        th.start()
        time.sleep(1.5)  # backoff(1)=1s ⇒ two attempts in the window
        stop.set()
        th.join(5)
        assert len(calls) >= 2, "stream mode keeps reconnecting (1s backoff)"
        assert state.post_bodies == [], "never fell to the poll branch"
    finally:
        srv.shutdown()


def test_auto_mode_three_transport_drops_downgrade_to_poll():
    """auto mode + 3 consecutive transport drops ⇒ Poll (bridge parity)."""
    state = _State()
    state.stream_available = False
    state.pending = [[{"body": {}, "deliveries":
                       [{"id": 9, "email": "a@x", "headers": {}}]}]]
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        calls = []

        def fake_round(on_email, seen, stats, stop_event, ping_timeout):
            calls.append(1)
            return "error"

        c._stream_round = fake_round
        got = []
        t0 = time.monotonic()
        stats = c.start_polling(got.append, interval=0.05, max_rounds=3,
                                mode="auto")
        elapsed = time.monotonic() - t0
        assert calls == [1, 1, 1], "exactly 3 drop attempts before downgrade"
        assert [m["id"] for m in got] == [9] and stats["acked"] == 1  # poll branch delivered
        assert elapsed < 10  # backoff 1s + 2s, not 600s
    finally:
        srv.shutdown()


# ── 4. long-pull contract details ──────────────────────────────────────────

def test_pull_list_body_carries_timeout_ms():
    state = _State()
    state.stream_available = False
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        stop = threading.Event()
        th = threading.Thread(
            target=lambda: c.start_polling(lambda m: None, interval=0.05,
                                           mode="poll", stop_event=stop),
            daemon=True)
        th.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not state.post_bodies:
            time.sleep(0.01)
        stop.set()
        th.join(5)
        assert state.post_bodies, "no POST /pending seen"
        body = json.loads(state.post_bodies[0])
        assert body["timeout_ms"] == base.PULL_HOLD_MAX_MS == 50_000
    finally:
        srv.shutdown()


def test_empty_round_sleeps_non_empty_round_does_not():
    state = _State()
    state.stream_available = False
    state.pending = [
        [{"body": {}, "deliveries": [{"id": 1, "email": "a@x", "headers": {}}]}],
        [{"body": {}, "deliveries": [{"id": 2, "email": "a@x", "headers": {}}]}],
    ]  # two back-to-back non-empty rounds
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        stamps = []

        def on_email(mail):
            stamps.append(time.monotonic())

        stats = c.start_polling(on_email, interval=5.0, max_rounds=2,
                                mode="poll")
        assert stats["acked"] == 2, stats
        # Non-empty rounds do NOT sleep the 5s interval between them.
        assert stamps[1] - stamps[0] < 2.0, stamps
    finally:
        srv.shutdown()


def test_empty_round_paces_the_interval():
    state = _State()
    state.stream_available = False
    state.pending = []  # always empty
    srv = _make_server(state)
    try:
        c = _client_for(srv)
        t0 = time.monotonic()
        stats = c.start_polling(lambda m: None, interval=0.5, max_rounds=2,
                                mode="poll")
        elapsed = time.monotonic() - t0
        assert stats == {"pulled": 0, "acked": 0, "errors": 0}
        assert elapsed >= 0.8, "two empty rounds must pace ~0.5s each"
    finally:
        srv.shutdown()
