"""SHIP framing and handshake.

Frame layout: a single MessageType byte (0x01 = Control, 0x02 = Data) followed by
EEBUS-encoded JSON.

The handshake follows the SHIP spec:
1. CMI initial bytes
2. Hello (trust establishment)
3. Protocol negotiation
4. PIN state (only `none` supported here)
5. Access methods exchange
"""

from __future__ import annotations

import json
import logging
import time

from .eebus_json import json_from_eebus_json, json_into_eebus_json, json_text_into_eebus_json

logger = logging.getLogger(__name__)
trace = logging.getLogger(__name__ + ".trace")


async def send_ship_json(ws, data) -> None:
    """Send SHIP Control JSON (MessageType=0x01) in EEBUS JSON format."""
    eebus_text = json_into_eebus_json(data)
    msg = b"\x01" + eebus_text.encode("utf-8")
    await ws.send(msg)
    logger.debug("📤 Sent: %s", list(data.keys()))
    trace.debug("< %s", eebus_text)


async def send_ship_data(ws, data) -> None:
    """Send SHIP Data JSON (MessageType=0x02) in ship-go compatible format.

    ship-go serializes SPINE in two steps:
    1) SPINE datagram -> EEBUS JSON
    2) SHIP Data envelope (with placeholder payload) -> EEBUS JSON,
       then patch the payload back in as a RawMessage.

    That keeps the SHIP payload object intact (not array-wrapped a second time).
    """
    spine_std = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    spine_eebus = json_text_into_eebus_json(spine_std)

    payload_placeholder = '{"place":"holder"}'
    ship_std_obj = {
        "data": {
            "header": {"protocolId": "ee1.0"},
            "payload": json.loads(payload_placeholder),
        }
    }
    ship_std = json.dumps(ship_std_obj, separators=(",", ":"), ensure_ascii=False)
    ship_eebus = json_text_into_eebus_json(ship_std)

    # ship-go replaces `[payloadPlaceholder]` with the already-transformed SPINE payload
    ship_eebus = ship_eebus.replace(f"[{payload_placeholder}]", spine_eebus)

    msg = b"\x02" + ship_eebus.encode("utf-8")
    await ws.send(msg)
    trace.debug("< %s", ship_eebus.encode("utf-8"))


async def send_access_methods(ws, local_ship_id: str) -> None:
    """Send the SHIP accessMethods response, which carries our local SHIP id."""
    await send_ship_json(ws, {"accessMethods": {"id": local_ship_id}})


