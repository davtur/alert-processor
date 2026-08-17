"""HMAC-signed email tokens and session cookies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app import config


class TokenError(ValueError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload: bytes) -> str:
    digest = hmac.new(
        config.SIGNING_SECRET.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    return _b64url(digest)


def encode(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64url(raw)}.{_sign(raw)}"


def decode(token: str) -> dict[str, Any]:
    try:
        blob, signature = token.split(".", 1)
    except ValueError as exc:
        raise TokenError("malformed token") from exc
    raw = _b64url_decode(blob)
    expected = _sign(raw)
    if not hmac.compare_digest(signature, expected):
        raise TokenError("invalid signature")
    payload = json.loads(raw.decode("utf-8"))
    exp = int(payload.get("exp", 0))
    if exp and exp < int(time.time()):
        raise TokenError("token expired")
    return payload


def make_action_token(incident_id: int, action: str) -> str:
    return encode(
        {
            "id": incident_id,
            "act": action,
            "exp": int(time.time()) + config.TOKEN_TTL_SECONDS,
        }
    )


def make_session_token() -> str:
    return encode({"v": 1, "exp": int(time.time()) + config.SESSION_TTL_SECONDS})


def is_session_token(token: str) -> bool:
    try:
        payload = decode(token)
    except TokenError:
        return False
    return payload.get("v") == 1
