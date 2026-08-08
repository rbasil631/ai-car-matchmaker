"""M6 tests: observability.

The local buffer is always on, so these run without Langfuse credentials. What
matters is that the two ranking steps appear as separate spans and that nothing
personal leaks into a trace.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import tracing
from app.agent import ScriptedAgent
from app.state import SessionStore


@pytest.fixture(autouse=True)
def _clean_buffer():
    tracing.clear()
    yield
    tracing.clear()


def _run_research(tmp_path, sid="t1"):
    store = SessionStore(tmp_path / "t.sqlite")
    agent = ScriptedAgent(store)
    for answer in ["", "rent", "goa trip", "SUV", "4000 per day",
                   "2026-09-10 to 2026-09-15"]:
        agent.respond(sid, answer)
    return store, agent


# ---- span recording ----------------------------------------------------------

def test_research_records_the_two_ranking_steps(tmp_path):
    _run_research(tmp_path)
    names = [s["name"] for s in tracing.get_trace("t1")]
    assert "search_listings" in names
    assert "shortlist_candidates" in names
    assert "agent_rank" in names
    # the deterministic step precedes the model step — the auditable order
    assert names.index("shortlist_candidates") < names.index("agent_rank")


def test_spans_carry_timing_and_shape(tmp_path):
    _run_research(tmp_path)
    for s in tracing.get_trace("t1"):
        assert s["duration_ms"] is not None and s["duration_ms"] >= 0
        assert s["session_id"] == "t1"
        assert s["kind"] in ("span", "tool", "generation")


def test_shortlist_span_shows_the_narrowing(tmp_path):
    _run_research(tmp_path)
    sp = next(s for s in tracing.get_trace("t1")
              if s["name"] == "shortlist_candidates")
    assert sp["output"]["considered"] >= sp["output"]["kept"]
    assert sp["output"]["kept"] == len(sp["output"]["top"])


def test_rank_span_records_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_AGENT_RANKING", "off")
    _run_research(tmp_path)
    sp = next(s for s in tracing.get_trace("t1") if s["name"] == "agent_rank")
    assert sp["output"]["fell_back"] is True
    assert sp["output"]["ranked"] == 0


def test_actions_are_traced(tmp_path):
    store, agent = _run_research(tmp_path)
    lid = store.get("t1")["research"]["shortlist"][0]["listing_id"]
    agent.handle_action("t1", {"name": "hold_car",
                               "context": {"listing_id": lid}})
    hold = next(s for s in tracing.get_trace("t1") if s["name"] == "hold_car")
    assert hold["input"]["listing_id"] == lid
    assert hold["kind"] == "tool"


def test_errors_are_recorded_and_reraised():
    with pytest.raises(ValueError):
        with tracing.span("e1", "boom"):
            raise ValueError("kaboom")
    sp = tracing.get_trace("e1")[0]
    assert "ValueError" in sp["error"] and "kaboom" in sp["error"]


# ---- redaction ---------------------------------------------------------------

def test_personal_fields_are_redacted():
    payload = {"form_data": {"name": "Riya", "phone": "9876500000"},
               "listing_id": "l-001"}
    out = tracing.redact(payload)
    assert out["form_data"] == "<redacted>"
    assert out["listing_id"] == "l-001"          # non-personal data survives


def test_redaction_reaches_nested_structures():
    out = tracing.redact({"outer": [{"name": "Riya", "ok": 1}]})
    assert out["outer"][0]["name"] == "<redacted>"
    assert out["outer"][0]["ok"] == 1


def test_no_personal_data_in_a_real_trace(tmp_path):
    store, agent = _run_research(tmp_path)
    state = store.get("t1")
    state["checkout"]["form_data"] = {"name": "Riya Menon", "phone": "9876500000"}
    store.save(state)
    lid = state["research"]["shortlist"][0]["listing_id"]
    agent.handle_action("t1", {"name": "hold_car", "context": {"listing_id": lid}})

    blob = json.dumps(tracing.get_trace("t1"))
    for banned in ("Riya", "9876500000"):
        assert banned not in blob


# ---- digest ------------------------------------------------------------------

def test_summary_explains_the_funnel(tmp_path):
    _run_research(tmp_path)
    summary = tracing.summarize("t1")
    assert summary["span_count"] >= 3
    assert summary["errors"] == []
    assert "narrowed" in summary["narrowing"]


def test_buffer_is_bounded():
    for i in range(tracing.MAX_SPANS_PER_SESSION + 25):
        with tracing.span("flood", f"s{i}"):
            pass
    assert len(tracing.get_trace("flood")) == tracing.MAX_SPANS_PER_SESSION


def test_langfuse_is_optional(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracing._langfuse_tried = False
    tracing._langfuse = None
    assert tracing.is_exporting() is False
    with tracing.span("nolf", "still-works") as sp:      # buffer still records
        sp["output"] = {"ok": True}
    assert tracing.get_trace("nolf")[0]["output"] == {"ok": True}


# ---- endpoint ----------------------------------------------------------------

def test_trace_endpoint_serves_spans(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_DB", str(tmp_path / "tr.sqlite"))
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/ws/tr-1") as ws:
        ws.receive_json()
        for answer in ["hi", "rent", "goa trip", "SUV", "4000 per day",
                       "2026-09-10 to 2026-09-15"]:
            ws.send_text(json.dumps({"type": "user_message", "text": answer}))
            while ws.receive_json()["type"] != "state":
                pass

    body = client.get("/trace/tr-1").json()
    assert body["summary"]["span_count"] >= 3
    assert [s["name"] for s in body["spans"]].count("search_listings") >= 1
    assert body["summary"]["exporting_to_langfuse"] in (True, False)
