"""A2UI v0.9.1 server-to-client message builders.

Shapes verified against google/A2UI specification/v0_9_1/json/server_to_client.json
(fetched 2026-08-08 — see specs/001-car-matchmaker/research.md):

  - createSurface   {surfaceId, catalogId, theme?, sendDataModel?}
  - updateComponents{surfaceId, components: [...]}
  - updateDataModel {surfaceId, path?, value}
  - deleteSurface   {surfaceId}

Every envelope carries version "v0.9.1". Components are a FLAT list with id
references (not a nested tree); the discriminator property is `component`.
Basic catalog components used here: Text, Card, Column, Row, Button, Divider.
"""
from __future__ import annotations

from typing import Any

VERSION = "v0.9.1"
BASIC_CATALOG = "https://a2ui.org/specification/v0_9_1/catalogs/basic"

# ---- envelopes -------------------------------------------------------------


def create_surface(surface_id: str, send_data_model: bool = False) -> dict[str, Any]:
    return {
        "version": VERSION,
        "createSurface": {
            "surfaceId": surface_id,
            "catalogId": BASIC_CATALOG,
            "sendDataModel": send_data_model,
        },
    }


def update_components(surface_id: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "updateComponents": {"surfaceId": surface_id, "components": components},
    }


def update_data_model(surface_id: str, value: dict[str, Any], path: str = "/") -> dict[str, Any]:
    """Per spec: the payload key is `value` (replaces at `path`; omit to delete)."""
    return {
        "version": VERSION,
        "updateDataModel": {"surfaceId": surface_id, "path": path, "value": value},
    }


def delete_surface(surface_id: str) -> dict[str, Any]:
    return {"version": VERSION, "deleteSurface": {"surfaceId": surface_id}}


# ---- components (flat list entries) ----------------------------------------


def text(cid: str, value: Any, variant: str = "body",
         weight: float | None = None) -> dict[str, Any]:
    """`value` is a literal str or a data binding like {"path": "/x"}.
    variant per catalog enum: h1..h5 | caption | body."""
    c = {"id": cid, "component": "Text", "text": value, "variant": variant}
    if weight is not None:
        c["weight"] = weight       # only valid directly inside a Row/Column
    return c


def column(cid: str, children: list[str], weight: float | None = None) -> dict[str, Any]:
    c = {"id": cid, "component": "Column", "children": children}
    if weight is not None:
        c["weight"] = weight
    return c


def row(cid: str, children: list[str], weight: float | None = None) -> dict[str, Any]:
    c = {"id": cid, "component": "Row", "children": children}
    if weight is not None:
        c["weight"] = weight
    return c


def card(cid: str, child: str) -> dict[str, Any]:
    return {"id": cid, "component": "Card", "child": child}


