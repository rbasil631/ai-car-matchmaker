"""Backend: chat websocket + A2UI stream + MCP App resource endpoints.

Wire protocol to the frontend (one websocket, JSON frames):
  client -> server: {"type": "user_message", "text": ...}
                    {"type": "a2ui_action", "action": {...}}          # A2UI client->server event
                    {"type": "mcp_tool_call", "request": {...}}       # relayed from iframe (JSON-RPC)
  server -> client: {"type": "agent_text", "text": ...}
                    {"type": "a2ui", "message": {...}}                # one A2UI envelope per frame
                    {"type": "mcp_app", "resource_uri": ..., "html": ..., "tool_result": {...}}
                    {"type": "mcp_tool_result", "response": {...}}
                    {"type": "state", "state": {...}}                 # dev/debug panel
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from .agent import ScriptedAgent
from .mcp_app import BOOKING_FORM_URI, BOOKING_TOOL, read_resource
from .state import SessionStore

DB_PATH = os.environ.get("CARMATCH_DB", "carmatch.sqlite")
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="AI Car Matchmaker")
store = SessionStore(DB_PATH)
agent = ScriptedAgent(store)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/mcp/tools")
def mcp_tools() -> JSONResponse:
    """tools/list shape — descriptors carry _meta.ui.resourceUri per spec."""
    return JSONResponse({"tools": [BOOKING_TOOL]})


@app.get("/mcp/resource")
def mcp_resource(uri: str) -> JSONResponse:
    result = read_resource(uri)
    if result is None:
        return JSONResponse({"error": {"code": "not_found", "message": uri}}, status_code=404)
    return JSONResponse(result)


@app.websocket("/ws/{session_id}")
async def ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    state = store.get_or_create(session_id)
    await websocket.send_json({"type": "state", "state": state})
    try:
        while True:
            frame = json.loads(await websocket.receive_text())
            kind = frame.get("type")

            if kind == "user_message":
                text = frame.get("text", "")
                if text.strip().lower() == "/book demo-listing":
                    # shortcut that exercises the MCP App path before M4 wires
                    # checkout to a held car
                    await _open_booking_form(websocket, session_id, "l-demo")
                    continue
                reply = agent.respond(session_id, text)
                for msg in reply.a2ui_messages:
                    await websocket.send_json({"type": "a2ui", "message": msg})
                await websocket.send_json({"type": "agent_text", "text": reply.text})
                await websocket.send_json({"type": "state", "state": store.get(session_id)})

            elif kind == "a2ui_action":
                # A2UI client->server event: {name, surfaceId, sourceComponentId,
                # timestamp, context}. Button actions carry listing_id in context.
                reply = agent.handle_action(session_id, frame.get("action", {}))
                for msg in reply.a2ui_messages:
                    await websocket.send_json({"type": "a2ui", "message": msg})
                await websocket.send_json({"type": "agent_text", "text": reply.text})
                await websocket.send_json({"type": "state", "state": store.get(session_id)})

            elif kind == "mcp_tool_call":
                await _handle_mcp_tool_call(websocket, session_id, frame.get("request", {}))

    except WebSocketDisconnect:
        return


async def _open_booking_form(websocket: WebSocket, session_id: str, listing_id: str) -> None:
    resource = read_resource(BOOKING_FORM_URI)
    state = store.get(session_id) or store.create(session_id)
    prefill_date = None
    td = state["intent"]["target_date"]
    if isinstance(td, dict):
        prefill_date = td.get("from")
    elif isinstance(td, str):
        prefill_date = td
    await websocket.send_json(
        {
            "type": "mcp_app",
            "resource_uri": BOOKING_FORM_URI,
            "html": resource["contents"][0]["text"],
            "tool_result": {
                "session_id": session_id,
                "listing_id": listing_id,
                "prefill_date": prefill_date,
            },
        }
    )
    await websocket.send_json(
        {"type": "agent_text", "text": "Here's the booking form — fill it in right here."}
    )


async def _handle_mcp_tool_call(websocket: WebSocket, session_id: str, request: dict) -> None:
    """JSON-RPC tools/call relayed from the iframe by the host."""
    params = request.get("params", {})
    if params.get("name") == "submit_booking_form":
        args = params.get("arguments", {})
        state = store.get(session_id)
        state["checkout"]["form_data"] = args.get("form_data", {})
        state["checkout"]["active_listing_id"] = args.get("listing_id")
        state["phase"] = "booking"
        state = store.save(state)
        await websocket.send_json(
            {
                "type": "mcp_tool_result",
                "response": {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {"ok": True, "version": state["version"]},
                },
            }
        )
        await websocket.send_json(
            {
                "type": "agent_text",
                "text": (
                    f"Details saved for {args.get('listing_id')} — "
                    "next step would be payment (lands in M4)."
                ),
            }
        )
        await websocket.send_json({"type": "state", "state": state})
    else:
        await websocket.send_json(
            {
                "type": "mcp_tool_result",
                "response": {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {"code": -32601, "message": "unknown tool"},
                },
            }
        )
