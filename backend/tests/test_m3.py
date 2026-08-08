"""M3 tests: garage (hold/release), comparison, and the live compare surface."""
import json

import pytest
from fastapi.testclient import TestClient

from app import a2ui, garage, marketplace
from app.agent import ScriptedAgent
from app.state import SessionStore, new_session_state


def _interviewed(store, sid="g1", mode="rent", car_type="SUV",
                 budget="4000 per day", dates="2026-09-10 to 2026-09-15"):
    """Drive a session to a shortlist so the garage has something to hold."""
    agent = ScriptedAgent(store)
    for answer in ["", mode, "goa trip", car_type, budget, dates]:
        agent.respond(sid, answer)
    return agent, store.get(sid)


# ---- hold / release ----------------------------------------------------------

def test_hold_unknown_listing_errors():
    s = new_session_state()
    out = garage.hold_car(s, "l-does-not-exist")
    assert out["error"]["code"] == "unknown_listing"
    assert s["garage"]["held"] == []


def test_hold_is_idempotent_and_preserves_note():
    s = new_session_state()
    lid = marketplace.load_listings()[0]["listing_id"]
    garage.hold_car(s, lid, note="my reason")
    again = garage.hold_car(s, lid)
    assert again["already_held"] is True
    assert len(s["garage"]["held"]) == 1
    assert s["garage"]["held"][0]["note"] == "my reason"   # not wiped


def test_hold_accepts_a_replacement_note():
    s = new_session_state()
    lid = marketplace.load_listings()[0]["listing_id"]
    garage.hold_car(s, lid, note="first")
    garage.hold_car(s, lid, note="second")
    assert s["garage"]["held"][0]["note"] == "second"


