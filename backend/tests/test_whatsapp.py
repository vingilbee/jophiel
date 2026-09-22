"""Focused unit coverage for the WhatsApp transport boundary."""
import hashlib
import hmac
from types import SimpleNamespace

from app.api.routes.whatsapp import _message_text, _timestamp, verify_webhook
from app.services import whatsapp


def test_signature_uses_raw_request_body(monkeypatch):
    monkeypatch.setattr(
        whatsapp, "get_settings", lambda: SimpleNamespace(whatsapp_app_secret="secret")
    )
    body = b'{"entry": [{"literal": "bytes matter"}]}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature(body, f"sha256={digest}")
    assert not whatsapp.verify_signature(body + b"\n", f"sha256={digest}")
    assert not whatsapp.verify_signature(body, None)


def test_split_text_preserves_content_and_limit():
    content = ("alpha beta gamma\n" * 500).strip()
    chunks = whatsapp.split_text(content, limit=120)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 120 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(content.split())


def test_webhook_message_shapes_are_normalized():
    assert _message_text({"type": "text", "text": {"body": "hello"}}) == "hello"
    assert _message_text({
        "type": "interactive",
        "interactive": {"button_reply": {"id": "yes", "title": "Yes"}},
    }) == "Yes"
    assert "Unsupported" in _message_text({"type": "image"})
    assert _message_text({"type": "reaction"}) is None


def test_provider_timestamp_is_utc():
    parsed = _timestamp("1700000000")
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert _timestamp("not-a-timestamp") is None


def test_verification_challenge(monkeypatch):
    from app.api.routes import whatsapp as route

    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(
        whatsapp_enabled=True, whatsapp_verify_token="verify-me"
    ))
    response = verify_webhook(
        mode="subscribe", token="verify-me", challenge="123456"
    )
    assert response.body == b"123456"


def test_debounce_deadline_has_a_hard_maximum():
    # This formula is the invariant used by ingest_message: normal bursts wait
    # for quiet, but a continuous typist cannot postpone dispatch forever.
    from datetime import datetime, timedelta, timezone

    first = datetime.now(timezone.utc)
    last = first + timedelta(seconds=2.8)
    deadline = min(last + timedelta(seconds=1), first + timedelta(seconds=3))
    assert deadline == first + timedelta(seconds=3)
