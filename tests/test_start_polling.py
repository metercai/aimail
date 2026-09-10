"""start_polling behavior baseline (open-application plan §2.5 consumption).

MockClient pins the polling loop contract:
1. dedup by delivery id (re-pulled mail is not delivered twice),
2. on_email failure leaves the id un-acked (mail re-pulls; nothing lost),
3. pull_list errors count and back off without crashing,
4. ack is called exactly with the delivered ids.
"""
import sys

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import pytest

from aimail_tools import _GatewayClient


class MockPollClient:
    """Scripted pull_list/pull_ack — no network.

    ``batches_per_round`` is a list of ROUNDS; each round is a LIST of
    batch dicts (gateway shape: {success, batches: [{body, deliveries}]}).
    """

    def __init__(self, batches_per_round):
        self.batches_per_round = batches_per_round
        self.round = 0
        self.ack_calls = []
        self.fail_ack = False

    def pull_list(self, limit=20):
        if self.round >= len(self.batches_per_round):
            return {"success": True, "batches": []}
        batches = self.batches_per_round[self.round]
        self.round += 1
        return {"success": True, "batches": list(batches)}

    def pull_ack(self, ids):
        self.ack_calls.append(list(ids))
        if self.fail_ack:
            return {"success": False, "error": "ack failed"}
        return {"success": True, "acked": len(ids)}


def _client_with(batches_per_round):
    c = _GatewayClient("http://127.0.0.1:1", "")
    mock = MockPollClient(batches_per_round)
    c.pull_list = mock.pull_list      # type: ignore[method-assign]
    c.pull_ack = mock.pull_ack        # type: ignore[method-assign]
    return c, mock


def test_dedup_by_delivery_id():
    """The same delivery id in two rounds is delivered ONCE."""
    batch = {"body": {"subject": "hi"}, "deliveries": [{"id": 7, "email": "a@x"}]}
    c, mock = _client_with([[batch], [batch], []])
    got = []
    c.start_polling(got.append, interval=0, max_rounds=3)
    assert len(got) == 1 and got[0]["id"] == 7
    # acked on the first round only
    assert mock.ack_calls == [[7]]


def test_on_email_failure_not_acked():
    """A failing on_email leaves the id un-acked — nothing is lost."""
    batch = {"body": {"subject": "hi"}, "deliveries": [{"id": 9, "email": "a@x"}]}
    c, mock = _client_with([[batch], []])

    def boom(_mail):
        raise RuntimeError("handler down")

    stats = c.start_polling(boom, interval=0, max_rounds=2)
    assert mock.ack_calls == []  # nothing acked
    assert stats["errors"] == 1


def test_pull_list_error_counts_and_continues():
    """A failed pull_list round counts an error and the loop continues."""
    c, mock = _GatewayClient("http://127.0.0.1:1", ""), None
    mock = MockPollClient([])
    c.pull_list = lambda limit=20: {"success": False, "error": "down"}  # type: ignore[method-assign]
    c.pull_ack = mock.pull_ack  # type: ignore[method-assign]
    got = []
    stats = c.start_polling(got.append, interval=0, max_rounds=2)
    assert stats["errors"] == 2 and got == [] and mock.ack_calls == []


def test_body_string_is_parsed_to_json():
    """Batch body arrives as a JSON string (wire shape) — delivered parsed."""
    batch = {"body": "{\"subject\": \"wire\"}", "deliveries": [{"id": 3, "email": "a@x"}]}
    c, _ = _client_with([[batch], []])
    got = []
    c.start_polling(got.append, interval=0, max_rounds=1)
    assert got and got[0]["body"] == {"subject": "wire"}
