#!/usr/bin/env python3
"""Read the latest Mailpit message for a recipient and extract a code.

Usage: mailpit_code.py --url http://127.0.0.1:8025 --to user@test.local \
                         [--kind verify|activation] [--timeout 60]
Prints the code (6 digits, sys-..., or addr-...).
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://127.0.0.1:8025')
p.add_argument('--to', required=True)
p.add_argument('--kind', choices=['verify', 'activation'], default='verify')
p.add_argument('--timeout', type=int, default=60)
p.add_argument('--contains', default='')
a = p.parse_args()

def get(path):
    with urllib.request.urlopen(a.url.rstrip('/') + path, timeout=10) as r:
        return json.loads(r.read().decode())

def message_text(mid):
    msg = get('/api/v1/message/' + urllib.parse.quote(str(mid), safe=''))
    return '\n'.join(str(msg.get(k, '')) for k in ('Text', 'HTML', 'Subject'))

deadline = time.time() + a.timeout
seen = set()
while time.time() < deadline:
    try:
        data = get('/api/v1/messages?limit=100')
        for m in data.get('messages', []):
            tos = [str(x.get('Address', '')).lower() for x in (m.get('To') or [])]
            if a.to.lower() not in tos:
                continue
            mid = m.get('ID')
            if mid in seen:
                continue
            seen.add(mid)
            text = message_text(mid)
            if a.contains and a.contains.lower() not in text.lower():
                continue
            if a.kind == 'verify':
                hit = re.search(r'\b(\d{6})\b', text)
                if hit:
                    print(hit.group(1)); sys.exit(0)
            else:
                hit = re.search(r'\b((?:sys|addr)-[A-Za-z0-9-]+)\b', text)
                if hit:
                    print(hit.group(1)); sys.exit(0)
    except Exception:
        pass
    time.sleep(1)
print(f'no {a.kind} code for {a.to} in Mailpit within {a.timeout}s', file=sys.stderr)
sys.exit(1)
