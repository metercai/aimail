#!/usr/bin/env python3
"""ws_mux.py — minimal WS client for dsh /api/remote.mux (stream channel).

argv: <port> <cookie> <sessionId> [seconds]
Opens the mux, calls session/follow as a stream, prints frame types it receives.
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import uuid


def handshake(port, cookie):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET /api/remote.mux HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
        f"Cookie: {cookie}\r\n\r\n"
    )
    s = socket.create_connection(("127.0.0.1", int(port)), timeout=10)
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    head, rest = buf.split(b"\r\n\r\n", 1)
    if b"101" not in head.split(b"\r\n")[0]:
        raise RuntimeError(head.split(b"\r\n")[0].decode())
    return s, rest


def send_text(s, text):
    data = text.encode()
    hdr = bytearray([0x81])
    n = len(data)
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126)
        hdr += struct.pack(">H", n)
    else:
        hdr.append(0x80 | 127)
        hdr += struct.pack(">Q", n)
    mask = os.urandom(4)
    hdr += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    s.sendall(bytes(hdr) + masked)


def read_frames(s, deadline):
    buf = b""
    while time.time() < deadline:
        s.settimeout(max(0.5, deadline - time.time()))
        try:
            chunk = s.recv(65536)
        except Exception:
            return
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 2:
            b1, b2 = buf[0], buf[1]
            ln = b2 & 0x7F
            off = 2
            if ln == 126:
                if len(buf) < 4:
                    break
                ln = struct.unpack(">H", buf[2:4])[0]
                off = 4
            elif ln == 127:
                if len(buf) < 10:
                    break
                ln = struct.unpack(">Q", buf[2:10])[0]
                off = 10
            if len(buf) < off + ln:
                break
            payload = buf[off:off + ln]
            buf = buf[off + ln:]
            op = b1 & 0x0F
            if op == 8:
                return
            if op in (1, 2):
                try:
                    txt = payload[:600].decode(errors="replace")
                    try:
                        o = json.loads(txt); v = o.get("value") or {}; ev = v.get("event") or {}
                        et = ev.get("type") or v.get("type")
                        extra = ""
                        if et == "request/header":
                            extra = json.dumps((ev.get("data") or {}).get("header", {}).get("config"))[:140]
                        if et == "turn/end":
                            extra = json.dumps(ev.get("data"))[:200]
                        print(f"frame: {o.get('type')} {et} {extra}", flush=True)
                    except Exception:
                        print("frame:", txt[:200], flush=True)
                except Exception:
                    pass


def main():
    port, cookie, sid = sys.argv[1], sys.argv[2], sys.argv[3]
    secs = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0
    s, rest = handshake(port, cookie)
    print("mux: open")
    msg = {"type": "open", "streamId": "s1", "endpoint": "session/follow",
           "payload": {"args": {"request": {"address": {"kind": "session", "sessionId": sid}, "activate": True}}}}
    send_text(s, json.dumps(msg))
    print("mux: follow sent")
    read_frames(s, time.time() + secs)
    s.close()


if __name__ == "__main__":
    main()
