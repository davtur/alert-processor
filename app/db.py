"""SQLite persistence for incidents and audit events."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from app import config

_lock = threading.Lock()
_initialized = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    global _initialized
    with _lock:
        if _initialized:
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    group_key TEXT,
                    status TEXT NOT NULL,
                    alertname TEXT,
                    namespace TEXT,
                    severity TEXT,
                    payload_json TEXT NOT NULL,
                    recommendation_json TEXT,
                    action_result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_notified_at TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id INTEGER,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
                CREATE INDEX IF NOT EXISTS idx_incidents_updated ON incidents(updated_at);
                """
            )
            conn.commit()
        finally:
            conn.close()
        _initialized = True


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("payload_json", "recommendation_json"):
        raw = item.get(key)
        if raw:
            try:
                item[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                item[key.replace("_json", "")] = None
        else:
            item[key.replace("_json", "")] = None
    return item


def get_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM incidents WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_by_id(incident_id: int) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_incidents(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM incidents WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM incidents ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows if r is not None]
    finally:
        conn.close()


def upsert_incident(
    *,
    fingerprint: str,
    group_key: str,
    status: str,
    alertname: str,
    namespace: str,
    severity: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    payload_json = json.dumps(payload)
    with _lock:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT * FROM incidents WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE incidents
                    SET group_key = ?, status = ?, alertname = ?, namespace = ?,
                        severity = ?, payload_json = ?, updated_at = ?
                    WHERE fingerprint = ?
                    """,
                    (
                        group_key,
                        status,
                        alertname,
                        namespace,
                        severity,
                        payload_json,
                        now,
                        fingerprint,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO incidents (
                        fingerprint, group_key, status, alertname, namespace,
                        severity, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        group_key,
                        status,
                        alertname,
                        namespace,
                        severity,
                        payload_json,
                        now,
                        now,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM incidents WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            return _row_to_dict(row) or {}
        finally:
            conn.close()


def save_recommendation(incident_id: int, recommendation: dict[str, Any]) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE incidents
                SET recommendation_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(recommendation), _now(), incident_id),
            )
            conn.commit()
        finally:
            conn.close()


def mark_notified(incident_id: int) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE incidents SET last_notified_at = ?, updated_at = ? WHERE id = ?",
                (_now(), _now(), incident_id),
            )
            conn.commit()
        finally:
            conn.close()


def set_status(incident_id: int, status: str, action_result: str | None = None) -> None:
    with _lock:
        conn = _connect()
        try:
            if action_result is None:
                conn.execute(
                    "UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), incident_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE incidents
                    SET status = ?, action_result = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, action_result, _now(), incident_id),
                )
            conn.commit()
        finally:
            conn.close()


def add_audit(incident_id: int | None, action: str, actor: str, detail: str = "") -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO audit_log (incident_id, action, actor, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (incident_id, action, actor, detail, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None
