"""M8 tests: research progress (req 11) and confirmation state (req 13)."""
import json

import pytest
from fastapi.testclient import TestClient

from app import a2ui, checkout, vehicle_parse as vp
from app.agent import ScriptedAgent
from app.state import SessionStore

ANSWERS = ["", "rent", "goa trip", "SUV", "5000 per day", "2026-09-10 to 2026-09-15"]


def _kind(msg):
    return next(k for k in msg if k != "version")


# ---- research progress surface (req 11) --------------------------------------

def test_progress_surface_is_built_once_then_updated():
    """Structure once, then data-model updates — the A2UI incremental pattern."""
    first = a2ui.research_progress_messages(
        {"search": "running", "score": "pending", "rank": "pending"}, True)
    assert [_kind(m) for m in first] == ["createSurface", "updateComponents",
                                         "updateDataModel"]
    later = a2ui.research_progress_messages(
        {"search": "done", "score": "running", "rank": "pending"}, False)
    assert [_kind(m) for m in later] == ["updateDataModel"]


def test_progress_marks_reflect_step_state():
    msgs = a2ui.research_progress_messages(
        {"search": "done", "score": "running", "rank": "pending"}, False,
        {"search": "17 in range"})
    value = msgs[0]["updateDataModel"]["value"]
    assert value["steps"]["search"] != value["steps"]["rank"]
    assert value["detail"]["search"] == "17 in range"


def test_research_emits_progress_in_order(tmp_path):
    store = SessionStore(tmp_path / "pr.sqlite")
    agent = ScriptedAgent(store)
    seen: list[dict] = []

    reply = None
    for answer in ANSWERS:
        reply = agent.respond("pr1", answer, emit=lambda f: seen.extend(f))

    updates = [m["updateDataModel"]["value"]["steps"]
               for m in seen
               if "updateDataModel" in m
               and m["updateDataModel"]["surfaceId"] == a2ui.RESEARCH_SURFACE]
    assert len(updates) >= 3, "each step should tick"

    # search completes before scoring, scoring before ranking
    done_marks = [sum(1 for v in u.values() if v == a2ui._STEP_MARKS["done"])
                  for u in updates]
    assert done_marks == sorted(done_marks), "steps must only ever progress"
    assert done_marks[-1] == 3, "all three finish"


def test_progress_is_cleared_when_results_arrive(tmp_path):
    store = SessionStore(tmp_path / "pr.sqlite")
    agent = ScriptedAgent(store)
    reply = None
    for answer in ANSWERS:
        reply = agent.respond("pr2", answer)
    kinds = [(_kind(m), m[_kind(m)].get("surfaceId")) for m in reply.a2ui_messages]
    assert ("deleteSurface", a2ui.RESEARCH_SURFACE) in kinds
    # and the catalogue is built after the ticker is removed
    idx_clear = kinds.index(("deleteSurface", a2ui.RESEARCH_SURFACE))
    idx_results = kinds.index(("createSurface", a2ui.RESULTS_SURFACE))
    assert idx_clear < idx_results


def test_progress_still_delivered_without_an_emit_callback(tmp_path):
    """Offline/test path batches the same frames rather than dropping them."""
    store = SessionStore(tmp_path / "pr.sqlite")
    agent = ScriptedAgent(store)
    reply = None
    for answer in ANSWERS:
        reply = agent.respond("pr3", answer)
    surfaces = [m[_kind(m)].get("surfaceId") for m in reply.a2ui_messages]
    assert a2ui.RESEARCH_SURFACE in surfaces


# ---- confirmation surface (req 13) -------------------------------------------

def test_confirmation_surface_shape():
    msgs = a2ui.confirmation_messages({
        "title": "Mahindra XUV700 (2024)", "confirmation_id": "c-abc12345",
        "amount": "\u20b914,000", "when": "2026-09-10 \u2192 2026-09-15", "location": "Goa"})
    assert [_kind(m) for m in msgs] == ["deleteSurface", "createSurface",
                                        "updateComponents"]
    texts = [c.get("text") for c in msgs[-1]["updateComponents"]["components"]]
    assert "Booking confirmed" in texts
    assert "c-abc12345" in texts
    assert "\u20b914,000" in texts
    assert any("no payment was taken" in str(t) for t in texts)


def test_payment_renders_the_confirmation_surface(tmp_path):
    store = SessionStore(tmp_path / "cf.sqlite")
    agent = ScriptedAgent(store)
    for answer in ANSWERS:
        agent.respond("cf1", answer)
    state = store.get("cf1")
    lid = state["research"]["shortlist"][0]["listing_id"]
    agent.handle_action("cf1", {"name": "hold_car", "context": {"listing_id": lid}})

    state = store.get("cf1")
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    result = checkout.submit_payment(state, "4242")
    store.save(state)

    reply = agent.after_payment("cf1", result)
    surfaces = [m[_kind(m)].get("surfaceId") for m in reply.a2ui_messages]
    assert a2ui.CONFIRMATION_SURFACE in surfaces
    comps = next(m["updateComponents"]["components"] for m in reply.a2ui_messages
                 if "updateComponents" in m
                 and m["updateComponents"]["surfaceId"] == a2ui.CONFIRMATION_SURFACE)
    texts = [c.get("text") for c in comps]
    assert result["confirmation_id"] in texts
    assert "2026-09-10 \u2192 2026-09-15" in texts     # dates carried from the interview


def test_declined_payment_renders_no_confirmation(tmp_path):
    store = SessionStore(tmp_path / "cf.sqlite")
    agent = ScriptedAgent(store)
    reply = agent.after_payment("cf2", {"status": "declined"})
    assert reply.a2ui_messages == []
    assert "declined" in reply.text.lower()


# ---- natural seat phrasing ----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("goa trip with 7 people", "seats>=7"),
    ("6 of us", "seats>=6"),
    ("for four passengers", "seats>=4"),
    ("space for 5", "seats>=5"),
])
def test_seat_counts_from_natural_phrasing(text, expected):
    assert expected in vp.parse_constraints(text)


def test_stray_numbers_are_not_seat_counts():
    assert not any(c.startswith("seats") for c in vp.parse_constraints("trip in 2026"))


# ---- websocket streaming ------------------------------------------------------

def test_progress_frames_stream_before_the_final_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_DB", str(tmp_path / "st.sqlite"))
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/ws/st-1") as ws:
        ws.receive_json()
        order: list[str] = []
        for answer in ANSWERS:
            ws.send_text(json.dumps({"type": "user_message", "text": answer}))
            while True:
                f = ws.receive_json()
                if f["type"] == "a2ui":
                    msg = f["message"]
                    sid = msg[_kind(msg)].get("surfaceId")
                    if sid == a2ui.RESEARCH_SURFACE:
                        order.append(_kind(msg))
                if f["type"] == "state":
                    break
        assert "createSurface" in order and "updateDataModel" in order
        assert order.count("updateDataModel") >= 3
