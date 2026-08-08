"""M4 tests: booking form, mock payment, confirmation, and the decline path."""
import json

import pytest
from fastapi.testclient import TestClient

from app import a2ui, checkout, garage, mcp_app
from app.agent import ScriptedAgent
from app.state import SessionStore


def _ready(store, sid="c1"):
    """Interview -> shortlist -> hold the top car, so checkout has a subject."""
    agent = ScriptedAgent(store)
    for answer in ["", "rent", "goa trip", "SUV", "4000 per day",
                   "2026-09-10 to 2026-09-15"]:
        agent.respond(sid, answer)
    state = store.get(sid)
    lid = state["research"]["shortlist"][0]["listing_id"]
    agent.handle_action(sid, {"name": "hold_car", "context": {"listing_id": lid}})
    return agent, store.get(sid), lid


# ---- booking form (contract §5) ----------------------------------------------

def test_booking_form_requires_a_held_car(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, _ = _ready(store)
    out = checkout.open_booking_form(state, "l-001-not-held")
    assert out["error"]["code"] == "not_held"


def test_booking_form_prefills_from_session_state(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    p = checkout.open_booking_form(state, lid)
    assert p["mode"] == "rent"
    assert p["prefill_from"] == "2026-09-10"      # from intent.target_date
    assert p["prefill_to"] == "2026-09-15"
    assert p["listing_id"] == lid and p["summary"]


def test_booking_form_rejects_incomplete_submissions(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    out = checkout.submit_booking_form(state, lid, {"name": "", "phone": ""})
    assert out["error"]["code"] == "incomplete_form"
    assert state["checkout"]["active_listing_id"] is None


def test_booking_form_submit_sets_active_checkout(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "99999"})
    assert state["checkout"]["active_listing_id"] == lid
    assert state["checkout"]["payment_status"] == "pending"
    assert state["phase"] == "booking"


def test_second_checkout_blocked_while_one_is_pending(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    agent, state, lid = _ready(store)
    other = state["research"]["shortlist"][1]["listing_id"]
    garage.hold_car(state, other)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    out = checkout.open_booking_form(state, other)
    assert out["error"]["code"] == "checkout_in_progress"


# ---- pricing -----------------------------------------------------------------

def test_rental_quote_multiplies_by_days(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    from app import marketplace
    listing = marketplace.listing_by_id(lid)
    total, breakdown = checkout.quote(state, listing)
    assert total == listing["price"]["amount"] * 5      # 10 Sep -> 15 Sep
    assert "× 5 days" in breakdown


def test_purchase_quote_is_the_sticker_price(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    agent = ScriptedAgent(store)
    for answer in ["", "buy", "commute", "Hatchback", "8 lakh", "2026-09-10"]:
        agent.respond("b1", answer)
    state = store.get("b1")
    from app import marketplace
    listing = marketplace.listing_by_id(state["research"]["shortlist"][0]["listing_id"])
    total, breakdown = checkout.quote(state, listing)
    assert total == listing["price"]["amount"]
    assert breakdown == "purchase total"


# ---- payment (contract §6) ----------------------------------------------------

def test_payment_requires_booking_details(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, _ = _ready(store)
    assert checkout.open_payment(state)["error"]["code"] == "no_booking"


def test_card_ending_0000_declines_and_keeps_the_booking(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    out = checkout.submit_payment(state, "0000")
    assert out["status"] == "declined"
    assert state["checkout"]["completed"] == []
    # the booking survives so the user can retry in place
    assert state["checkout"]["active_listing_id"] == lid
    assert state["checkout"]["payment_status"] == "pending"


def test_retry_after_decline_succeeds(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    checkout.submit_payment(state, "0000")
    out = checkout.submit_payment(state, "4242")
    assert out["status"] == "confirmed"
    assert out["confirmation_id"].startswith("c-")


def test_successful_payment_records_the_booking(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    out = checkout.submit_payment(state, "4242")

    c = state["checkout"]
    assert c["payment_status"] == "confirmed"
    assert c["confirmation_id"] == out["confirmation_id"]
    assert [b["listing_id"] for b in c["completed"]] == [lid]
    assert c["active_listing_id"] is None          # cleared for the next car
    assert c["form_data"] == {}


def test_phase_returns_to_comparing_when_cars_remain_held(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    garage.hold_car(state, state["research"]["shortlist"][1]["listing_id"])
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    checkout.submit_payment(state, "4242")
    assert state["phase"] == "comparing"


def test_phase_is_done_when_nothing_is_left_to_book(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    checkout.submit_payment(state, "4242")
    assert state["phase"] == "done"


def test_booked_car_cannot_be_booked_twice(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    checkout.submit_payment(state, "4242")
    assert checkout.open_booking_form(state, lid)["error"]["code"] == "already_booked"


# ---- card data never reaches the server --------------------------------------

def test_payment_template_sends_only_last_four_digits():
    html = mcp_app.PAYMENT_HTML
    assert "digits.slice(-4)" in html
    assert "card_last4" in html
    # the CVV and expiry inputs exist for realism but are never posted
    for never_sent in ('arguments: { session_id: sessionId, card_number',
                       '"cvv":', 'cvv:', 'expiry:'):
        assert never_sent not in html


def test_session_state_never_holds_card_fields(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    _, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    checkout.submit_payment(state, "4242")
    blob = json.dumps(state).lower()
    for banned in ("cvv", "card_number", "cardnumber", "4242"):
        assert banned not in blob


# ---- garage badging -----------------------------------------------------------

def test_booked_car_is_badged_and_loses_its_book_button(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    agent, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    result = checkout.submit_payment(state, "4242")
    store.save(state)

    reply = agent.after_payment("c1", result)
    garage_update = next(
        m["updateComponents"] for m in reply.a2ui_messages
        if "updateComponents" in m
        and m["updateComponents"]["surfaceId"] == a2ui.GARAGE_SURFACE)
    comps = garage_update["components"]
    texts = [c.get("text") for c in comps if c.get("component") == "Text"]
    assert any(str(t).startswith("Booked ✓") for t in texts)
    assert "Book this" not in texts
    assert result["confirmation_id"] in " ".join(str(t) for t in texts)


# ---- agent-driven flow --------------------------------------------------------

def test_book_action_returns_the_booking_form_app(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    agent, _, lid = _ready(store)
    reply = agent.handle_action("c1", {"name": "book_car",
                                       "context": {"listing_id": lid}})
    assert reply.mcp_app["resource_uri"] == "ui://car-matchmaker/booking-form"
    assert reply.mcp_app["tool_result"]["listing_id"] == lid
    assert "<!doctype html>" in reply.mcp_app["html"].lower()


def test_booking_form_hands_off_to_payment(tmp_path):
    store = SessionStore(tmp_path / "c.sqlite")
    agent, state, lid = _ready(store)
    checkout.submit_booking_form(state, lid, {"name": "Riya", "phone": "9"})
    store.save(state)
    reply = agent.after_booking_form("c1")
    assert reply.mcp_app["resource_uri"] == "ui://car-matchmaker/mock-payment"
    assert reply.mcp_app["tool_result"]["amount_label"].startswith("₹")


# ---- full websocket checkout ---------------------------------------------------

def test_ws_full_checkout_including_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_DB", str(tmp_path / "ws4.sqlite"))
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/ws/ws-m4") as ws:
        ws.receive_json()

        def turn(frame):
            ws.send_text(json.dumps(frame))
            got = []
            while True:
                f = ws.receive_json()
                got.append(f)
                if f["type"] == "state":
                    return got

        for answer in ["hi", "rent", "goa trip", "SUV", "4000 per day",
                       "2026-09-10 to 2026-09-15"]:
            frames = turn({"type": "user_message", "text": answer})
        lid = frames[-1]["state"]["research"]["shortlist"][0]["listing_id"]

        def action(name):
            return turn({"type": "a2ui_action", "action": {
                "name": name, "surfaceId": "results", "sourceComponentId": "x",
                "timestamp": "2026-08-08T00:00:00Z",
                "context": {"listing_id": lid}}})

        action("hold_car")
        frames = action("book_car")
        app_frame = next(f for f in frames if f["type"] == "mcp_app")
        assert app_frame["resource_uri"] == "ui://car-matchmaker/booking-form"

        # submit booking -> payment app is mounted automatically
        frames = turn({"type": "mcp_tool_call", "request": {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "submit_booking_form", "arguments": {
                "session_id": "ws-m4", "listing_id": lid,
                "form_data": {"name": "Riya", "phone": "99999",
                              "field_1": "2026-09-10", "field_2": "2026-09-15",
                              "field_3": "Goa"}}}}})
        pay_frame = next(f for f in frames if f["type"] == "mcp_app")
        assert pay_frame["resource_uri"] == "ui://car-matchmaker/mock-payment"

        # declined card
        frames = turn({"type": "mcp_tool_call", "request": {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "submit_payment", "arguments": {
                "session_id": "ws-m4", "card_last4": "0000"}}}})
        res = next(f for f in frames if f["type"] == "mcp_tool_result")
        assert res["response"]["result"]["status"] == "declined"
        assert frames[-1]["state"]["checkout"]["completed"] == []

        # retry succeeds
        frames = turn({"type": "mcp_tool_call", "request": {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "submit_payment", "arguments": {
                "session_id": "ws-m4", "card_last4": "4242"}}}})
        res = next(f for f in frames if f["type"] == "mcp_tool_result")
        assert res["response"]["result"]["status"] == "confirmed"
        final = frames[-1]["state"]
        assert len(final["checkout"]["completed"]) == 1
        assert final["phase"] == "done"


def test_ws_rejects_unknown_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("CARMATCH_DB", str(tmp_path / "ws4b.sqlite"))
    import importlib
    from app import main as main_mod
    importlib.reload(main_mod)

    client = TestClient(main_mod.app)
    with client.websocket_connect("/ws/ws-m4b") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "mcp_tool_call", "request": {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "definitely_not_a_tool", "arguments": {}}}}))
        res = ws.receive_json()
        assert res["response"]["error"]["code"] == -32601