def test_release_removes_from_held_and_compare(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    _, state = _interviewed(store)
    ids = [c["listing_id"] for c in state["research"]["shortlist"]][:2]
    for lid in ids:
        garage.hold_car(state, lid)
    garage.compare_cars(state, ids)
    assert state["garage"]["compare_ids"] == ids

    garage.release_car(state, ids[0])
    assert [h["listing_id"] for h in state["garage"]["held"]] == [ids[1]]
    assert state["garage"]["compare_ids"] == [ids[1]]   # cascade, no stale id


def test_release_unheld_errors():
    s = new_session_state()
    assert garage.release_car(s, "l-001")["error"]["code"] == "not_held"


def test_auto_note_is_derived_from_the_shortlist(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    _, state = _interviewed(store)
    lid = state["research"]["shortlist"][0]["listing_id"]
    garage.hold_car(state, lid)
    assert state["garage"]["held"][0]["note"]     # non-empty, explains the save


# ---- compare -----------------------------------------------------------------

def test_compare_rejects_bad_sizes(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    _, state = _interviewed(store)
    ids = [c["listing_id"] for c in state["research"]["shortlist"]]
    for lid in ids:
        garage.hold_car(state, lid)
    assert garage.compare_cars(state, ids[:1])["error"]["code"] == "bad_compare_size"
    assert garage.compare_cars(state, ids[:5])["error"]["code"] == "bad_compare_size"


def test_compare_requires_held_and_names_the_offender(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    _, state = _interviewed(store)
    ids = [c["listing_id"] for c in state["research"]["shortlist"]][:2]
    garage.hold_car(state, ids[0])
    out = garage.compare_cars(state, ids)
    assert out["error"]["code"] == "not_held"
    assert ids[1] in out["error"]["message"]


def test_compare_matrix_shape(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    _, state = _interviewed(store)
    state["intent"]["constraints"] = ["automatic"]
    ids = [c["listing_id"] for c in state["research"]["shortlist"]][:3]
    for lid in ids:
        garage.hold_car(state, lid)
    m = garage.compare_cars(state, ids)

    assert [c["listing_id"] for c in m["columns"]] == ids
    labels = [r["label"] for r in m["rows"]]
    for expected in ("Price", "Fuel", "Seats", "Availability",
                     "Your requirements", "Your note"):
        assert expected in labels
    assert all(len(r["values"]) == len(ids) for r in m["rows"])
    assert state["phase"] == "comparing"


def test_availability_cell_reflects_target_dates(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    _, state = _interviewed(store)
    ids = [c["listing_id"] for c in state["research"]["shortlist"]][:2]
    for lid in ids:
        garage.hold_car(state, lid)
    m = garage.compare_cars(state, ids)
    avail = next(r for r in m["rows"] if r["label"] == "Availability")
    assert all(v in ("covers your dates", "partial overlap", "not available then")
               for v in avail["values"])


# ---- live compare surface (acceptance check 3) --------------------------------

def _kinds(msgs):
    return [next(k for k in m if k != "version") for m in msgs]


def test_compare_surface_appears_and_disappears_live(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    agent, state = _interviewed(store)
    sid = "g1"
    ids = [c["listing_id"] for c in state["research"]["shortlist"]][:2]

    for lid in ids:
        agent.handle_action(sid, {"name": "hold_car", "context": {"listing_id": lid}})

    # one selected: no table yet
    r1 = agent.handle_action(sid, {"name": "toggle_compare",
                                   "context": {"listing_id": ids[0]}})
    assert "at least two" in r1.text
    assert _kinds(r1.a2ui_messages)[-1] == "deleteSurface"

    # two selected: table renders
    r2 = agent.handle_action(sid, {"name": "toggle_compare",
                                   "context": {"listing_id": ids[1]}})
    assert _kinds(r2.a2ui_messages)[-3:] == ["deleteSurface", "createSurface",
                                             "updateComponents"]
    assert store.get(sid)["garage"]["compare_ids"] == ids

    # deselect one: table goes away again
    r3 = agent.handle_action(sid, {"name": "toggle_compare",
                                   "context": {"listing_id": ids[0]}})
    assert _kinds(r3.a2ui_messages)[-1] == "deleteSurface"
    assert store.get(sid)["garage"]["compare_ids"] == [ids[1]]


def test_compare_caps_at_four(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    agent, state = _interviewed(store)
    sid = "g1"
    ids = [c["listing_id"] for c in state["research"]["shortlist"]][:5]
    assert len(ids) == 5, "need five shortlisted cars for this test"
    for lid in ids:
        agent.handle_action(sid, {"name": "hold_car", "context": {"listing_id": lid}})
    for lid in ids[:4]:
        agent.handle_action(sid, {"name": "toggle_compare",
                                  "context": {"listing_id": lid}})
    reply = agent.handle_action(sid, {"name": "toggle_compare",
                                      "context": {"listing_id": ids[4]}})
    assert "up to 4" in reply.text
    assert len(store.get(sid)["garage"]["compare_ids"]) == 4


def test_unknown_action_is_reported_not_crashed(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    agent, _ = _interviewed(store)
    assert "don't know" in agent.handle_action("g1", {"name": "explode"}).text


# ---- holds survive constraint revision (S2 / S3) ------------------------------

def test_holds_survive_a_new_search(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    agent, state = _interviewed(store)
    sid = "g1"
    lid = state["research"]["shortlist"][0]["listing_id"]
    agent.handle_action(sid, {"name": "hold_car", "context": {"listing_id": lid}})

    # user revises the budget and research re-runs
    state = store.get(sid)
    state["intent"]["budget"]["amount"] = 2500
    state = store.save(state)
    agent._research(sid, first_render=False)

    held = [h["listing_id"] for h in store.get(sid)["garage"]["held"]]
    assert lid in held, "revising constraints must not empty the garage"


# ---- catalogue reflects garage state -----------------------------------------

def test_hold_marks_the_card_as_held(tmp_path):
    store = SessionStore(tmp_path / "g.sqlite")
    agent, state = _interviewed(store)
    sid = "g1"
    lid = state["research"]["shortlist"][0]["listing_id"]
    reply = agent.handle_action(sid, {"name": "hold_car",
                                      "context": {"listing_id": lid}})
    comps = reply.a2ui_messages[-1]["updateComponents"]["components"]
    labels = [c["text"] for c in comps if c["id"].endswith("-hold-lbl")]
    assert "Held ✓" in labels


# ---- websocket end-to-end -----------------------------------------------------

def test_ws_action_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_DB", str(tmp_path / "ws.sqlite"))
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/ws/ws-m3") as ws:
        ws.receive_json()  # initial state
        for answer in ["hi", "rent", "goa trip", "SUV", "4000 per day",
                       "2026-09-10 to 2026-09-15"]:
            ws.send_text(json.dumps({"type": "user_message", "text": answer}))
            while True:
                f = ws.receive_json()
                if f["type"] == "state":
                    state = f["state"]
                    break

        lid = state["research"]["shortlist"][0]["listing_id"]
        ws.send_text(json.dumps({"type": "a2ui_action", "action": {
            "name": "hold_car", "surfaceId": "results",
            "sourceComponentId": "c0-hold",
            "timestamp": "2026-08-08T00:00:00Z",
            "context": {"listing_id": lid}}}))
        while True:
            f = ws.receive_json()
            if f["type"] == "state":
                assert [h["listing_id"] for h in f["state"]["garage"]["held"]] == [lid]
                break
