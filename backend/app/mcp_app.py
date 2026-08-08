"""MCP App surfaces: the booking form and the mock payment interface.

Shapes verified against modelcontextprotocol/ext-apps specification/2026-01-26
(see specs/001-car-matchmaker/research.md):

  - UI resources are PRE-DECLARED under the ui:// scheme, mimeType
    "text/html;profile=mcp-app". A template is not generated per call; the host
    fetches it once and the server passes data in at render time.
  - Tool -> UI linkage is _meta.ui.resourceUri. The flat _meta["ui/resourceUri"]
    form is DEPRECATED and must not be emitted.
  - Host renders in a sandboxed iframe; iframe <-> host speak MCP JSON-RPC over
    postMessage.

Because the templates are pre-declared, each one must handle every variant it
will ever be asked to show — hence the booking form branching on buy vs rent
from its prefill payload rather than the server shipping two templates.

SECURITY: the payment iframe never sends a full card number anywhere. It
extracts the last four digits locally and posts only those. The full number,
expiry and CVV never leave the iframe, so no card data can reach session state
or traces (contract §6).
"""
from __future__ import annotations

from typing import Any

BOOKING_FORM_URI = "ui://car-matchmaker/booking-form"
PAYMENT_URI = "ui://car-matchmaker/mock-payment"
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

PAYMENT_TOOL: dict[str, Any] = {
    "name": "open_payment",
    "description": "Open the mocked payment interface for the active booking.",
    "inputSchema": {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
    },
    "_meta": {"ui": {"resourceUri": PAYMENT_URI, "visibility": ["model", "app"]}},
}

TOOLS = [BOOKING_TOOL, PAYMENT_TOOL]

_SHARED_CSS = """
  body { font-family: system-ui, sans-serif; margin:0; padding:16px; background:#fff; color:#1a1a1a; }
  h3 { margin:0 0 4px; font-size:15px; }
  .sub { color:#666; font-size:12.5px; margin-bottom:14px; }
  label { display:block; font-size:11.5px; color:#555; margin:10px 0 3px; }
  input { width:100%; box-sizing:border-box; padding:8px; border:1px solid #ccc;
          border-radius:6px; font-size:14px; }
  .row { display:flex; gap:10px; }
  .row > div { flex:1; }
  button { margin-top:16px; padding:10px 18px; border:0; border-radius:6px;
           background:#1a3a8f; color:#fff; font-size:14px; cursor:pointer; }
  button[disabled] { background:#9aa4bf; cursor:default; }
  .note { font-size:11px; color:#999; margin-top:10px; }
  .banner { background:#fff5e6; border:1px solid #f0d9a8; color:#8a6100;
            padding:8px 10px; border-radius:6px; font-size:12px; margin-bottom:12px; }
  .error { background:#fdeaea; border:1px solid #f3bdbd; color:#a12626;
           padding:8px 10px; border-radius:6px; font-size:12.5px; margin-top:12px; }
  .ok { background:#eaf7ee; border:1px solid #b7e0c4; color:#1d6b34;
        padding:10px; border-radius:6px; font-size:13px; margin-top:12px; }
"""

BOOKING_FORM_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>%s</style></head><body>
  <h3>Booking details</h3>
  <div class="sub" id="listing">Loading…</div>
  <label>Full name</label><input id="name" autocomplete="off">
  <label>Phone</label><input id="phone" autocomplete="off" inputmode="tel">
  <div id="mode-fields"></div>
  <button id="submit">Continue to payment</button>
  <div id="err"></div>
  <div class="note">Details are stored in this session only.</div>