async def perform_ship_handshake(ws, local_ship_id: str) -> bool:
    """Drive the SHIP handshake state machine to completion.

    Returns True on success, False on failure/abort.
    """
    # === PHASE 1: CMI ===
    init_ack = await ws.recv()
    if isinstance(init_ack, bytes):
        logger.debug("📥 [CMI] Initial bytes received: %s", init_ack.hex())
    else:
        logger.debug("📥 [CMI] Initial received (non-bytes): %s", init_ack)

    # === PHASE 2: HELLO ===
    logger.debug("📤 [HELLO] Sending connectionHello (phase: ready)...")
    await send_ship_json(ws, {"connectionHello": {"phase": "ready", "waiting": 60000}})

    state = "WAITING_HELLO"
    last_pending_hello_sent = 0.0

    while True:
        try:
            raw_msg = await ws.recv()
        except Exception as e:
            code = getattr(e, "code", None)
            reason = getattr(e, "reason", None)
            logger.error(
                "WebSocket closed during handshake: code=%s reason=%s err=%s", code, reason, e
            )
            return False
        if not isinstance(raw_msg, bytes) or len(raw_msg) < 2:
            continue

        header = raw_msg[0]
        if header != 0x01:
            logger.warning("Unexpected SHIP MessageType: 0x%02x (len=%d)", header, len(raw_msg))
            continue

        payload_text = raw_msg[1:].decode("utf-8", errors="ignore")
        payload_text = json_from_eebus_json(payload_text)

        try:
            msg = json.loads(payload_text)
            logger.debug("📥 Received: %s", json.dumps(msg, indent=2))
        except json.JSONDecodeError:
            logger.warning("JSON decode error: %s", payload_text[:200])
            continue

        # === PHASE 2: HELLO RESPONSE ===
        if "connectionHello" in msg and state == "WAITING_HELLO":
            hello = msg.get("connectionHello") or {}
            phase = hello.get("phase")

            if phase == "pending":
                prolong = hello.get("prolongationRequest")
                waiting_ms = hello.get("waiting")

                logger.warning(
                    "⏳ [HELLO] STATUS: PENDING - waiting for confirmation in the myVAILLANT app..."
                )
                logger.warning("👉 Confirm access in the app NOW!")

                # While the remote side is still pending (waiting for user trust/pairing),
                # we MUST NOT proceed to protocol/pin/access. To keep the hello phase alive
                # and avoid timeouts, we answer with our own PENDING + waiting.
                if isinstance(waiting_ms, int):
                    logger.debug(
                        "⏳ [HELLO] Remote waiting=%dms prolongationRequest=%s", waiting_ms, prolong
                    )
                else:
                    logger.debug("⏳ [HELLO] Remote prolongationRequest=%s", prolong)

                now = time.monotonic()
                if now - last_pending_hello_sent > 5.0:
                    await send_ship_json(
                        ws, {"connectionHello": {"phase": "pending", "waiting": 60000}}
                    )
                    last_pending_hello_sent = now

            elif phase == "ready":
                logger.debug("✅ [HELLO] Phase complete - both sides READY")

                # === PHASE 3: PROTOCOL HANDSHAKE ===
                logger.debug("📤 [PROTOCOL] Sending messageProtocolHandshake...")
                await send_ship_json(
                    ws,
                    {
                        "messageProtocolHandshake": {
                            "handshakeType": "announceMax",
                            "version": {"major": 1, "minor": 0},
                            "formats": {"format": ["JSON-UTF8"]},
                        }
                    },
                )
                state = "WAITING_PROTOCOL"

            elif phase == "aborted":
                logger.error("[HELLO] Connection rejected by heat pump (aborted).")
                return False

        # === PHASE 3: PROTOCOL RESPONSE ===
        elif "messageProtocolHandshake" in msg and state == "WAITING_PROTOCOL":
            handshake = msg.get("messageProtocolHandshake") or {}

            handshake_type = handshake.get("handshakeType")
            if handshake_type != "select":
                logger.error("[PROTOCOL] Unexpected handshakeType: %s", handshake_type)
                return False

            version = handshake.get("version", {})
            if version.get("major") != 1:
                logger.error("[PROTOCOL] Unsupported version: %s", version)
                return False

            logger.debug("✅ [PROTOCOL] Protocol handshake confirmed (version 1.0)")

            logger.debug("📤 [PROTOCOL] Confirming selection (select)...")
            await send_ship_json(
                ws,
                {
                    "messageProtocolHandshake": {
                        "handshakeType": "select",
                        "version": {"major": 1, "minor": 0},
                        "formats": {"format": ["JSON-UTF8"]},
                    }
                },
            )

            # === PHASE 4: PIN STATE ===
            logger.debug("📤 [PIN] Sending connectionPinState (none)...")
            await send_ship_json(ws, {"connectionPinState": {"pinState": "none"}})
            state = "WAITING_PIN"

        elif "messageProtocolHandshakeError" in msg:
            err = (msg.get("messageProtocolHandshakeError") or {}).get("error")
            logger.error("[PROTOCOL] messageProtocolHandshakeError received: error=%s", err)
            return False

        # === PHASE 4: PIN RESPONSE ===
        elif "connectionPinState" in msg and state == "WAITING_PIN":
            pin_state = (msg.get("connectionPinState") or {}).get("pinState")
            logger.debug("✅ [PIN] PIN state confirmed: %s", pin_state)
            if pin_state != "none":
                logger.error(
                    "[PIN] Device requires a PIN (or sent unexpected state). Only 'none' is supported."
                )
                return False

            # === PHASE 5: ACCESS METHODS ===
            logger.debug("📤 [ACCESS] Sending accessMethodsRequest...")
            await send_ship_json(ws, {"accessMethodsRequest": {}})
            state = "WAITING_ACCESS"

        # === PHASE 5: ACCESS RESPONSE ===
        elif "accessMethodsRequest" in msg and state == "WAITING_ACCESS":
            logger.debug(
                "📥 [ACCESS] accessMethodsRequest received from device → sending accessMethods..."
            )
            await send_access_methods(ws, local_ship_id)

        elif "accessMethods" in msg and state == "WAITING_ACCESS":
            remote_id = (msg.get("accessMethods") or {}).get("id", "unknown")
            logger.debug("✅ [ACCESS] Access methods received (remote ID: %s)", remote_id)
            logger.debug("=" * 60)
            logger.debug("💎 SHIP HANDSHAKE COMPLETED SUCCESSFULLY!")
            logger.debug("=" * 60)
            return True

        elif (
            state == "WAITING_ACCESS"
            and "connectionPinState" not in msg
            and "accessMethods" not in msg
            and "accessMethodsRequest" not in msg
        ):
            logger.warning("[STATE: %s] Unexpected message: %s", state, list(msg.keys()))
