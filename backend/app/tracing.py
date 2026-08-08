"""Observability (plan §6).

Two layers, deliberately:

1. An **always-on in-memory buffer**. Every tool call and agent turn is recorded
   locally and served at `GET /trace/{session_id}`. This costs nothing, needs no
   account, and means a judge can see "code narrowed 388→6, model ranked those 6"
   as two separate spans without signing up for anything.
2. **Optional Langfuse export**, enabled by setting LANGFUSE_PUBLIC_KEY and
   LANGFUSE_SECRET_KEY. Same spans, shipped to a hosted UI.

The buffer is bounded — an unbounded trace log in a long demo session is a slow
memory leak, and nobody reads the 400th span anyway.

Redaction is enforced here rather than at each call site: `checkout.form_data`
carries the customer's name and phone, so it is stripped from every span before
it is recorded. Card data never reaches this process at all (see mcp_app).
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger(__name__)

MAX_SPANS_PER_SESSION = 200
REDACTED_KEYS = {"form_data", "name", "phone", "card", "card_last4", "cvv"}

_buffers: dict[str, deque] = {}
_langfuse = None
_langfuse_tried = False


# ---- redaction ---------------------------------------------------------------


def redact(value: Any) -> Any:
    """Strip personal fields from anything headed for a trace."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if k in REDACTED_KEYS else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# ---- Langfuse (optional) -----------------------------------------------------


def _client():
    """Lazily construct the Langfuse client; None when unconfigured."""
    global _langfuse, _langfuse_tried
    if _langfuse_tried:
        return _langfuse
    _langfuse_tried = True

    if not (os.environ.get("LANGFUSE_PUBLIC_KEY")
            and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        log.info("langfuse not installed — local trace buffer only")
        return None
    try:
        _langfuse = Langfuse()
        log.info("Langfuse tracing enabled")
    except Exception as exc:
        log.warning("Langfuse init failed (%s) — local trace buffer only",
                    type(exc).__name__)
        _langfuse = None
    return _langfuse


def is_exporting() -> bool:
    return _client() is not None


# ---- spans -------------------------------------------------------------------


@contextmanager
def span(session_id: str, name: str, *, kind: str = "span",
         input: Any = None, metadata: dict[str, Any] | None = None
         ) -> Iterator[dict[str, Any]]:
    """Record one unit of work.

    Yields a mutable record; assign to `record["output"]` inside the block and
    it lands in both the local buffer and Langfuse. Exceptions are recorded and
    re-raised — a span that silently swallows errors is worse than no span.
    """
    started = time.perf_counter()
    record: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "session_id": session_id,
        "input": redact(input),
        "output": None,
        "metadata": redact(metadata or {}),
        "started_at": time.time(),
        "duration_ms": None,
        "error": None,
    }

    client = _client()
    ctx = None
    if client is not None:
        try:
            ctx = client.start_as_current_observation(
                name=name, as_type=kind if kind in ("tool", "generation") else "span",
                input=record["input"], metadata=record["metadata"])
            ctx.__enter__()
        except Exception as exc:                   # never let tracing break a demo
            log.debug("langfuse span failed to start: %s", exc)
            ctx = None

    try:
        yield record
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        record["output"] = redact(record["output"])
        _append(session_id, record)
        if ctx is not None:
            try:
                ctx.update(output=record["output"],
                           level="ERROR" if record["error"] else None,
                           status_message=record["error"])
                ctx.__exit__(None, None, None)
            except Exception as exc:
                log.debug("langfuse span failed to close: %s", exc)


def _append(session_id: str, record: dict[str, Any]) -> None:
    buf = _buffers.get(session_id)
    if buf is None:
        buf = _buffers[session_id] = deque(maxlen=MAX_SPANS_PER_SESSION)
    buf.append(record)


def get_trace(session_id: str) -> list[dict[str, Any]]:
    return list(_buffers.get(session_id, ()))


def clear(session_id: str | None = None) -> None:
    if session_id is None:
        _buffers.clear()
    else:
        _buffers.pop(session_id, None)


def summarize(session_id: str) -> dict[str, Any]:
    """A judge-readable digest: what ran, how long, and how the funnel narrowed.

    The narrowing line is the point of the whole exercise — it shows the
    deterministic step and the model step as separate, auditable moves.
    """
    spans = get_trace(session_id)
    narrowing = None
    for s in spans:
        if s["name"] == "shortlist_candidates" and s["output"]:
            considered = s["output"].get("considered")
            kept = s["output"].get("kept")
            if considered is not None and kept is not None:
                narrowing = f"code narrowed {considered}→{kept}"
    for s in spans:
        if s["name"] == "agent_rank" and s["output"]:
            n = s["output"].get("ranked")
            if n:
                narrowing = f"{narrowing}, model ranked those {n}" if narrowing \\
                    else f"model ranked {n}"
    return {
        "session_id": session_id,
        "span_count": len(spans),
        "total_ms": round(sum(s["duration_ms"] or 0 for s in spans), 2),
        "errors": [s["name"] for s in spans if s["error"]],
        "narrowing": narrowing,
        "exporting_to_langfuse": is_exporting(),
    }
