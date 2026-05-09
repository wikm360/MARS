"""
Wire protocol helpers: framing, JSON serialisation, and optional encryption.
All messages are JSON-encoded, optionally Fernet-encrypted, sent as UTF-8 text
frames over WebSocket.
"""
from __future__ import annotations
import json
import logging

from shared.models import Message
from shared.crypto import SessionCipher

log = logging.getLogger(__name__)


def encode_message(msg: Message, cipher: SessionCipher | None = None) -> str:
    """Serialise a Message to a wire string (optionally encrypted)."""
    raw = json.dumps(msg.to_dict(), ensure_ascii=False)
    if cipher:
        return cipher.encrypt_str(raw)
    return raw


def decode_message(wire: str, cipher: SessionCipher | None = None) -> Message | None:
    """Deserialise a wire string to a Message (optionally decrypted)."""
    try:
        text = cipher.decrypt_str(wire) if cipher else wire
        return Message.from_dict(json.loads(text))
    except Exception as exc:
        log.warning("Failed to decode message: %s", exc)
        return None
