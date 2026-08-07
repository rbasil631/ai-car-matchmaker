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


def text(cid: str, value: Any, variant: str = "body") -> dict[str, Any]:
    """`value` is a literal str or a data binding like {"path": "/x"}.
    variant per catalog enum: h1..h5 | caption | body."""
    return {"id": cid, "component": "Text", "text": value, "variant": variant}


def column(cid: str, children: list[str]) -> dict[str, Any]:
    return {"id": cid, "component": "Column", "children": children}


def row(cid: str, children: list[str]) -> dict[str, Any]:
    return {"id": cid, "component": "Row", "children": children}


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
        "car_type": intent["car_type"] or "…",
        "budget": (
            f"₹{intent['budget']['amount']:,} {intent['budget']['period']}"
            if intent["budget"]["amount"]
            else "…"
        ),
        "target_date": _fmt_date(intent["target_date"]),
    }
    msgs.append(update_data_model(INTERVIEW_SURFACE, {"slots": slots}))
    return msgs


def _fmt_date(value: Any) -> str:
    if value is None:
        return "…"
    if isinstance(value, dict):
        return f"{value.get('from', '?')} → {value.get('to', '?')}"
    return str(value)
