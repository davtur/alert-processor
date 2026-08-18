"""Sort incidents so the highest-priority alerts appear first."""

from __future__ import annotations

from datetime import timezone
from typing import Any

from app.db import parse_iso

SEVERITY_RANK = {
    "critical": 0,
    "error": 1,
    "warning": 2,
    "info": 3,
    "none": 4,
    "": 4,
}

RISK_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "": 3,
}


def _epoch(ts: str | None) -> float:
    parsed = parse_iso(ts)
    if not parsed:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def sort_key(item: dict[str, Any]) -> tuple:
    rec = item.get("recommendation") or {}
    severity = str(item.get("severity") or "").lower()
    risk = str(item.get("risk") or rec.get("risk") or "").lower()
    status = str(item.get("status") or "")
    firing = 0 if status == "firing" else 1
    return (
        SEVERITY_RANK.get(severity, 5),
        RISK_RANK.get(risk, 3),
        firing,
        -_epoch(item.get("updated_at")),
    )


def sort_incidents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=sort_key)
