"""SSE read framing: a small frame on a long-lived chunked stream must be
delivered immediately, not accumulated.

Regression for the A1 L2-10 hang (py host never delivered while ts did):

``_stream_round`` used ``resp.read(4096)``. CPython's chunked branch
(``http.client._read_chunked``) keeps reading across chunk boundaries until
``amt`` bytes are collected, so a frame smaller than 4096 (every real SSE
frame) is never returned while the stream stays open. The socket timeout does
not save it either: each arriving chunk resets it, and the gateway's 15s
heartbeat keeps resetting it forever.

The server here sends ONE mail frame and then stays silent without closing —
exactly the real stream shape. Delivery must happen within ~1.5s.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

from aimail_tools import _GatewayClient  # noqa: E402


def _mail_frame(delivery_id=42, email="a@x"):
    payload = {"batches": [{"body": {"subject": "frame"},
                            "deliveries": [{"id": delivery_id, "email": email,
                                            "headers": {}}]}]}
    return ("event: mail\ndata: %s\n\n"
            % json.dumps(payload, separators=(",", ":"))).encode("utf-8")


class _ChunkedStreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hold_s = 3.0

    def log_message(self, format, *args):  # keep pytest output clean
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        frame = _mail_frame()
        # One chunk, then silence (connection stays open) — the real shape.
        self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
        self.wfile.flush()
        time.sleep(self.hold_s)
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass

    def do_POST(self):  # pull_ack (delivery already counted before ack)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps({"success": True, "acked": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_small_frame_on_long_lived_chunked_stream_is_delivered_promptly():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _ChunkedStreamHandler)
    th_srv = threading.Thread(target=srv.serve_forever, daemon=True)
    th_srv.start()
    try:
        client = _GatewayClient("http://127.0.0.1:%d" % srv.server_address[1], "")
        got, stats = [], {"pulled": 0, "acked": 0, "errors": 0}
        stop = threading.Event()
        done = threading.Event()

        def on_email(mail):
            got.append(mail["id"])
            done.set()

        th = threading.Thread(
            target=lambda: client._stream_round(on_email, set(), stats, stop, 30),
            daemon=True)
        th.start()
        assert done.wait(1.5), (
            "frame not delivered within 1.5s — a small SSE frame on an open "
            "chunked stream is being accumulated instead of returned "
            f"(got={got}, stats={stats})")
        assert got == [42], got
    finally:
        srv.shutdown()
