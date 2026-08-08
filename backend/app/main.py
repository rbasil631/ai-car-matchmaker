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

from . import checkout as checkout_tools
from . import tracing
from .agent import ScriptedAgent
from .mcp_app import TOOLS, read_resource
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


@app.get("/trace/{session_id}")
def trace(session_id: str) -> JSONResponse:
    """The session's spans, oldest first, plus a digest.

    Served locally so the two-step ranking is inspectable without a Langfuse
    account — see app/tracing.py.
    """
    return JSONResponse({"summary": tracing.summarize(session_id),
                         "spans": tracing.get_trace(session_id)})


@app.get("/mcp/tools")
def mcp_tools() -> JSONResponse:
    """tools/list shape — descriptors carry _meta.ui.resourceUri per spec."""
    return JSONResponse({"tools": TOOLS})


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
                reply = agent.respond(session_id, frame.get("text", ""))
                await _emit(websocket, session_id, reply)

            elif kind == "a2ui_action":
                # A2UI client->server event: {name, surfaceId, sourceComponentId,
                # timestamp, context}. Button actions carry listing_id in context.
                reply = agent.handle_action(session_id, frame.get("action", {}))
                await _emit(websocket, session_id, reply)

            elif kind == "mcp_tool_call":
                await _handle_mcp_tool_call(websocket, session_id, frame.get("request", {}))

    except WebSocketDisconnect:
        return


async def _emit(websocket: WebSocket, session_id: str, reply) -> None:
    """Send one agent turn: A2UI envelopes, prose, any MCP App, then state."""
    for msg in reply.a2ui_messages:
        await websocket.send_json({"type": "a2ui", "message": msg})
    await websocket.send_json({"type": "agent_text", "text": reply.text})
    if reply.mcp_app:
        await websocket.send_json({"type": "mcp_app", **reply.mcp_app})
    await websocket.send_json({"type": "state", "state": store.get(session_id)})


async def _handle_mcp_tool_call(websocket: WebSocket, session_id: str, request: dict) -> None:
    """JSON-RPC tools/call relayed from an MCP App iframe by the host."""
    params = request.get("params", {})
    name = params.get("name")
    args = params.get("arguments", {})
    state = store.get(session_id)

    if name == "submit_booking_form":
        with tracing.span(session_id, name, kind="tool",
                          input={"listing_id": args.get("listing_id")}) as sp:
            result = checkout_tools.submit_booking_form(
                state, args.get("listing_id"), args.get("form_data", {}))
            sp["output"] = {"ok": "error" not in result}
    elif name == "submit_payment":
        # only the last four digits are ever sent by the iframe, and the
        # tracer redacts even those
        with tracing.span(session_id, name, kind="tool") as sp:
            result = checkout_tools.submit_payment(state, args.get("card_last4", ""))
            sp["output"] = {"status": result.get("status", "error")}
    else:
        await websocket.send_json({"type": "mcp_tool_result", "response": {
            "jsonrpc": "2.0", "id": request.get("id"),
            "error": {"code": -32601, "message": f"unknown tool {name}"}}})
        return

    if "error" in result:
        # surfaced inside the iframe so the user fixes it in place
        await websocket.send_json({"type": "mcp_tool_result", "response": {
            "jsonrpc": "2.0", "id": request.get("id"),
            "error": {"code": -32602, "message": result["error"]["message"]}}})
        return

    state = store.save(state)
    await websocket.send_json({"type": "mcp_tool_result", "response": {
        "jsonrpc": "2.0", "id": request.get("id"),
        "result": {**result, "version": state["version"]}}})

    follow_up = (agent.after_booking_form(session_id) if name == "submit_booking_form"
                 else agent.after_payment(session_id, result))
    await _emit(websocket, session_id, follow_up)
