"""Session state store — the agent's memory (specs/001-car-matchmaker/data-model.md).

One JSON object per session, versioned on every write. SQLite-backed so the
skeleton survives restarts (acceptance check 4) without external services.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_INTENT_SLOTS = ("mode", "use_case", "car_type", "budget", "target_date")


def new_session_state(session_id: str | None = None) -> dict[str, Any]:
    """Blank session per data-model.md. Nulls in `intent` drive the interview."""
    return {
        "session_id": session_id or str(uuid.uuid4()),
        "version": 0,
        "phase": "interview",
        "updated_at": _now(),
        "intent": {
            "mode": None,
            "use_case": None,
            "car_type": None,
            "budget": {"amount": None, "period": "total", "currency": "INR"},
            "target_date": None,
            "constraints": [],
        },
        "interview": {"asked": [], "attempts": {}, "declined": [], "complete": False},
        "research": {"last_query": {}, "shortlist": [], "ranked": []},
        "garage": {"held": [], "compare_ids": []},
        "checkout": {
            "active_listing_id": None,
            "form_data": {},
            "payment_status": "none",
            "confirmation_id": None,
            "completed": [],
        },
    }


MAX_SLOT_ATTEMPTS = 2


def missing_slots(state: dict[str, Any]) -> list[str]:
    """Which required intent slots are still unfilled. This IS the interview logic.

    A slot the user has been asked about `MAX_SLOT_ATTEMPTS` times without a
    usable answer is treated as declined and stops being requested. Otherwise
    an unparseable reply traps the conversation on one question forever — and
    the downstream tools already handle a null field by simply not filtering
    on it, which is the right outcome for "no preference".
    """
    intent = state["intent"]
    interview = state.get("interview", {})
    declined = set(interview.get("declined", []))
    missing = []
    for slot in REQUIRED_INTENT_SLOTS:
        if slot in declined:
            continue
        value = intent[slot]
        if slot == "budget":
            if value.get("amount") is None:
                missing.append(slot)
        elif value is None:
            missing.append(slot)
    return missing


def record_attempt(state: dict[str, Any], slot: str) -> None:
    """Count an ask, and mark the slot declined once it has been asked enough."""
    interview = state["interview"]
    attempts = interview.setdefault("attempts", {})
    attempts[slot] = attempts.get(slot, 0) + 1
    if attempts[slot] >= MAX_SLOT_ATTEMPTS:
        declined = interview.setdefault("declined", [])
        if slot not in declined:
            declined.append(slot)


class StaleWriteError(Exception):
    """Raised when a write is based on a stale version (optimistic concurrency)."""


class SessionStore:
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            " session_id TEXT PRIMARY KEY,"
            " version INTEGER NOT NULL,"
            " state TEXT NOT NULL)"
        )
        self._conn.commit()

    def create(self, session_id: str | None = None) -> dict[str, Any]:
        state = new_session_state(session_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (session_id, version, state) VALUES (?, ?, ?)",
                (state["session_id"], 0, json.dumps(state)),
            )
            self._conn.commit()
        return state

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT state FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_or_create(self, session_id: str) -> dict[str, Any]:
        return self.get(session_id) or self.create(session_id)

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        """Optimistic-concurrency write: bumps version, rejects stale writers."""
        state = copy.deepcopy(state)
        expected = state["version"]
        state["version"] = expected + 1
        state["updated_at"] = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET version = ?, state = ? "
                "WHERE session_id = ? AND version = ?",
                (state["version"], json.dumps(state), state["session_id"], expected),
            )
            self._conn.commit()
        if cur.rowcount != 1:
            raise StaleWriteError(
                f"session {state['session_id']}: expected version {expected}"
            )
        return state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
