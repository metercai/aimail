#!/usr/bin/env python3
"""Minimal plain-SMTP sink for the self-hosted L2 gateway (no external mail service)."""
import argparse, asyncio, pathlib

async def handle(reader, writer, spool):
    def send(line): writer.write((line + "\r\n").encode())
    send("220 local-smtp ESMTP")
    await writer.drain()
    mail_from = None
    rcpts = []
    try:
        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            up = line.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                send("250-local-smtp"); send("250 8BITMIME")
            elif up.startswith("MAIL FROM:"):
                mail_from = line[10:].strip(); rcpts = []; send("250 OK")
            elif up.startswith("RCPT TO:"):
                rcpts.append(line[8:].strip()); send("250 OK")
            elif up == "DATA":
                send("354 End data with <CR><LF>.<CR><LF>")
                chunks = []
                while True:
                    d = await reader.readline()
                    if not d or d in (b".\r\n", b".\n"):
                        break
                    chunks.append(d)
                body = b"".join(chunks)
                rec = (f"From: {mail_from}\r\nTo: {', '.join(rcpts)}\r\n").encode() + body
                with open(spool, "ab") as f:
                    f.write(rec + b"\r\n---AIMAIL-MESSAGE---\r\n")
                send("250 queued")
            elif up == "QUIT":
                send("221 bye"); break
            elif up == "RSET":
                mail_from = None; rcpts = []; send("250 OK")
            else:
                send("250 OK")
            await writer.drain()
    finally:
        writer.close()

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--spool", required=True)
    a = ap.parse_args()
    pathlib.Path(a.spool).write_bytes(b"")
    server = await asyncio.start_server(lambda r, w: handle(r, w, a.spool), "127.0.0.1", a.port)
    print(f"SMTP_SINK_READY {a.port}", flush=True)
    async with server:
        await server.serve_forever()

asyncio.run(main())