def button(cid: str, child_id: str, action_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per catalog: Button requires `child` (a component id, typically a Text)
    and `action`; there is no `label` property."""
    return {
        "id": cid,
        "component": "Button",
        "child": child_id,
        "action": {"event": {"name": action_name, "context": context or {}}},
    }


def divider(cid: str) -> dict[str, Any]:
    return {"id": cid, "component": "Divider"}


# ---- higher-level surfaces used by the agent --------------------------------

INTERVIEW_SURFACE = "interview-progress"

SLOT_LABELS = {
    "mode": "Buy or rent",
    "use_case": "Use case",
    "car_type": "Car type",
    "budget": "Budget",
    "target_date": "Target date",
}


def interview_progress_messages(state: dict[str, Any], first_time: bool) -> list[dict[str, Any]]:
    """The FR-1 interview-progress surface: one chip row, filled slots shown live.

    Structure is created once; subsequent turns only send updateDataModel —
    exactly the incremental-update pattern A2UI is designed for.
    """
    msgs: list[dict[str, Any]] = []
    if first_time:
        chips: list[str] = []
        components: list[dict[str, Any]] = []
        for slot in SLOT_LABELS:
            label_id, value_id, col_id = f"lbl-{slot}", f"val-{slot}", f"col-{slot}"
            components.append(text(label_id, SLOT_LABELS[slot], variant="caption"))
            components.append(text(value_id, {"path": f"/slots/{slot}"}))
            components.append(column(col_id, [label_id, value_id]))
            chips.append(col_id)
        components.append(row("chips", chips))
        components.append(text("title", "Finding your car", variant="h3"))
        components.append(column("root", ["title", "chips"]))
        components.append(card("progress-card", "root"))
        msgs.append(create_surface(INTERVIEW_SURFACE))
        msgs.append(update_components(INTERVIEW_SURFACE, components))

    intent = state["intent"]
    slots = {
        "mode": intent["mode"] or "…",
        "use_case": intent["use_case"] or "…",
        "car_type": intent["car_type"] or _slot_placeholder(state, "car_type"),
        "budget": (
            f"₹{intent['budget']['amount']:,} {intent['budget']['period']}"
            if intent["budget"]["amount"]
            else "…"
        ),
        "target_date": _fmt_date(intent["target_date"]),
    }
    msgs.append(update_data_model(INTERVIEW_SURFACE, {"slots": slots}))
    return msgs


RESULTS_SURFACE = "results"


def results_messages(cards: list[dict[str, Any]], considered: int) -> list[dict[str, Any]]:
    """Catalogue surface (FR-8). Rebuilt per search: a revised query is a new
    result set, and deleteSurface + createSurface is the spec's way to say that.

    Each card is {listing_id, title, price, meta, why, held} — `why` is the score
    rationale, so the reasoning the user sees is the reasoning that ranked it.
    """
    components: list[dict[str, Any]] = []
    card_ids: list[str] = []
    for i, c in enumerate(cards):
        t, p, m, w = f"c{i}-title", f"c{i}-price", f"c{i}-meta", f"c{i}-why"
        components.append(text(t, c["title"], variant="h5"))
        components.append(text(p, c["price"], variant="body"))
        components.append(text(m, c["meta"], variant="caption"))
        components.append(text(w, c["why"], variant="caption"))
        # hold action: the card is the entry point to the garage (FR-4)
        lbl, btn = f"c{i}-hold-lbl", f"c{i}-hold"
        components.append(text(lbl, "Held ✓" if c.get("held") else "Hold"))
        components.append(button(btn, lbl, "hold_car",
                                 {"listing_id": c["listing_id"]}))
        components.append(column(f"c{i}-col", [t, p, m, w, btn]))
        components.append(card(f"c{i}", f"c{i}-col"))
        card_ids.append(f"c{i}")

    heading = (f"Top {len(cards)} of {considered} matches"
               if cards else "No matches — let's adjust the search")
    components.append(text("res-title", heading, variant="h3"))
    components.append(column("res-list", card_ids))
    components.append(column("res-root", ["res-title", "res-list"]))

    return [
        delete_surface(RESULTS_SURFACE),
        create_surface(RESULTS_SURFACE),
        update_components(RESULTS_SURFACE, components),
    ]


GARAGE_SURFACE = "garage"
COMPARE_SURFACE = "compare"


def garage_messages(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The held set (FR-4). Each entry is
    {listing_id, title, price, note, in_compare, booked?, confirmation_id?} and
    renders with book / compare / release actions. Rebuilt whenever the garage
    changes.
    """
    components: list[dict[str, Any]] = []
    rows: list[str] = []
    for i, e in enumerate(entries):
        t, p, n = f"g{i}-title", f"g{i}-price", f"g{i}-note"
        cmp_lbl, rel_lbl = f"g{i}-cmp-lbl", f"g{i}-rel-lbl"
        cmp_btn, rel_btn = f"g{i}-cmp", f"g{i}-rel"
        components.append(text(t, e["title"], variant="h5"))
        components.append(text(p, e["price"]))
        note = e["note"] or "—"
        if e.get("booked"):
            note = f"Booked ✓ · {e['confirmation_id']}"
        components.append(text(n, note, variant="caption"))
        components.append(column(f"g{i}-info", [t, p, n], weight=3))
        components.append(text(cmp_lbl,
                               "In comparison" if e["in_compare"] else "Compare"))
        components.append(button(cmp_btn, cmp_lbl, "toggle_compare",
                                 {"listing_id": e["listing_id"]}))
        components.append(text(rel_lbl, "Release"))
        components.append(button(rel_btn, rel_lbl, "release_car",
                                 {"listing_id": e["listing_id"]}))
        actions = [cmp_btn, rel_btn]
        # A booked car offers no Book button — the badge in its note says why.
        if not e.get("booked"):
            book_lbl, book_btn = f"g{i}-book-lbl", f"g{i}-book"
            components.append(text(book_lbl, "Book this"))
            components.append(button(book_btn, book_lbl, "book_car",
                                     {"listing_id": e["listing_id"]}))
            actions.insert(0, book_btn)
        components.append(column(f"g{i}-actions", actions, weight=1))
        components.append(row(f"g{i}", [f"g{i}-info", f"g{i}-actions"]))
        rows.append(f"g{i}")

    heading = (f"Your garage ({len(entries)})" if entries
               else "Your garage is empty")
    components.append(text("gar-title", heading, variant="h3"))
    components.append(column("gar-list", rows))
    components.append(column("gar-root", ["gar-title", "gar-list"]))
    return [
        delete_surface(GARAGE_SURFACE),
        create_surface(GARAGE_SURFACE),
        update_components(GARAGE_SURFACE, components),
    ]


def compare_messages(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """The comparison table (FR-4, acceptance check 3).

    The basic catalog has no Table component, so the grid is built the way the
    spec prescribes: a Column of Rows, each Row holding one label Column plus
    one Column per car. `weight` keeps the label column narrow.
    """
    columns = matrix["columns"]
    components: list[dict[str, Any]] = []

    header_cells = ["cmp-h-label"]
    components.append(text("cmp-h-label", "", variant="caption", weight=2))
    for j, col in enumerate(columns):
        cid = f"cmp-h{j}"
        components.append(text(cid, col["title"], variant="h5", weight=3))
        header_cells.append(cid)
    components.append(row("cmp-header", header_cells))

    row_ids = ["cmp-header"]
    for i, r in enumerate(matrix["rows"]):
        cells = [f"cmp-r{i}-label"]
        components.append(text(f"cmp-r{i}-label", r["label"],
                               variant="caption", weight=2))
        for j, value in enumerate(r["values"]):
            cid = f"cmp-r{i}c{j}"
            components.append(text(cid, value, weight=3))
            cells.append(cid)
        components.append(row(f"cmp-r{i}", cells))
        components.append(divider(f"cmp-d{i}"))
        row_ids += [f"cmp-r{i}", f"cmp-d{i}"]

    components.append(text("cmp-title",
                           f"Comparing {len(columns)} cars", variant="h3"))
    components.append(column("cmp-grid", row_ids))
    components.append(column("cmp-root", ["cmp-title", "cmp-grid"]))
    return [
        delete_surface(COMPARE_SURFACE),
        create_surface(COMPARE_SURFACE),
        update_components(COMPARE_SURFACE, components),
    ]


def clear_compare_messages() -> list[dict[str, Any]]:
    """Fewer than two cars selected — the table has nothing to say."""
    return [delete_surface(COMPARE_SURFACE)]


def _slot_placeholder(state: dict[str, Any], slot: str) -> str:
    """A declined slot reads as 'any', not as a still-pending ellipsis."""
    declined = (state.get("interview") or {}).get("declined") or []
    return "any" if slot in declined else "…"


def _fmt_date(value: Any) -> str:
    if value is None:
        return "…"
    if isinstance(value, dict):
        return f"{value.get('from', '?')} → {value.get('to', '?')}"
    return str(value)
