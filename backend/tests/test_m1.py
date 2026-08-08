"""M1 walking-skeleton tests.

Covers: state versioning + optimistic concurrency, nulls-drive-the-interview,
A2UI envelope validity (against the real v0.9.1 spec schemas when available),
and the websocket chat + MCP App round trip end-to-end.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import a2ui
from app.agent import ScriptedAgent, _parse_amount
from app.state import SessionStore, StaleWriteError, missing_slots, new_session_state


# ---- state -------------------------------------------------------------------

def test_versioning_and_persistence(tmp_path):
    store = SessionStore(tmp_path / "t.sqlite")
    s = store.create("s1")
    assert s["version"] == 0
    s["intent"]["mode"] = "rent"
    s = store.save(s)
    assert s["version"] == 1
    # survives a fresh store on the same file (acceptance check 4)
    store2 = SessionStore(tmp_path / "t.sqlite")
    assert store2.get("s1")["intent"]["mode"] == "rent"


def test_stale_write_rejected(tmp_path):
    store = SessionStore(tmp_path / "t.sqlite")
    s = store.create("s1")
    stale = json.loads(json.dumps(s))
    store.save(s)
    with pytest.raises(StaleWriteError):
        store.save(stale)


def test_missing_slots_drive_interview():
    s = new_session_state()
    assert missing_slots(s) == ["mode", "use_case", "car_type", "budget", "target_date"]
    s["intent"]["mode"] = "buy"
    s["intent"]["budget"]["amount"] = 1_500_000
    assert missing_slots(s) == ["use_case", "car_type", "target_date"]


# ---- scripted interview --------------------------------------------------------

def test_full_interview_reaches_researching(tmp_path):
    store = SessionStore(tmp_path / "t.sqlite")
    agent = ScriptedAgent(store)
    sid = "s1"
    answers = ["", "rent please", "goa trip with friends", "an SUV",
               "3500 per day", "2026-09-10 to 2026-09-15"]
    reply = None
    for a in answers:
        reply = agent.respond(sid, a)
    state = store.get(sid)
    assert state["interview"]["complete"] is True
    assert state["phase"] == "researching"
    assert state["intent"]["mode"] == "rent"
    assert state["intent"]["budget"] == {"amount": 3500, "period": "per_day", "currency": "INR"}
    assert state["intent"]["target_date"] == {"from": "2026-09-10", "to": "2026-09-15"}
    assert reply.a2ui_messages, "every turn updates the progress surface"


def test_amount_parsing():
    assert _parse_amount("15 lakh") == 1_500_000
    assert _parse_amount("1.2 crore") == 12_000_000
    assert _parse_amount("3,500") == 3500
    assert _parse_amount("around 20k") == 20_000
    assert _parse_amount("no idea") is None


# ---- A2UI envelopes ------------------------------------------------------------

def test_envelope_shapes():
    msgs = a2ui.interview_progress_messages(new_session_state(), first_time=True)
    kinds = [next(k for k in m if k != "version") for m in msgs]
    assert kinds == ["createSurface", "updateComponents", "updateDataModel"]
    for m in msgs:
        assert m["version"] == "v0.9.1"
    create = msgs[0]["createSurface"]
    assert {"surfaceId", "catalogId"} <= set(create)  # both REQUIRED by spec
    comps = msgs[1]["updateComponents"]["components"]
    assert all("id" in c and "component" in c for c in comps)


SPEC = Path(__file__).resolve().parents[2] / "research" / "a2ui-schemas"


@pytest.mark.skipif(not (SPEC / "server_to_client.json").exists(),
                    reason="A2UI spec schemas not vendored")
def test_envelopes_validate_against_real_spec():
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    registry = Registry()
    for f in SPEC.glob("*.json"):
        schema = json.loads(f.read_text())
        res = Resource.from_contents(schema, default_specification=DRAFT202012)
        registry = registry.with_resource(f.name, res)
        registry = registry.with_resource(
            f"https://a2ui.org/specification/v0_9/{f.name}", res
        )
    main = json.loads((SPEC / "server_to_client.json").read_text())
    validator = jsonschema.Draft202012Validator(main, registry=registry)

    messages = list(a2ui.interview_progress_messages(new_session_state(), first_time=True))
    # the catalogue surface must be spec-valid too, not just the interview one
    messages += a2ui.results_messages(
        [{"listing_id": "l-001", "title": "Tata Nexon (2024)", "price": "\u20b91,200,000",
          "meta": "Compact SUV \u00b7 petrol \u00b7 automatic \u00b7 5 seats \u00b7 Delhi",
          "why": "Why: uses your budget well"}],
        considered=12,
    )
    # garage + compare surfaces must be spec-valid too (weight, nested Row/Column)
    messages += a2ui.garage_messages([
        {"listing_id": "l-001", "title": "Tata Nexon (2024)", "price": "\u20b91,200,000",
         "note": "cheapest on the shortlist", "in_compare": True},
    ])
    messages += a2ui.compare_messages({
        "columns": [{"listing_id": "l-001", "title": "Tata Nexon"},
                    {"listing_id": "l-002", "title": "Kia Sonet"}],
        "rows": [{"label": "Price", "values": ["\u20b91,200,000", "\u20b91,100,000"]},
                 {"label": "Seats", "values": ["5", "5"]}],
    })
    messages += a2ui.clear_compare_messages()
    for m in messages:
        validator.validate(m)


# ---- end-to-end over the websocket ---------------------------------------------

def test_ws_chat_and_mcp_app_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_DB", str(tmp_path / "e2e.sqlite"))
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/ws/e2e-1") as ws:
        assert ws.receive_json()["type"] == "state"

        ws.send_text(json.dumps({"type": "user_message", "text": "hello"}))
        frames = [ws.receive_json() for _ in range(5)]
        types = [f["type"] for f in frames]
        assert types.count("a2ui") == 3 and "agent_text" in types

        # MCP App path: open form, submit, verify state written
        ws.send_text(json.dumps({"type": "user_message", "text": "/book demo-listing"}))
        app_frame = ws.receive_json()
        assert app_frame["type"] == "mcp_app"
        assert app_frame["resource_uri"] == "ui://car-matchmaker/booking-form"
        ws.receive_json()  # agent_text

        ws.send_text(json.dumps({"type": "mcp_tool_call", "request": {
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "submit_booking_form", "arguments": {
                "session_id": "e2e-1", "listing_id": "l-demo",
                "form_data": {"name": "Riya", "phone": "9", "date": "2026-09-10"}}}}}))
        result = ws.receive_json()
        assert result["type"] == "mcp_tool_result"
        assert result["response"]["result"]["ok"] is True
        ws.receive_json()  # agent_text
        state = ws.receive_json()["state"]
        assert state["checkout"]["form_data"]["name"] == "Riya"
        assert state["phase"] == "booking"

    # tool descriptor uses the NON-deprecated linkage key
    tools = client.get("/mcp/tools").json()["tools"]
    assert tools[0]["_meta"]["ui"]["resourceUri"] == "ui://car-matchmaker/booking-form"
    assert "ui/resourceUri" not in tools[0]["_meta"]

    res = client.get("/mcp/resource", params={"uri": "ui://car-matchmaker/booking-form"}).json()
    assert res["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
