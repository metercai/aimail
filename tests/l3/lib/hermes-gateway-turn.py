#!/usr/bin/env python3
"""hermes-gateway-turn.py — deliver ONE prompt to hermes through its gateway session.

Why this exists (owner ruling 2026-11-27, option A): hermes' `-z` one-shot only accepts a
short literal prompt; our 795-byte activation prompt produced no output at all (measured at
240/420/900s), while a short prompt answered instantly. So the prompt is delivered the way
the product's own TUI delivers it — as a session message over the gateway WebSocket:

    hermes gateway attach --json --start   -> {url, ticket, protocols}
    dial url with those subprotocols       -> JSON-RPC ("runtime.describe", "session.create",
                                              "prompt.submit")

It reuses hermes' own client class instead of re-implementing the framing.

Usage (inside the container, as the hermes user):
    python hermes-gateway-turn.py <prompt-file> [timeout-seconds]
Exit 0 = a turn was admitted and closed; the transcript is printed to stdout.
"""
import asyncio
import json
import os
import subprocess
import sys
import uuid

HERMES = os.environ.get("HERMES_BIN", "/opt/hermes/bin/hermes")


def attach():
    """Ask the host for a dialable endpoint + a single-use ticket (starts it if needed)."""
    out = subprocess.run([HERMES, "gateway", "attach", "--json", "--start"],
                         capture_output=True, text=True, timeout=180)
    lines = [ln for ln in out.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        raise SystemExit(f"attach produced no json (rc={out.returncode}): "
                         f"{out.stdout[-500:]}{out.stderr[-500:]}")
    return json.loads(lines[-1])


def _text_of(payload):
    """The assistant text may arrive as str, list-of-parts, or dict with content/text."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "content", "delta", "message"):
            if key in payload:
                return _text_of(payload[key])
        return ""
    if isinstance(payload, list):
        return "".join(_text_of(p) for p in payload)
    return ""


async def main():
    prompt_file = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 600.0
    text = open(prompt_file, encoding="utf-8").read()

    info = attach()
    print(f"gateway: {info.get('url')} state={info.get('state')}", flush=True)

    import websockets
    from hermes_cli.gateway_client import GatewayClient

    async with websockets.connect(info["url"], subprotocols=info["protocols"],
                                  max_size=None, ping_interval=20) as ws:
        client = GatewayClient(ws)
        async with client:
            desc = await client.rpc("runtime.describe")
            create_params = ((desc or {}).get("session_create") or {}).get("parameters") or []
            print(f"session_create params: {create_params}", flush=True)

            # every accepted parameter with an empty/neutral value: we only need a session
            request = {}
            for name in create_params:
                request[name] = None
            try:
                created = await client.rpc("session.create", request_id=uuid.uuid4().hex, **request)
            except Exception as exc:  # noqa: BLE001 - surface the host's own refusal
                print(f"session.create with blanks failed ({exc}); retrying bare", flush=True)
                created = await client.rpc("session.create", request_id=uuid.uuid4().hex)
            sid = (created or {}).get("session_id") or (created or {}).get("id")
            if not sid:
                raise SystemExit(f"session.create gave no session id: {created}")
            print(f"session={sid}", flush=True)

            receipt = await client.rpc("prompt.submit", session_id=sid,
                                       input_id=uuid.uuid4().hex, text=text,
                                       finite=True, unattended=True)
            print(f"admitted: {json.dumps(receipt)[:300]}", flush=True)

            # drain events until the turn settles (or the deadline passes)
            transcript = []
            dump = open(os.environ.get("HG_DUMP", "/tmp/hermes-events.jsonl"), "a", encoding="utf-8")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                try:
                    frame = await asyncio.wait_for(client.events.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue
                except AttributeError:
                    print("client has no event queue; receipts only", flush=True)
                    break
                # GatewayClient hands us frames wrapped once: {"type": "event", ...event}.
                ev = frame if isinstance(frame, dict) else {}
                # JSON-RPC notification: {"jsonrpc":"2.0","method":"event","params":{...}}
                if ev.get("method") == "event" and isinstance(ev.get("params"), dict):
                    ev = ev["params"]
                elif ev.get("type") in ("event", "notification") and isinstance(ev.get("payload"), dict):
                    ev = ev["payload"]
                dump.write(json.dumps(ev)[:20000] + "\n")
                dump.flush()
                kind = ev.get("type") or ev.get("method") or ""
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev
                chunk = _text_of(payload) or _text_of(ev)
                if chunk:
                    transcript.append(chunk)
                print(f"[{kind}] {json.dumps(payload)[:400]}" if not chunk else f"[{kind}] {chunk[:400]}", flush=True)
                if kind in ("message.complete", "turn.settled", "prompt.settled", "turn.end", "session.idle"):
                    break
            print("--- transcript ---", flush=True)
            print("".join(transcript)[-4000:], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