<script>
  let sessionId = null, listingId = null;

  window.addEventListener("message", (ev) => {
    const msg = ev.data;
    if (msg && msg.method === "notifications/tool-result" && msg.params) {
      const p = msg.params;
      sessionId = p.session_id; listingId = p.listing_id;
      document.getElementById("listing").textContent = p.summary || "";
      // The template is pre-declared, so it renders whichever variant the
      // server asks for rather than the server shipping two templates.
      document.getElementById("mode-fields").innerHTML = p.mode === "rent"
        ? '<div class="row"><div><label>Pickup date</label>' +
          '<input id="f1" value="' + (p.prefill_from || "") + '"></div>' +
          '<div><label>Return date</label>' +
          '<input id="f2" value="' + (p.prefill_to || "") + '"></div></div>' +
          '<label>Pickup location</label><input id="f3" value="' + (p.location || "") + '">'
        : '<label>Delivery address</label><input id="f1">' +
          '<label>Registration city</label><input id="f3" value="' + (p.location || "") + '">' +
          '<label>Preferred delivery date</label><input id="f2" value="' +
          (p.prefill_from || "") + '">';
    }
    if (msg && msg.jsonrpc === "2.0" && msg.error) {
      document.getElementById("err").innerHTML =
        '<div class="error">' + msg.error.message + '</div>';
      document.getElementById("submit").disabled = false;
    }
  });

  document.getElementById("submit").addEventListener("click", () => {
    const val = (id) => (document.getElementById(id) || {}).value || "";
    document.getElementById("err").innerHTML = "";
    document.getElementById("submit").disabled = true;
    window.parent.postMessage({
      jsonrpc: "2.0", id: Date.now(), method: "tools/call",
      params: { name: "submit_booking_form", arguments: {
        session_id: sessionId, listing_id: listingId,
        form_data: { name: val("name"), phone: val("phone"),
                     field_1: val("f1"), field_2: val("f2"), field_3: val("f3") } } }
    }, "*");
  });
</script></body></html>
""" % _SHARED_CSS

PAYMENT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>%s</style></head><body>
  <div class="banner"><strong>MOCK PAYMENT</strong> — no real transaction occurs.
  A card ending <code>0000</code> demonstrates the declined path.</div>
  <h3 id="amount">…</h3>
  <div class="sub" id="detail"></div>
  <div id="form">
    <label>Card number</label><input id="card" inputmode="numeric" placeholder="4242 4242 4242 4242">
    <div class="row">
      <div><label>Expiry</label><input id="exp" placeholder="MM/YY"></div>
      <div><label>CVV</label><input id="cvv" inputmode="numeric" placeholder="123"></div>
    </div>
    <label>Name on card</label><input id="holder" autocomplete="off">
    <button id="pay">Pay now</button>
  </div>
  <div id="out"></div>
<script>
  let sessionId = null;

  window.addEventListener("message", (ev) => {
    const msg = ev.data;
    if (msg && msg.method === "notifications/tool-result" && msg.params) {
      const p = msg.params;
      sessionId = p.session_id;
      document.getElementById("amount").textContent = p.amount_label || "";
      document.getElementById("detail").textContent = p.summary || "";
    }
    if (msg && msg.jsonrpc === "2.0" && msg.result) {
      const r = msg.result;
      if (r.status === "confirmed") {
        document.getElementById("form").style.display = "none";
        document.getElementById("out").innerHTML =
          '<div class="ok">Payment confirmed \\u2713<br>Confirmation ' +
          r.confirmation_id + '</div>';
      } else {
        // declined: the form stays open so the user can retry (contract §6)
        document.getElementById("out").innerHTML =
          '<div class="error">' + (r.message || "Payment declined.") + '</div>';
        document.getElementById("pay").disabled = false;
        document.getElementById("pay").textContent = "Try again";
      }
    }
  });

  document.getElementById("pay").addEventListener("click", () => {
    const digits = (document.getElementById("card").value || "").replace(/\\D/g, "");
    if (digits.length < 4) {
      document.getElementById("out").innerHTML =
        '<div class="error">Enter a card number.</div>';
      return;
    }
    document.getElementById("out").innerHTML = "";
    const btn = document.getElementById("pay");
    btn.disabled = true; btn.textContent = "Processing…";
    // Only the last four digits ever leave this iframe. The full number, the
    // expiry and the CVV are never transmitted, stored, or traced.
    const last4 = digits.slice(-4);
    setTimeout(() => {
      window.parent.postMessage({
        jsonrpc: "2.0", id: Date.now(), method: "tools/call",
        params: { name: "submit_payment",
                  arguments: { session_id: sessionId, card_last4: last4 } }
      }, "*");
    }, 900);   // simulated processing delay (contract §6)
  });
</script></body></html>
""" % _SHARED_CSS

_RESOURCES = {
    BOOKING_FORM_URI: BOOKING_FORM_HTML,
    PAYMENT_URI: PAYMENT_HTML,
}


def read_resource(uri: str) -> dict[str, Any] | None:
    """Shape of a resources/read result for our UI resources."""
    html = _RESOURCES.get(uri)
    if html is None:
        return None
    return {"contents": [{"uri": uri, "mimeType": MIME, "text": html}]}
