"""MCP App surface for M1: the booking-form UI resource + its tool descriptor.

Shapes verified against modelcontextprotocol/ext-apps specification/2026-01-26
(see specs/001-car-matchmaker/research.md):

  - UI resources use the ui:// scheme, mimeType "text/html;profile=mcp-app"
  - Tools link to their UI via _meta.ui.resourceUri
    (the flat _meta["ui/resourceUri"] form is DEPRECATED — do not use)
  - Iframe <-> host speak MCP JSON-RPC over postMessage

M1 scope: prove the render + round-trip inside our own host. The full MCP
server wiring (initialize handshake, resources/read over the SDK) lands in M4;
the descriptor and resource shapes below are already spec-correct so M4 is a
transport swap, not a redesign.
"""
from __future__ import annotations

from typing import Any

BOOKING_FORM_URI = "ui://car-matchmaker/booking-form"
MIME = "text/html;profile=mcp-app"

BOOKING_TOOL: dict[str, Any] = {
    "name": "open_booking_form",
    "description": "Open the in-chat booking form for a held listing.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "listing_id": {"type": "string"},
        },
        "required": ["session_id", "listing_id"],
    },
    "_meta": {"ui": {"resourceUri": BOOKING_FORM_URI, "visibility": ["model", "app"]}},
}

# The template is PRE-DECLARED (a resource), not generated per call — per spec.
# It reads prefill data passed by the host and posts a JSON-RPC tools/call back.
BOOKING_FORM_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 16px; background: #fff; color: #1a1a1a; }
  h3 { margin: 0 0 4px; } .sub { color: #666; font-size: 13px; margin-bottom: 14px; }
  label { display: block; font-size: 12px; color: #555; margin: 10px 0 3px; }
  input { width: 100%; box-sizing: border-box; padding: 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
  button { margin-top: 16px; padding: 10px 18px; border: 0; border-radius: 6px; background: #1a3a8f; color: #fff; font-size: 14px; cursor: pointer; }
  .mock { font-size: 11px; color: #999; margin-top: 10px; }
</style></head><body>
  <h3>Booking form</h3>
  <div class="sub" id="listing">Loading listing…</div>
  <label>Full name</label><input id="name" autocomplete="off">
  <label>Phone</label><input id="phone" autocomplete="off">
  <label>Pickup / delivery date</label><input id="date" autocomplete="off">
  <button id="submit">Confirm details</button>
  <div class="mock">MCP App demo — booking data is stored in session state only.</div>
<script>
  // Host -> iframe: prefill via postMessage notification.
  // Iframe -> host: MCP JSON-RPC tools/call, per ext-apps spec.
  let sessionId = null, listingId = null;
  window.addEventListener("message", (ev) => {
    const msg = ev.data;
    if (msg && msg.method === "notifications/tool-result" && msg.params) {
      sessionId = msg.params.session_id; listingId = msg.params.listing_id;
      document.getElementById("listing").textContent =
        "Listing " + listingId + " · prefilled from session " + sessionId.slice(0, 8);
      if (msg.params.prefill_date) document.getElementById("date").value = msg.params.prefill_date;
    }
  });
  document.getElementById("submit").addEventListener("click", () => {
    window.parent.postMessage({
      jsonrpc: "2.0", id: Date.now(), method: "tools/call",
      params: { name: "submit_booking_form", arguments: {
        session_id: sessionId, listing_id: listingId,
        form_data: {
          name: document.getElementById("name").value,
          phone: document.getElementById("phone").value,
          date: document.getElementById("date").value
        } } }
    }, "*");
  });
</script></body></html>
"""


def read_resource(uri: str) -> dict[str, Any] | None:
    """Shape of a resources/read result for our UI resource."""
    if uri != BOOKING_FORM_URI:
        return None
    return {"contents": [{"uri": uri, "mimeType": MIME, "text": BOOKING_FORM_HTML}]}
