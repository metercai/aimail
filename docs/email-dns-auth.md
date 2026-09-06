# Email Domain Authentication DNS (SPF / DKIM / DMARC / rDNS)

> Applies to: self-hosted mail domains, AIMail independent-domain systems,
> and any SMTP-direct mail server. Receivers (QQ Mail, Gmail, Outlook, …)
> decide to trust mail from an unfamiliar server based on these four DNS
> record groups. Missing one, and mail may land in spam — or be rejected.
>
> 🇨🇳 [中文版](./email-dns-auth_zh.md)

## 0. Overview

| Record | Where | Purpose | Required |
|---|---|---|---|
| SPF | sender-domain TXT | declares which servers may send as this domain | strongly recommended |
| DKIM | `{selector}._domainkey` TXT | publishes the signing public key; receivers verify the mail is unmodified | recommended (essential for direct sending) |
| DMARC | `_dmarc` TXT | tells receivers what to do when SPF/DKIM both fail, and collects reports | recommended |
| rDNS / PTR | reverse zone of the server IP | makes the IP resolve back to a name | receiver-dependent |

## 1. SPF (sender authorization)

**Where**: on the sending domain itself (subdomain sends → subdomain; root
sends → root). Most panels: host `@` or empty (the domain itself).

**Value** (common examples):
```
v=spf1 mx ~all                    # the domain's MX servers may send (softfail others)
v=spf1 ip4:203.0.113.10 -all      # only this IP may send (-all = reject everything else)
```

**Fields**:
| Part | Meaning |
|---|---|
| `v=spf1` | version marker, fixed start |
| `mx` | the servers in this domain's MX may send |
| `ip4:203.0.113.10` | this IPv4 may send (repeatable; `ip6:` likewise) |
| `include:_spf.example.com` | include another domain's policy (e.g. provider/ESP) |
| `~all` | senders not listed: softfail (accept, but flag) |
| `-all` | senders not listed: fail (receiver should reject; use `~all` first, tighten later) |

Note: SPF allows at most 10 DNS lookups — keep `include` chains short.
Verify: `dig +short TXT your.domain` shows `v=spf1 …`.

## 2. DKIM (signing public key)

DKIM is three parts: the **private key signs** (server side), the **public
key is published** (DNS), and the **selector names it**. The receiving
server looks up `d=domain` + `s=selector` from the mail header and verifies
the signature with the public key.

**Key generation & public-key export** (run on the server):
```bash
openssl genrsa -out /path/dkim/{domain}.pem 2048    # private key stays on the server
chmod 600 /path/dkim/{domain}.pem
openssl rsa -in /path/dkim/{domain}.pem -pubout -outform DER | openssl base64 -A   # public key blob
```

**Where**: host `{selector}._domainkey` (selector comes from server config,
e.g. `aimail` → `aimail._domainkey`).

**Value**:
```
v=DKIM1; k=rsa; p=<public-key blob from the command above>
```

**Fields**:
| Part | Meaning |
|---|---|
| `v=DKIM1` | version, fixed |
| `k=rsa` | algorithm (optional, defaults to rsa) |
| `h=sha256` | signature hash (some providers add it; optional) |
| `p=…` | **the public key itself** (one continuous base64 blob — no line breaks, no truncation) |

Verify: `dig +short TXT {selector}._domainkey.{domain}` returns a record
starting with `v=DKIM1` whose `p=` matches the server key.

## 3. DMARC (receiver policy & reports)

**Where**: host `_dmarc` (fixed).

**Value** (start monitoring, tighten once stable):
```
v=DMARC1; p=none; rua=mailto:admin@example.com
v=DMARC1; p=quarantine; rua=mailto:admin@example.com; pct=100
```

**Fields**:
| Part | Meaning |
|---|---|
| `v=DMARC1` | version, fixed |
| `p=none` | monitor only (no rejection; recommended while observing reports) |
| `p=quarantine` | SPF and DKIM both fail → spam folder |
| `p=reject` | both fail → reject (strictest; only after confirming no misconfig) |
| `rua=mailto:…` | aggregate report address (multiple allowed, comma-separated) |
| `pct=100` | percentage of mail the policy applies to (start small, e.g. 10) |
| `sp=` | optional separate policy for subdomains |

Note: DMARC passes only when SPF **or** DKIM passes **and** is domain-
aligned — DMARC is the judge of the two; without them it is meaningless.
Verify: `dig +short TXT _dmarc.{domain}`.

## 4. rDNS / PTR (server IP reverse)

**Where**: reverse DNS of the server's public IP (cloud provider / DC
"reverse/PTR" panel — not at the domain registrar).

**Set**: make the IP resolve back to a sending name (e.g. `mail.example.com`)
and ensure that name has an A record pointing to the IP. Keep the SMTP
banner / EHLO name consistent.

**Why**: most receivers do not hard-fail it, but Gmail/Outlook etc. trust
less when an IP has no PTR or one unrelated to the sending domain. Verify:
```bash
dig +short -x <server IP>        # should return your sending name
```

## 5. Post-setup self-check

```bash
# SPF
dig +short TXT example.com
# DKIM
dig +short TXT aimail._domainkey.example.com
# DMARC
dig +short TXT _dmarc.example.com
# rDNS
dig +short -x 203.0.113.10
# Real-send test: mail a Gmail/QQ address and inspect the headers:
#   Authentication-Results: … spf=pass … dkim=pass … dmarc=pass
```

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Mail in spam | one of SPF/DKIM/DMARC missing; no PTR; content triggers |
| Header shows `spf=fail` | sending server not in the SPF list (add `ip4:`/`mx`) |
| Header shows `dkim=fail` | `p=` mismatch with the server private key / selector mismatch / record truncated |
| Header shows `dmarc=fail` | SPF and DKIM both failed or not domain-aligned |
