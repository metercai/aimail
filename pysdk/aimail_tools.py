"""aimail_tools — Mail toolset: send_mail, contacts, email_summary."""
from __future__ import annotations
import json
import sqlite3
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, List, Any, Union
from datetime import datetime
import urllib.request
import urllib.error

from aimail_base import _load_profile_config, list_personas
import urllib.parse


logger = logging.getLogger(__name__)
_TOOLSET = "agentmail"


class _GatewayClient:
    def __init__(self, gateway_url: str, api_key: str, timeout: int = 30,
                 identity: str = ""):
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # v1 signature identity: the key's email (address-scoped) or its
        # system_id (system-level). Sent as X-Api-Identity; the raw key stays
        # offline and only the derived signature crosses the wire.
        self.identity = identity

    def _signed_headers(self, method: str, path: str,
                        data: Optional[bytes]) -> Dict[str, str]:
        """Return the v1 signature headers for a request (empty if no key)."""
        from aimail_base import compute_api_signature
        h: Dict[str, str] = {}
        if not self.api_key:
            return h
        if self.identity:
            h["X-Api-Identity"] = self.identity
        sig = compute_api_signature(self.api_key, method, path,
                                    data or b"")
        if sig:
            h.update(sig)
        return h

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        raw_body: Optional[bytes] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """"Make an HTTP request to the gateway API. Returns parsed JSON or error dict."""
        url = f"{self.gateway_url}{path}"
        req_headers = {"Accept": "application/json"}

        data = None
        if raw_body is not None:
            data = raw_body
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        # v1 API signature (raw key stays offline).
        req_headers.update(self._signed_headers(method, path, data))
        if headers:
            req_headers.update(headers)

        try:
            req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_body = resp.read().decode("utf-8")
                status = resp.status
                try:
                    parsed = json.loads(resp_body)
                    # Handle JSON arrays -- wrap into {"data": [...]}
                    if isinstance(parsed, list):
                        return {"status": status, "data": parsed}
                    # Don't let response body overwrite HTTP status
                    parsed.pop("status", None)
                    return {"status": status, **parsed}
                except json.JSONDecodeError:
                    return {"status": status, "body": resp_body}
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                err_body.pop("status", None)
            except Exception:
                err_body = {"error": str(e)}
            return {"status": e.code, "error": str(e), **err_body}
        except Exception as e:
            return {"status": 0, "error": str(e)}

    # ── Send API ────────────────────────────────────────────────

    def send_mail(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        attachments: Optional[List[dict]] = None,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
        sender: Optional[str] = None,
        message_id: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """POST /api/v1/send"""
        payload: Dict[str, Any] = {
            "to": to,
            "markdown": body,
        }
        if sender:
            payload["sender"] = sender
        if subject:
            payload["subject"] = subject
        if cc:
            payload["cc"] = cc  # list[str] — gateway SendEmailRequest.cc: Option<Vec<String>>
        if attachments:
            payload["attachments"] = attachments

        hdrs: Dict[str, str] = {}
        if message_id:
            hdrs["Message-ID"] = message_id
        if in_reply_to:
            hdrs["In-Reply-To"] = in_reply_to
        if references:
            hdrs["References"] = references
        if headers:
            # Caller-supplied headers (e.g. X-AIMail-Agent) merged in.
            hdrs.update(headers)
        if hdrs:
            payload["headers"] = hdrs
        return self._request("POST", "/api/v1/send", body=payload)

    # ── Attachment API ──────────────────────────────────────────

    def upload_attachment(self, file_path: str) -> dict:
        """POST /api/v1/upload -- upload a file as an attachment."""
        path = Path(file_path)
        if not path.is_file():
            return {"status": 400, "error": f"File not found: {file_path}"}
        content = path.read_bytes()
        # Use multipart-like approach via raw bytes with content-type header
        boundary = "----HermesBoundary"
        body = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        return self._request(
            "POST",
            "/api/v1/upload",
            raw_body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def download_attachment(self, attachment_id: str) -> Optional[bytes]:
        """GET /api/v1/attachments/{id} -- download attachment bytes."""
        path = f"/api/v1/attachments/{attachment_id}"
        url = f"{self.gateway_url}{path}"
        req_headers = dict(self._signed_headers("GET", path, None))
        req = urllib.request.Request(
            url,
            headers=req_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as e:
            logger.error("download_attachment(%s) failed: %s", attachment_id, e)
            return None




    def check_whitelist_value(self, domain_addr: str, value: str, direction: str = "to") -> dict:
        """GET /api/v1/whitelists/check — check if a value is whitelisted.

        Returns {"in_contacts": True/False, "direction": "..."} — no info leakage
        beyond the single queried address.
        """
        result = self._request(
            "GET",
            f"/api/v1/whitelists/check?domain_addr={domain_addr}&value={value}&direction={direction}",
        )
        whitelisted = result.get("status") == 200 and result.get("whitelisted", False)
        entry_direction = result.get("direction", direction) if whitelisted else direction
        return {"in_contacts": whitelisted, "direction": entry_direction}

    def update_whitelist_by_value(self, domain_addr: str, value: str, direction: str) -> dict:
        """PUT /api/v1/whitelists?domain_addr=&value= — update direction by composite key.

        Unlike update_whitelist_entry which requires a DB entry_id, this uses
        the same composite-key lookup as delete_whitelist_by_value — no
        information leakage from listing all entries.
        """
        return self._request("PUT",
            f"/api/v1/whitelists?domain_addr={domain_addr}&value={value}",
            body={"direction": direction})

    def delete_whitelist_by_value(self, domain_addr: str, value: str) -> dict:
        """DELETE /api/v1/whitelists?domain_addr=&value= — delete by composite key."""
        return self._request("DELETE",
            f"/api/v1/whitelists?domain_addr={domain_addr}&value={value}")

    # ── Agent State API (per-agent KV store) ─────────────────────

    def agent_state_get(self, key: str) -> Optional[str]:
        """GET /api/v1/agent-state/:key - returns value string or None."""
        result = self._request("GET", f"/api/v1/agent-state/{key}")
        if result.get("status") == 200:
            return result.get("value")
        return None

    def agent_state_put(self, key: str, value: str) -> dict:
        """PUT /api/v1/agent-state/:key - upsert a value."""
        return self._request("PUT", f"/api/v1/agent-state/{key}", body={"value": value})

    # ── Semantic endpoints ──────────────────────────────

    def put_contact(self, address: str, profile: str) -> dict:
        """PUT /api/v1/contacts/:address — atomic write + name index + merge."""
        return self._request("PUT", f"/api/v1/contacts/{address}",
                             body={"profile": profile})

    def get_contact(self, address: str) -> Optional[dict]:
        """GET /api/v1/contacts/:address — returns {address, profile} or None."""
        result = self._request("GET", f"/api/v1/contacts/{address}")
        if result.get("status") == 200:
            return {"address": result.get("address"), "profile": result.get("profile")}
        return None

    def get_contacts_by_name(self, name: str) -> list:
        """GET /api/v1/contacts?name=... — returns [{"address":...,"profile":...}]."""
        result = self._request("GET", f"/api/v1/contacts?name={name}")
        if result.get("status") == 200:
            return result.get("results", [])
        return []

    def get_contact_profiles(self, addresses: List[str]) -> dict:
        """GET /api/v1/contacts?addresses=a,b,c — batch profile lookup.

        Returns {"my_profile": {...}|None, "sender_profile": {addr: profile},
        "recipients_profile": {addr: profile}, "results": [...]}.
        Empty dict on failure (caller treats as no profiles available).
        """
        addrs = [a.strip() for a in addresses if a and a.strip()]
        if not addrs:
            return {}
        q = urllib.parse.quote(",".join(addrs), safe="@")
        result = self._request("GET", f"/api/v1/contacts?addresses={q}")
        if result.get("status") == 200:
            return {
                "my_profile": result.get("my_profile"),
                "sender_profile": result.get("sender_profile", {}),
                "recipients_profile": result.get("recipients_profile", {}),
            }
        logger.warning("[aimail_gateway] batch contacts lookup failed: HTTP %s", result.get("status"))
        return {}

    # ── Domain / API Key management ─────────────────────────────

    def list_system_domains(self, system_id: str) -> list:
        """GET /api/v1/admin/systems/:sid/domains — list domains for a system."""
        result = self._request("GET", f"/api/v1/admin/systems/{system_id}/domains")
        data = result.get("data", result) if isinstance(result, dict) else result
        return data if isinstance(data, list) else []

    def update_system_domain(self, domain_id: str, webhook_url: str = "",
                             webhook_secret: str = "") -> dict:
        """PUT /api/v1/admin/system-domains/:id — update webhook config."""
        body = {}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if webhook_secret:
            body["webhook_secret"] = webhook_secret
        return self._request("PUT", f"/api/v1/admin/system-domains/{domain_id}", body=body)

    def get_api_key_by_email(self, email: str) -> dict:
        """GET /api/v1/admin/api-keys?email= — lookup API key by email."""
        result = self._request("GET", f"/api/v1/admin/api-keys?email={email}")
        entries = result.get("entries", result.get("data", []))
        if isinstance(entries, list) and entries:
            return entries[0]
        return {}

    def delete_api_key(self, key_id: int) -> dict:
        """DELETE /api/v1/admin/api-keys/:id — delete an API key."""
        return self._request("DELETE", f"/api/v1/admin/api-keys/{key_id}")

    def register_email(
        self,
        system_id: str,
        email: str,
        webhook_url: str,
        webhook_secret: str,
        manager_address: str = "",
        generate_code: bool = False,
    ) -> dict:
        """POST /api/v1/admin/systems/:sid/addresses — register an agent address.
        When generate_code=True, also creates an activation code in one call.
        Note: domain is derived by the gateway from the email address itself —
        no domain/mx_domain parameter (mx_domain removed 2026-08-18, was never sent)."""
        params = "?generate_code=true" if generate_code else ""
        result = self._request(
            "POST",
            f"/api/v1/admin/systems/{system_id}/addresses{params}",
            body={
                "id": f"addr-{email.replace('@', '-at-')}-{int(time.time())}",
                "email": email,
                "webhook_url": webhook_url,
                "webhook_secret": webhook_secret,
                "manager_address": manager_address,
            },
        )
        return result

    # ── System Activation ─────────────────────────────────────────

    def activate_system(self, code: str, **kwargs) -> dict:
        """POST /api/v1/activate-system -- Activate a system using a product code.

        No authentication required -- the activation code IS the credential.
        Extra kwargs (system_id, system_name, domain) are passed through
        as optional fields -- the server auto-generates any missing values.

        Args:
            code: The product activation code (e.g. "prod-xxxx-xxxx-...")

        Returns ``{"status": 200, "raw_key": "sk-...", "system_id": "...", ...}``
        """
        body = {"code": code}
        # Pass through any optional overrides
        for k in ("system_id", "system_name", "domain"):
            v = kwargs.get(k)
            if v:
                body[k] = v
        result = self._request("POST", "/api/v1/activate-system", body=body)
        raw_key = result.get("raw_key", "")
        if not raw_key:
            return {"success": False, "error": f"activation failed: {result}"}
        return {
            "success": True,
            "raw_key": raw_key,
            "system_id": result.get("system_id", ""),
            "system_name": result.get("system_name", ""),
            "domain": result.get("domain", ""),
        }

    # ── Address Activation (Agent side) ─────────────────────────

    def activate_address(self, code: str, email_address: str = "", scopes: Optional[list] = None) -> dict:
        """POST /api/v1/activate-address -- Agent activates an address code to get raw_key.

        No authentication required -- the address activation code IS the credential.

        Args:
            code: The address activation code (e.g. "addr-xxxx-xxxx-...")
            email_address: The email address to bind to the API key (required)
            scopes: Optional scope list (defaults to ["agent"])

        Returns ``{"status": 200, "raw_key": "sk-...", "api_key_id": N, ...}``
        """
        body = {"code": code, "email_address": email_address, "scopes": scopes or ["agent"]}
        result = self._request("POST", "/api/v1/activate-address", body=body)
        raw_key = result.get("raw_key", "")
        if not raw_key:
            return {"success": False, "error": f"activation failed: {result}"}
        return {
            "success": True,
            "raw_key": raw_key,
            "api_key_id": result.get("api_key_id", ""),
        }




# ═══════════════════════════════════════════════════════════════
# Agent Tools
# ═══════════════════════════════════════════════════════════════

# ── Agent-system detection registry ──────────────────────────────
# Different agent systems are detected (and versioned) differently.
# Add a new entry to extend support; explicit config/env always wins
# over automatic detection (see _agent_identity).
# 显式身份覆盖:多 agent 共存机器上"目录存在"检测会误判(Hermes 目录在
# OpenClaw 机器也存在,registry 顺序导致 OpenClaw 进程被检测为 hermes)。
# 平台适配层(amail_base/hermes adapter)在 import 时注入自己的身份。
_AGENT_IDENTITY_OVERRIDE = None    # 由适配层设置,如 "openclaw/2026.7.1"
_AGENT_MODEL_OVERRIDE = None       # 由适配层设置,主/默认模型名(如 "glm-5.3-flash")


def set_agent_model(model: str) -> None:
    """设置 X-AIMail-Agent 的模型段(主/默认模型),头变 {platform}/{ver}+{model}。"""
    global _AGENT_MODEL_OVERRIDE
    if isinstance(model, str) and model.strip():
        _AGENT_MODEL_OVERRIDE = model.strip()

_AGENT_DETECTORS = [
    {
        "name": "hermes",
        "detect": lambda home: bool(os.environ.get("HERMES_PROFILE_DIR"))
        or os.path.isdir(os.path.join(home, ".hermes")),
        "version_args": ["hermes", "--version"],
        "version_re": r"v([0-9][0-9.]*)",
    },
    {
        "name": "openclaw",
        "detect": lambda home: os.path.isdir(os.path.join(home, ".openclaw")),
        "version_args": ["openclaw", "--version"],
        "version_re": r"([0-9][0-9.]*-?[0-9]*)",
    },
]


def _detect_agent_identity() -> str:
    """Auto-detect {platform}/{version} by walking the detector registry."""
    if _AGENT_IDENTITY_OVERRIDE:
        return _AGENT_IDENTITY_OVERRIDE
    home = os.path.expanduser("~")
    for det in _AGENT_DETECTORS:
        try:
            if det["detect"](home):
                version = "unknown"
                try:
                    r = subprocess.run(
                        det["version_args"], capture_output=True, text=True, timeout=5
                    )
                    out = (r.stdout or r.stderr).strip()
                    m = re.search(det["version_re"], out)
                    if m:
                        version = m.group(1)
                except Exception:
                    pass
                return f"{det['name']}/{version}"
        except Exception:
            continue
    return "unknown/unknown"


def _agent_identity() -> str:
    """X-AIMail-Agent header value: {platform}/{version}.

    Only real detection results are reported: the host agent system is
    identified by walking the _AGENT_DETECTORS registry (different
    systems are detected differently). If nothing is detected the value
    is unknown/unknown — we never guess.
    """
    ident = _detect_agent_identity()
    if _AGENT_MODEL_OVERRIDE and '+' not in ident:
        ident = f"{ident}+{_AGENT_MODEL_OVERRIDE}"
    return ident


def send_mail(
    to: Union[str, List[str]],
    subject: str,
    body: str,
    cc: Optional[Union[str, List[str]]] = None,
    attachments: Optional[List[str]] = None,
    message_id: Optional[str] = None,
) -> dict:
    """Send an email via your aimail address.

    Attachments (file paths) are automatically uploaded before sending.
    For replies, pass the original email's message_id -- the tool will
    automatically resolve In-Reply-To, References headers, and the
    sender persona (from the stored inbound message metadata).
    """
    # Normalize array args
    if isinstance(to, list):
        to = ", ".join(to)
    # NOTE: cc 宽容归一化为 list — LLM 常按 schema 传逗号串 "a@x, b@y",
    # 而网关 SendEmailRequest.cc: Option<Vec<String>> 要求 JSON 数组
    # (字符串 → axum 反序列化 422)。list 透传,字符串按逗号拆分。
    if cc is None:
        cc_list: Optional[List[str]] = None
    elif isinstance(cc, str):
        cc_list = [a.strip() for a in cc.split(",") if a.strip()]
    else:
        cc_list = [str(a).strip() for a in cc if str(a).strip()]
    if not cc_list:
        cc_list = None

    config = _load_profile_config()
    if not config:
        return {"success": False, "error": "aimail not configured for this profile"}

    if not config.get("api_key"):
        return {"success": False, "error": "aimail api_key not available (activation may have failed)"}

    # ── Guard: email must be configured ──────────────────────
    base_email = config.get("email", "")
    if not base_email:
        return {"success": False, "error": "aimail email not configured for this profile — cannot send"}

    client = _GatewayClient(config["gateway_url"], config["api_key"])

    # ── Resolve message metadata once (avoids duplicate HTTP round-trip) ──
    msg_meta = _load_message_meta(message_id) if message_id else None

    # ── Resolve sender: persona from inbound metadata > current persona > base email ──
    sender = base_email
    if msg_meta:
        stored_persona = msg_meta.get("my_amail_addr", "")
        if stored_persona and "@" in stored_persona:
            sender = stored_persona
            logger.info("[aimail] Reply detected — using persona sender: %s", sender)
    elif not message_id:
        # New email: use the current persona ONLY if it is an approved persona
        # (present in agent.personalities). Deriving from the profile dir name
        # alone produced unregistered sender addresses (agent.<name>.<profile>@)
        # → gateway 403 Sender mismatch. Unapproved → fall back to base email.
        persona = _current_persona_name()
        if persona:
            approved = list_personas()
            if persona in approved:
                local, domain = base_email.split("@", 1)
                sender = f"{local}.{persona}@{domain}"
                logger.info("[aimail] New email from approved persona '%s' — sender: %s", persona, sender)

    # Parse recipients (cc normalized above at L482-489)
    to_list = [a.strip() for a in to.split(",") if a.strip()]

    # Detect forward vs reply from subject line (case-insensitive "fw:" prefix)
    _is_forward = bool(message_id and subject and subject.lower().startswith("fw:"))

    # Resolve threading headers from message_id
    in_reply_to = None
    references = None
    if message_id:
        if not _is_forward:
            in_reply_to = message_id
        if msg_meta:
            # Build References: original references + original message_id
            refs = msg_meta.get("references", [])
            if isinstance(refs, str):
                refs = [r.strip() for r in refs.split() if r.strip()]
            all_refs = refs + [message_id]
            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for r in all_refs:
                if r not in seen:
                    seen.add(r)
                    deduped.append(r)
            references = " ".join(deduped)
        else:
            # No metadata -- just use message_id as the reference chain start
            references = message_id

    # Resolve and validate attachments
    resolved_paths, resolve_errors = _resolve_attachments(attachments) if attachments else ([], [])
    if resolve_errors:
        return {"success": False, "error": "Attachment resolution failed", "details": resolve_errors}

    # Size checks + upload
    upload_errors = []
    attachment_ids = []
    for path in resolved_paths:
        size_err = _check_attachment_size(path)
        if size_err:
            upload_errors.append(size_err)
            continue
        resp = client.upload_attachment(path)
        if resp.get("status") == 201:
            attachment_ids.append({"id": resp.get("attachment_id", resp.get("id", ""))})
        else:
            upload_errors.append(f"Upload failed for {Path(path).name}: {resp.get('error', 'HTTP ' + str(resp.get('status', '?')))}")

    if upload_errors and not attachment_ids:
        return {"success": False, "error": "All attachments failed", "details": upload_errors}

    # ── 先存再调: meta 常写 + outbox 快照按开关, 随后才调 API ──
    # Message-ID 本地生成并传给 gateway(仅无 id 时才自动补全), 本地值即线上值。
    generated_mid = _build_message_id(config)
    _store_message_meta(generated_mid, references, my_amail_addr=sender)
    if config.get("save_raw_snapshots"):
        _save_outbound_snapshot(generated_mid, sender, sender, to, subject, body,
                                cc_list or [], resolved_paths or [], attachment_ids or [],
                                in_reply_to or "", references or "")

    # ── Submit with bounded retry + terminal semantics ───────────
    # The LLM must never see a "fixable" failure state — that is what
    # drove the 2026-08-28 duplicate-reply storm (422 → agent wrote
    # diag scripts → re-sent the same reply 3× out-of-band). So the
    # tool owns the whole failure loop:
    #   - retryable (network drop / 429 / 5xx): retry w/ backoff, ≤5
    #   - 409 duplicate_send: the email IS already out → success
    #   - anything else: TERMINAL failure with an explicit guardrail
    #     instruction to stop, not to work around.
    _RETRYABLE_STATUSES = {0, 429, 500, 502, 503, 504}
    _MAX_ATTEMPTS = 5
    _GUARDRAIL = (
        "This is terminal. Do NOT retry by any other means "
        "(no terminal commands, curl, scripts, or direct gateway API "
        "calls). Report this failure in your reply to the sender."
    )

    status = 0
    result: Dict[str, Any] = {}
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        result = client.send_mail(
            to=",".join(to_list),
            subject=subject,
            body=body,
            cc=cc_list,
            attachments=attachment_ids if attachment_ids else None,
            in_reply_to=in_reply_to,
            references=references,
            sender=sender,
            message_id=generated_mid,
            headers={"X-AIMail-Agent": _agent_identity()},
        )
        status = result.pop("status", 0)
        if 200 <= status < 300 or (status == 409 and result.get("error") == "duplicate_send"):
            break
        if status not in _RETRYABLE_STATUSES or attempt == _MAX_ATTEMPTS:
            break
        delay = min(2 ** (attempt - 1), 8)
        logger.warning(
            "[aimail] send attempt %d/%d failed (HTTP %s: %s), retrying in %ds",
            attempt, _MAX_ATTEMPTS, status,
            result.get("error") or result.get("detail") or "?", delay,
        )
        time.sleep(delay)

    # Auto-bootstrap thread summary for new (non-reply) emails
    thread_bootstrapped = False
    if not message_id:
        try:
            initial_summary = f"Subject: {subject}\nStatus: awaiting response"
            set_email_summary(generated_mid, initial_summary)
            thread_bootstrapped = True
            logger.info("[aimail] Thread summary bootstrapped for new email: %s", generated_mid)
        except Exception as e:
            logger.warning("[aimail] Failed to bootstrap thread summary: %s", e)

    # Flatten status into success/error
    if 200 <= status < 300:
        out = {"success": True, **result}
        if thread_bootstrapped:
            out["thread_bootstrapped"] = True
        if upload_errors:
            out["note"] = f"Sent, but {len(upload_errors)} attachment(s) had issues: {'; '.join(upload_errors[:3])}"
        # Log outbound to the per-agent aimail.log for integration test
        # verification (send_welcome polls this file instead of the stats API).
        try:
            _log_amail("outbound", sender, to, subject, email_id=generated_mid)
        except Exception:
            pass
        return out

    if status == 409 and result.get("error") == "duplicate_send":
        # An identical (to/cc/subject/body) email was already accepted by
        # the gateway within the dedup window. From the agent's perspective
        # the content IS out — report success, nothing left to do.
        logger.info("[aimail] send suppressed by gateway dedup (409) — already sent")
        return {
            "success": True,
            "duplicate": True,
            "note": "An identical email was already sent (gateway dedup window). "
                    "The content is already out — no further action needed.",
        }

    error = result.get("error", result.get("detail", f"HTTP {status}"))
    return {
        "success": False,
        "error": f"Send failed (terminal, HTTP {status}): {error}",
        "instruction": _GUARDRAIL,
    }


def manage_contacts(
    action: str,
    address: Optional[str] = None,
    direction: str = "all",
    **kwargs,
) -> dict:
    """Manage your address book (whitelist).

    Args:
        action: "check", "add", or "remove"
        address: email address to add/remove (required for add/remove)
        direction: "from" (default, inbound receive) or "to" (outbound send) or "all"
    """
    config = _load_profile_config()
    if not config:
        return {"success": False, "error": "aimail not configured for this profile"}

    client = _GatewayClient(config["gateway_url"], config["api_key"])
    # Agent whitelist is per-profile, not per-domain.
    # domain_addr = aimail address (agent-1@mail.project.com)
    email_addr = config.get("email", "")

    if action == "check":
        if not address:
            return {"success": False, "error": "address is required for check"}
        result = client.check_whitelist_value(email_addr, address, direction)
        return {
            "success": True,
            "in_contacts": result.get("in_contacts", False),
            "direction": result.get("direction", direction),
            "address": address,
        }

    elif action == "add":
        if not address:
            return {"success": False, "error": "address is required for add"}
        # Agent cannot directly add to whitelist.
        # Instead, send a request email to the manager for approval.
        # The manager replies with "add X to my contacts" which is processed
        # by webhook.rs handle_manager_commands.
        manager_addr = config.get("manager_address", "")
        if not manager_addr:
            return {"success": False, "error": "No manager_address configured — cannot send approval request"}
        client_mgr = _GatewayClient(config["gateway_url"], config["api_key"])
        description = kwargs.get("description", "") if kwargs else ""
        desc_line = f"\ndescription: {description}" if description else ""
        result = client_mgr.send_mail(
            to=manager_addr,
            subject=f"[AIMail] Contact request: {address}",
            body=f"Please add {address} to {email_addr}'s contacts with direction={direction}.{desc_line}\n\n"
                 f"To approve, reply to this email with:\nadd {address} to my contacts with direction={direction}",
        )
        status = result.get("status", 0)
        if 200 <= status < 300:
            return {"success": True, "note": f"Approval request sent to manager ({manager_addr})"}
        error = result.get("error", f"HTTP {status}")
        return {"success": False, "error": f"Failed to send approval request: {error}"}

    elif action == "remove":
        if not address:
            return {"success": False, "error": "address is required for remove"}
        result = client.delete_whitelist_by_value(email_addr, address)
        status = result.pop("status", 0)
        if status == 204:
            return {"success": True}
        if status == 404:
            return {"success": False, "error": f"{address} not found in whitelist"}
        error = result.get("error", result.get("detail", f"HTTP {status}"))
        return {"success": False, "error": f"Failed to remove {address}: {error}"}

    elif action == "update":
        if not address:
            return {"success": False, "error": "address is required for update"}
        new_direction = kwargs.get("direction", direction)
        if not new_direction:
            return {"success": False, "error": "direction is required for update"}
        result = client.update_whitelist_by_value(email_addr, address, new_direction)
        status = result.pop("status", 0)
        if 200 <= status < 300:
            return {"success": True, "note": f"direction updated to {new_direction}"}
        error = result.get("error", result.get("detail", f"HTTP {status}"))
        return {"success": False, "error": f"Failed to update {address}: {error}"}


    else:
        return {"success": False, "error": f"Unknown action: {action}"}



# ── Contact profile (for context awareness) ──────────────────────

def contact_profile(address: str = "", name: str = "") -> dict:
    """Look up a contact profile by address or name.

    At least one of address or name must be provided.
    - address: exact lookup via GET /api/v1/contacts/:address
    - name: server-side search via GET /api/v1/contacts?name=
    """
    if not address and not name:
        return {"address": "", "profile": None, "error": "address or name required"}

    config = _load_profile_config()
    if not config:
        return {"address": address, "profile": None}
    client = _GatewayClient(config["gateway_url"], config["api_key"])

    # Search by address (exact match) — semantic endpoint
    if address:
        contact = client.get_contact(address)
        if contact:
            return {"address": address, "profile": contact.get("profile")}
        return {"address": address, "profile": None}

    # Search by name (server-side)
    results = client.get_contacts_by_name(name.strip())
    if not results:
        return {"address": "", "profile": None, "searched_name": name}
    if len(results) == 1:
        return {"address": results[0]["address"], "profile": results[0]["profile"]}
    return {"ambiguous": True, "candidates": [r["address"] for r in results]}



def set_contact_profile(address: str, profile: str) -> dict:
    """Store or update a contact profile. The gateway handles JSON merge,
    name extraction, and name index maintenance atomically.
    """
    config = _load_profile_config()
    if not config:
        return {"success": False, "error": "no profile config"}
    client = _GatewayClient(config["gateway_url"], config["api_key"])
    return client.put_contact(address, profile)

# ═══════════════════════════════════════════════════════════════
# Message Metadata — LOCAL meta/{xx}/{safe_mid}.json (always written)
#    value: {"references": [...], "thread_id": ..., "my_amail_addr": ...,
#            "direction": "inbound|outbound"}
#    Sharded by first 2 chars of the sanitized mid (256 buckets).
#    Replaces the former gateway agent_state msg:{mid} key.
# ═══════════════════════════════════════════════════════════════

# Local-only helpers for raw email snapshots (not gateway data)


def _build_message_id(config: dict) -> str:
    """Generate a Message-ID header value from the configured domain."""
    import uuid as _uuid
    domain = config.get("domain", "") or "amail.local"
    return f"<{_uuid.uuid4().hex}@{domain}>"


def _sanitize_message_id(message_id: str) -> str:
    mid = message_id.strip().lstrip("<").rstrip(">")
    for ch in "/\\:*?\"<>|@ ":
        mid = mid.replace(ch, "_")
    return mid


# ── Local meta / thread summary (meta/{xx}/, threads/{xx}/) ─────────

def _local_meta_path(message_id: str) -> Path:
    """meta/{前两位}/{safe_mid}.json — 前两位分片(256 桶)防平铺目录膨胀。"""
    k = _sanitize_message_id(message_id)
    return _aimail_dir() / "meta" / k[:2] / f"{k}.json"


def _thread_path(thread_id: str) -> Path:
    """threads/{前两位}/{safe_tid}.json — 会话摘要, 与 meta 同分片策略。"""
    k = _sanitize_message_id(thread_id)
    return _aimail_dir() / "threads" / k[:2] / f"{k}.json"


def _save_local_meta(message_id, references, my_amail_addr, direction) -> None:
    """Per-message lightweight metadata (常写, 不受 save_raw_snapshots 控制).

    回复链依赖: references/thread_id/my_amail_addr 替代 gateway agent_state
    的 msg:{mid} key(已在 gateway 侧删除)。"""
    mid = (message_id or "").strip()
    if not mid:
        return
    if isinstance(references, str):
        refs = [r.strip() for r in references.split() if r.strip()]
    else:
        refs = [str(r).strip() for r in (references or []) if str(r).strip()]
    payload = {
        "message_id": mid,
        "references": refs,
        "thread_id": refs[0] if refs else mid,
        "my_amail_addr": my_amail_addr or "",
        "direction": direction,
        "at": datetime.now().isoformat(),
    }
    try:
        p = _local_meta_path(mid)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.warning("Failed to save local meta for %s: %s", mid, e)


def _read_local_meta(message_id: str) -> Optional[dict]:
    try:
        return json.loads(_local_meta_path(message_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_thread_id(message_id: str) -> str:
    """thread_id = 首条 References(线程根), 无则 message_id 本身。"""
    meta = _read_local_meta(message_id)
    return (meta or {}).get("thread_id") or (message_id or "").strip()


# ── Attachment path resolution ─────────────────────────────────────

ATTACH_MAX_SIZE_MB = 10
ATTACH_MAX_SEARCH_DEPTH = 5    # max directory depth from workspace root
ATTACH_MAX_SEARCH_MATCHES = 50  # stop early if too many candidates
ATTACH_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    ".hermes", "target", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "dist", "build", "__pypackages__",
}


def _resolve_attachments(raw_paths: list) -> tuple:
    """Resolve a list of attachment references to verified absolute paths.

    Resolution order for each item:
      1. Absolute path — verify it exists.
      2. CWD-relative — resolve, verify it exists.
      3. Bare filename — walk workspace looking for a unique match.
      4. No match / ambiguous → returned as error for the caller to surface.

    Returns (resolved: list[str], errors: list[str]).
    """
    resolved: list[str] = []
    errors: list[str] = []

    cwd = Path.cwd()
    workspace_roots = _workspace_roots()

    for raw in raw_paths:
        raw = raw.strip()
        if not raw:
            continue

        p = Path(raw)

        # 1. Absolute path
        if p.is_absolute():
            if p.is_file():
                resolved.append(str(p))
            else:
                errors.append(f"Attachment not found: {raw}")
            continue

        # 2. CWD-relative
        cwd_candidate = (cwd / p).resolve()
        if cwd_candidate.is_file():
            resolved.append(str(cwd_candidate))
            continue

        # 3. Bare filename — search workspace trees
        name = p.name
        if not name:
            errors.append(f"Invalid attachment path: {raw}")
            continue

        matches: list[Path] = []
        for root in workspace_roots:
            if not root.is_dir():
                continue
            for candidate in root.rglob(name):
                # Depth guard — skip files nested too deep
                depth = len(candidate.relative_to(root).parts)
                if depth > ATTACH_MAX_SEARCH_DEPTH:
                    continue
                if _is_skipped_dir(candidate):
                    continue
                if candidate.name != name:
                    continue
                matches.append(candidate)
                # Early exit — avoid scanning the entire filesystem
                if len(matches) >= ATTACH_MAX_SEARCH_MATCHES:
                    break
            if len(matches) >= ATTACH_MAX_SEARCH_MATCHES:
                break

        # Deduplicate by resolved path
        unique = list(dict.fromkeys(str(m.resolve()) for m in matches))

        if len(unique) == 0:
            errors.append(
                f"Attachment '{name}' not found in workspace. "
                f"Provide an absolute or CWD-relative path."
            )
        elif len(unique) == 1:
            resolved.append(unique[0])
        else:
            # Multiple matches — need disambiguation
            candidates = "\n    ".join(unique[:5])
            errors.append(
                f"Ambiguous attachment '{name}' — found {len(unique)} files:\n"
                f"    {candidates}\n"
                f"  Use a more specific path."
            )

    return resolved, errors


def _workspace_roots() -> list[Path]:
    """Return the directories to search for bare-filename attachments."""
    roots: list[Path] = [Path.cwd()]

    # Profile directory (agent's working sandbox)
    import aimail_base as _abm
    _resolver = _abm._PROFILE_DIR_RESOLVER
    profile_dir = _resolver() if _resolver else ""
    if profile_dir:
        roots.append(Path(profile_dir))

    # Home — broad but last-resort; walk depth limited in practice
    home = Path.home()
    if home.is_dir():
        roots.append(home)

    return roots


def _is_skipped_dir(path: Path) -> bool:
    """True if any ancestor of *path* is a directory that should be skipped."""
    for parent in path.parents:
        if parent.name in ATTACH_SKIP_DIRS:
            return True
    return False


def _check_attachment_size(path: str) -> Optional[str]:
    """Return an error message if the file exceeds the size limit, else None."""
    try:
        size_mb = Path(path).stat().st_size / (1024 * 1024)
        if size_mb > ATTACH_MAX_SIZE_MB:
            return (
                f"Attachment '{Path(path).name}' is {size_mb:.1f} MB — "
                f"max allowed is {ATTACH_MAX_SIZE_MB} MB"
            )
    except OSError:
        pass
    return None


# ── Raw email snapshots ────────────────────────────────────────────

def _save_outbound_snapshot(out_msg_id: str, my_addr: str, sender: str,
                             to: str, subject: str, body: str,
                             cc_list: list, resolved_paths: list, attachment_ids: list,
                             in_reply_to: str, references: str) -> None:
    """Save a JSON snapshot of an outbound email to raw_email/.

    my_addr determines the snapshot subdirectory (persona or base address).
    Attachments are copied to {yyyymm}/attch/{safe_mid}/ (same layout as
    inbound); the snapshot records local paths + gateway attachment_ids.
    """
    safe_mid = _sanitize_message_id(out_msg_id)
    now = datetime.now()
    yyyymm = now.strftime("%Y%m")
    snapshot_dir = _raw_email_dir() / yyyymm
    snapshot_path = snapshot_dir / f"out-{safe_mid}.json"
    local_atts: list = []
    if resolved_paths:
        attch_dir = snapshot_dir / "attch" / safe_mid
        try:
            attch_dir.mkdir(parents=True, exist_ok=True)
            for src in resolved_paths:
                s = Path(src)
                if s.is_file():
                    dest = attch_dir / s.name
                    dest.write_bytes(s.read_bytes())
                    local_atts.append(str(dest))
        except Exception:
            logger.warning("Failed to copy outbound attachments for %s", safe_mid)
    att_md = _collect_attachments_md(local_atts)
    payload = {
        "message_id": out_msg_id,
        "direction": "outbound",
        "sender": sender,
        "to": to,
        "cc": ", ".join(cc_list) if cc_list else "",
        "subject": subject,
        "body": body,
        "attachments": local_atts,
        "attachment_ids": attachment_ids,
        "in_reply_to": in_reply_to,
        "references": references,
        "sent_at": now.isoformat(),
    }
    if att_md:
        payload["attachments_md"] = att_md
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        tmp = snapshot_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(snapshot_path)
        # Incremental FTS5 index AFTER the snapshot file is on disk.
        to_list = [t.strip() for t in (to or "").split(",") if t.strip()] + list(cc_list or [])
        _index_snapshot_record(
            mid=out_msg_id,
            direction="outbound",
            ts=now.isoformat(),
            subject=subject or "",
            from_addr=sender or "",
            to_json=json.dumps(to_list, ensure_ascii=False),
            body=body or "",
            att_text=_join_att_md(att_md),
            thread_id="",
        )
    except Exception:
        logger.warning("Failed to save outbound email snapshot for %s", safe_mid)


# ── persona 上下文注入点（Hermes 适配层注入；OpenClaw 无 persona → None）──
_PERSONA_NAME_PROVIDER = None  # () -> Optional[str]


def _current_persona_name() -> Optional[str]:
    """当前 persona 名（注入点）。Hermes 适配层注入（profile 目录派生）；
    OpenClaw 无 persona 概念 → None（发件用基础地址）。"""
    return _PERSONA_NAME_PROVIDER() if _PERSONA_NAME_PROVIDER is not None else None




def _resolve_agent_email() -> str:
    """Resolve the agent's base email via the pointer file (for log naming)."""
    # 第一优先:显式注入(OpenClaw set_agent_context 设置;Hermes
    # 不改此环境变量,走指针逻辑)。避免日志落 aimail.default.log。
    env_email = os.environ.get("AIMAIL_AGENT_EMAIL", "")
    if env_email:
        return env_email
    import aimail_base as _abm
    _resolver = _abm._PROFILE_DIR_RESOLVER
    pdir = _resolver() if _resolver else ""
    if pdir:
        pointer = Path(pdir) / ".agentmail"
        if pointer.is_file():
            try:
                pd = json.loads(pointer.read_text())
                return pd.get("email", "")
            except Exception:
                pass
    return ""


def _aimail_dir() -> Path:
    """Per-agent mail data leaf: {aimail_home}/mail/{cleaned_addr}/.

    Layout: {aimail_home}/mail/{cleaned_addr}/ — email content + attachments,
    isolated from {aimail_home}/systems/ (config). aimail_home() is env
    AIMAIL_HOME or ~/.aimail — the home ROOT, so
    the env var relocates the whole tree (mirrors TS agentMailDir())."""
    import aimail_base as _abm
    base = _abm.aimail_home()
    email = _resolve_agent_email()
    if email:
        return base / "mail" / _abm._clean_agent_dir_name(email)
    return base / "mail" / "default"


def _raw_email_dir() -> Path:
    """Return the directory for raw email snapshots (yyyymm subdir appended by caller)."""
    return _aimail_dir()



def _log_amail(direction: str, from_addr: str, to_addr: str, subject: str,
               email_id: str = "") -> None:
    """Append a lightweight email processing log entry (not dependent on save_raw_snapshots).

    Log is written to {AIMAIL_HOME}/aimail.log for integration test verification.
    """
    import json as _json
    import aimail_base as _abm
    log_path = _abm.aimail_log_path(_resolve_agent_email())
    entry = _json.dumps({
        "ts": datetime.now().isoformat(),
        "dir": direction,
        "from": from_addr,
        "to": to_addr,
        "subj": subject,
        **({"email_id": email_id} if email_id else {}),
    }, ensure_ascii=False)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(entry + "\n")
    except Exception:
        logger.debug("Failed to write aimail log: %s", log_path)

def store_inbound_message(
    message_id: str,
    references: list,
    my_amail_addr: str,
    preprocessed_payload: Optional[dict] = None,
    attachment_sources: Optional[dict] = None,
) -> Optional[str]:
    """Called by the gateway preprocessor when an inbound email arrives.

    Optionally (save_raw_snapshots=true): saves the AGENT-VISIBLE JSON snapshot
    (AFTER preprocessing) to raw_email/{agent_addr}/{yyyymm}/.

    IMPORTANT: preprocessed_payload must be the output of preprocess_mail_payload()
    — the agent-visible format with sender/recipients/my_amail_addr/direct_message fields.
    Do NOT pass the gateway RAW webhook payload.
    """
    if not message_id or not message_id.strip():
        return None
    mid = message_id.strip()

    # ── Always-write local meta (回复链依赖, 不受快照开关控制) ──
    _save_local_meta(mid, references, my_amail_addr, direction="inbound")

    # Only save the agent-visible snapshot if configured.
    config = _load_profile_config()

    # ── Optionally save agent-visible snapshot ──────────────────
    if not config or not config.get("save_raw_snapshots"):
        return None

    safe_mid = _sanitize_message_id(mid)
    now = datetime.now()
    yyyymm = now.strftime("%Y%m")
    snapshot_dir = _raw_email_dir() / yyyymm
    snapshot_path = snapshot_dir / f"in-{safe_mid}.json"
    attch_dir = snapshot_dir / "attch" / safe_mid

    snapshot_saved = False
    att_md: list = []
    if preprocessed_payload:
        # Guard: detect gateway RAW format (has 'mail_id' field — gateway-internal UUID)
        if "mail_id" in preprocessed_payload and "recipients" not in preprocessed_payload:
            logger.warning(
                "store_inbound_message received gateway RAW payload instead of preprocessed agent-visible JSON. "
                "Call preprocess_mail_payload() first. Snapshot may contain wrong format."
            )
        try:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            # Snapshot payload = agent-visible JSON + md-escaped attachment text
            # (textual attachments only, so full-text search covers attachments).
            snapshot_payload = dict(preprocessed_payload)
            if attachment_sources:
                att_md = _collect_attachments_md(list(attachment_sources.values()))
                if att_md:
                    snapshot_payload["attachments_md"] = att_md
            tmp = snapshot_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot_payload, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
            tmp.replace(snapshot_path)
            snapshot_saved = True
            # Incremental FTS5 index AFTER the snapshot file is on disk.
            _index_snapshot_record(
                mid=mid,
                direction="inbound",
                ts=snapshot_payload.get("ts") or snapshot_payload.get("date") or now.isoformat(),
                subject=snapshot_payload.get("subject") or "",
                from_addr=snapshot_payload.get("sender") or snapshot_payload.get("from") or "",
                to_json=_norm_to_json(snapshot_payload.get("to")),
                body=snapshot_payload.get("body") or "",
                att_text=_join_att_md(att_md),
                thread_id="",
            )
        except Exception:
            logger.warning("Failed to save inbound email snapshot for %s", safe_mid)

    if attachment_sources:
        try:


            attch_dir.mkdir(parents=True, exist_ok=True)
            for filename, src_path in (attachment_sources or {}).items():
                src = Path(src_path)
                if not src.is_file():
                    continue
                safe_name = Path(filename).name
                dst = attch_dir / safe_name
                dst.write_bytes(src.read_bytes())
        except Exception:
            logger.warning("Failed to copy attachments for %s", safe_mid)

    return str(snapshot_path) if snapshot_saved else None


def _load_message_meta(message_id: str) -> Optional[dict]:
    """Load per-message metadata from the local mail dir. None if not found.

    (Was gateway agent_state msg:{mid}; 本地化后零 HTTP 往返。)
    """
    if not message_id or not message_id.strip():
        return None
    return _read_local_meta(message_id.strip())


def _store_message_meta(message_id: str, references: Optional[str] = None,
                        my_amail_addr: str = "") -> None:
    """Store outbound message metadata locally for future replies (常写)."""
    _save_local_meta(message_id, references, my_amail_addr, direction="outbound")


# ═══════════════════════════════════════════════════════════════
# email_summary / set_email_summary — LOCAL threads/{xx}/{thread_id}.json
#    value: {"thread_id": ..., "summary": ..., "updated_at": ...}
#    空 summary = 删除线程文件(与原 gateway 语义一致)
# ═══════════════════════════════════════════════════════════════

def email_summary(message_id: str) -> dict:
    """Look up the stored summary for the email thread containing this message.

    Resolves message_id → thread_id via local meta (meta/{xx}/), then reads
    threads/{xx}/{thread_id}.json. Returns {"thread_id": ..., "summary": ...}.
    """
    mid = (message_id or "").strip()
    thread_id = _resolve_thread_id(mid) if mid else ""
    if not thread_id:
        return {"thread_id": "", "summary": ""}
    try:
        data = json.loads(_thread_path(thread_id).read_text(encoding="utf-8"))
        return {"thread_id": thread_id, "summary": data.get("summary", "")}
    except Exception:
        return {"thread_id": thread_id, "summary": ""}


def set_email_summary(message_id: str, summary: str) -> dict:
    """Store or update the summary for the email thread containing this message.

    Resolves message_id → thread_id via local meta, then writes
    threads/{xx}/{thread_id}.json. Empty summary deletes the thread file
    (same semantics as the former gateway endpoint).
    """
    if not message_id or not message_id.strip():
        return {"success": False, "error_code": "MESSAGE_ID_REQUIRED"}
    if not isinstance(summary, str):
        return {"success": False, "error_code": "SUMMARY_MUST_BE_STRING"}
    if len(summary) > 2000:
        return {"success": False, "error_code": "SUMMARY_TOO_LONG", "max_length": 2000}

    thread_id = _resolve_thread_id(message_id)
    if not summary.strip():
        # 空 summary = 删除线程(与原 gateway 语义一致)
        try:
            _thread_path(thread_id).unlink(missing_ok=True)
        except Exception:
            pass
        return {"success": True}
    data = {
        "thread_id": thread_id,
        "summary": summary,
        "updated_at": datetime.now().isoformat(),
    }
    try:
        p = _thread_path(thread_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        return {"success": False, "error": f"Failed to store summary: {e}"}
    return {"success": True}



# ═══════════════════════════════════════════════════════════════
# 本地全文检索:快照增量 FTS5 索引 + search_mail(2026-09-05)
#   顺序契约:快照文件先落盘, 再增量索引(同 mid 幂等)。
#   附件:文本型附件(护栏内)以 md 转义文本随快照落盘并进索引。
#   引擎:sqlite FTS5 trigram(python 内置;中文/英文子串均命中);
#   失败自动降级顺序扫快照(LIKE), 结果语义一致。
# ═══════════════════════════════════════════════════════════════
_TEXT_ATTACH_EXTS = {
    ".txt", ".md", ".markdown", ".json", ".csv", ".eml", ".log",
    ".yaml", ".yml", ".toml", ".xml", ".html", ".py", ".js", ".ts",
    ".rs", ".sh", ".ini", ".conf", ".rst",
}
_ATTACH_TEXT_MAX = 512 * 1024


def _search_index_path() -> Path:
    """Per-agent FTS5 index db: mail/{addr}/.search/index.db."""
    return _aimail_dir() / ".search" / "index.db"


def _open_search_index() -> "sqlite3.Connection | None":
    import sqlite3
    try:
        p = _search_index_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(p), timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            "CREATE TABLE IF NOT EXISTS emails("
            " mid TEXT PRIMARY KEY, dir TEXT NOT NULL, ts TEXT NOT NULL,"
            " subject TEXT DEFAULT '', from_addr TEXT DEFAULT '',"
            " to_json TEXT DEFAULT '', body TEXT DEFAULT '',"
            " att_text TEXT DEFAULT '', thread_id TEXT DEFAULT '');"
            "CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5("
            " subject, body, att_text, tokenize='trigram');"
        )
        return db
    except Exception:
        logger.debug("search index unavailable", exc_info=True)
        return None


def _index_snapshot_record(mid: str, direction: str, ts: str, subject: str,
                           from_addr: str, to_json: str, body: str,
                           att_text: str, thread_id: str) -> None:
    """Incremental UPSERT (idempotent by mid). Silent on failure."""
    db = _open_search_index()
    if db is None:
        return
    try:
        db.execute("DELETE FROM emails_fts WHERE rowid IN"
                   " (SELECT rowid FROM emails WHERE mid = ?)", (mid,))
        db.execute("DELETE FROM emails WHERE mid = ?", (mid,))
        cur = db.execute(
            "INSERT INTO emails(mid,dir,ts,subject,from_addr,to_json,body,att_text,thread_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, direction, ts, subject, from_addr, to_json, body, att_text, thread_id),
        )
        db.execute(
            "INSERT INTO emails_fts(rowid,subject,body,att_text) VALUES (?,?,?,?)",
            (cur.lastrowid, subject, body, att_text),
        )
        db.commit()
    except Exception:
        logger.debug("search index upsert failed for %s", mid, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def _norm_to_json(v) -> str:
    """Normalise payload 'to' (list | str | dict) to a JSON array."""
    import json as _json
    if isinstance(v, list):
        return _json.dumps([str(x) for x in v], ensure_ascii=False)
    if isinstance(v, dict):
        vals = v.get("to") or v.get("cc") or []
        if isinstance(vals, list):
            return _json.dumps([str(x) for x in vals], ensure_ascii=False)
    if isinstance(v, str):
        return _json.dumps([x.strip() for x in v.split(",") if x.strip()], ensure_ascii=False)
    return _json.dumps([], ensure_ascii=False)


def _is_text_attachment(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_ATTACH_EXTS and path.stat().st_size <= _ATTACH_TEXT_MAX


def _md_escape_attachment(path: Path) -> Optional[dict]:
    """Read a textual attachment and return {'name','text'} md-escaped, or None."""
    try:
        raw = path.read_bytes()[: _ATTACH_TEXT_MAX + 1]
        if len(raw) > _ATTACH_TEXT_MAX:
            return None
        text = raw.decode("utf-8", errors="replace")
        ext = path.suffix.lstrip(".").lower()
        fence = "```"
        while fence in text:
            fence += "`"
        return {"name": path.name, "text": f"{fence}{ext}\n{text.rstrip()}\n{fence}"}
    except Exception:
        return None


def _collect_attachments_md(paths: list) -> list:
    """Collect md-escaped text for textual attachments (silent on errors)."""
    out = []
    for p in paths or []:
        try:
            pp = Path(p)
            if pp.is_file() and _is_text_attachment(pp):
                item = _md_escape_attachment(pp)
                if item:
                    out.append(item)
        except Exception:
            continue
    return out


def _join_att_md(att_md: list) -> str:
    return "\n\n".join((a.get("text") or "") for a in att_md or [])


def _make_snippet(text: str, words: list, width: int = 90) -> str:
    """First hit of any word in text → window around it; '' if no hit."""
    low = text.lower()
    pos = -1
    for w in words:
        i = low.find(w)
        if i >= 0:
            pos = i
            break
    if pos < 0:
        return ""
    start = max(0, pos - width // 2)
    end = min(len(text), pos + width // 2)
    seg = text[start:end].replace("\n", " ").strip()
    return ("…" if start > 0 else "") + seg + ("…" if end < len(text) else "")


def _fts_query(words: list) -> str:
    """Quote each word for a safe trigram MATCH (empty words dropped)."""
    return " AND ".join(f'"{w}"' for w in words)


def _scan_snapshot_files(words: list, scope: str, since: str, until: str,
                         from_: str, limit: int) -> list:
    """Ordered-scan fallback over snapshot JSON files when the index db is
    unavailable. Month granularity for the time window (yyyymm dirs);
    LIKE semantics identical to the index fallback path."""
    import json as _json
    root = _aimail_dir()
    if not root.is_dir():
        return []
    rows = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir() or len(d.name) != 6 or not d.name.isdigit():
            continue
        ym = d.name
        if since and ym < since[:4] + since[5:7]:
            continue
        if until and ym > until[:4] + until[5:7]:
            continue
        for f in sorted(d.glob("*.json"), reverse=True):
            fn = f.name
            if not (fn.startswith("in-") or fn.startswith("out-")):
                continue
            if scope == "inbound" and not fn.startswith("in-"):
                continue
            if scope == "outbound" and not fn.startswith("out-"):
                continue
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            dir_ = "inbound" if fn.startswith("in-") else "outbound"
            subj = data.get("subject") or ""
            body = data.get("body") or ""
            att = "\n\n".join((a.get("text") or "") for a in (data.get("attachments_md") or []))
            sender = data.get("sender") or data.get("from") or ""
            if from_ and from_.lower() not in sender.lower():
                continue
            if words:
                hay = " ".join([subj.lower(), body.lower(), att.lower()])
                if not all(w in hay for w in words):
                    continue
            ts = (data.get("ts") or data.get("sent_at") or data.get("date")
                  or f"{ym[:4]}-{ym[4:6]}-01")
            rows.append((fn, dir_, ts, subj, sender, "[]", body, att, ""))
            if len(rows) >= limit:
                return rows
    return rows



def search_mail(query: str = "", scope: str = "all", since: str = "",
                until: str = "", from_: str = "", limit: int = 20) -> dict:
    """Search YOUR OWN locally stored mail (offline). Matches keywords in
    subject/body/attachment text; filters: mailbox scope (inbound/outbound),
    time window (since/until, YYYY-MM-DD) and sender (from_, substring,
    case-insensitive). Results newest first with a hit snippet.

    query: space-separated keywords (AND). Empty = filter-only browse.
    """
    if scope not in ("all", "inbound", "outbound"):
        return {"success": False, "error_code": "INVALID_SCOPE",
                "error": f"scope must be all|inbound|outbound, got {scope!r}"}
    if limit is None:
        limit = 20
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20
    import re as _re
    _DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for label, val in (("since", since), ("until", until)):
        if val and not _DATE_RE.match(val):
            return {"success": False, "error_code": "INVALID_DATE",
                    "error": f"{label} must be YYYY-MM-DD, got {val!r}"}
    if since and until and until < since:
        return {"success": False, "error_code": "INVALID_DATE",
                "error": "until must not be earlier than since"}

    words = [w.lower() for w in (query or "").split() if w.strip()]
    rows: list = []
    used_fts = False
    note = ""
    db = _open_search_index()
    if db is not None:
        try:
            where, params = [], []
            if scope != "all":
                where.append("dir = ?")
                params.append(scope)
            if since:
                where.append("substr(ts,1,10) >= ?")
                params.append(since)
            if until:
                where.append("substr(ts,1,10) <= ?")
                params.append(until)
            if from_:
                where.append("lower(from_addr) LIKE ?")
                params.append(f"%{from_.lower()}%")
            if words:
                # FTS path: every keyword >= 3 chars & alnum-only → trigram MATCH
                safe = all(len(w) >= 3 and w.isalnum() for w in words)
                if safe:
                    sql = ("SELECT mid,dir,ts,subject,from_addr,to_json,body,att_text,thread_id"
                           " FROM emails e JOIN emails_fts f ON f.rowid = e.rowid"
                           f" WHERE emails_fts MATCH ?{(' AND ' + ' AND '.join(where)) if where else ''}"
                           " ORDER BY e.ts DESC LIMIT ?")
                    match_params = [_fts_query(words)] + params + [limit]
                    try:
                        rows = db.execute(sql, match_params).fetchall()
                        used_fts = True
                    except Exception:
                        rows = []
            if not used_fts:
                # LIKE fallback across subject/body/att_text (also covers
                # short keywords and FTS-syntax-unsafe input). Semantics:
                # every word must hit in at least one column (word AND,
                # column OR), matching the FTS path.
                like_cols = ("subject", "body", "att_text")
                per_word, like_params = [], []
                for w in words:
                    per_word.append("(" + " OR ".join(f"{c} LIKE ?" for c in like_cols) + ")")
                    like_params += [f"%{w}%"] * len(like_cols)
                like_cond = " AND ".join(per_word)
                sql = ("SELECT mid,dir,ts,subject,from_addr,to_json,body,att_text,thread_id"
                       " FROM emails" + ((" WHERE " + like_cond) if words else "")
                       + ((" AND " + " AND ".join(where)) if where and words else
                          (" WHERE " + " AND ".join(where)) if where else "")
                       + " ORDER BY ts DESC LIMIT ?")
                rows = db.execute(sql, like_params + params + [limit]).fetchall()
        except Exception:
            logger.debug("search_mail db query failed — fallback scan", exc_info=True)
            rows = []
        finally:
            if not rows:
                # Empty index (feature on, nothing stored yet) vs a real miss.
                try:
                    n = db.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
                    if n == 0:
                        note = "no local mail index yet — snapshots are saved from now on"
                except Exception:
                    pass
            try:
                db.close()
            except Exception:
                pass
    if db is None:
        # No index available: ordered scan over snapshot files (same semantics).
        rows = _scan_snapshot_files(words, scope, since, until, from_, limit)

    results = []
    for mid, dir_, ts, subject, from_addr, to_json, body, att_text, thread_id in rows:
        to_list = []
        try:
            import json as _json
            to_list = _json.loads(to_json or "[]")
        except Exception:
            to_list = []
        if words:
            subj_hit = subject and all(w in subject.lower() for w in words)
            body_hit = body and all(w in body.lower() for w in words)
            att_hit = att_text and all(w in att_text.lower() for w in words)
            if subj_hit:
                matched_in, src = "subject", subject
            elif body_hit:
                matched_in, src = "body", body
            elif att_hit:
                matched_in, src = "attachment", att_text
            else:
                matched_in, src = "subject", subject
            snippet = _make_snippet(src, words)
        else:
            matched_in, snippet = "", ""
        results.append({
            "mid": mid, "dir": dir_, "ts": ts, "subject": subject,
            "from": from_addr, "to": to_list,
            "matched_in": matched_in, "snippet": snippet, "thread_id": thread_id,
        })
    out = {"success": True, "count": len(results), "results": results}
    if note:
        out["note"] = note
    return out
