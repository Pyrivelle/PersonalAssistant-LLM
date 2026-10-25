"""Hybrid IPC worker skeleton: genuine subprocess transport, NO model execution.

The worker refuses incoming dialogue until the pinned native Maisaka adapter
is implemented. This prevents a protocol-only milestone from falsely implying
that Planner->Reply->Wait is functioning.
"""
from __future__ import annotations

import sys
from zhuangfy.engine_fusion.ipc_contract import Envelope, ProtocolError, MAX_WIRE_BYTES, VERSION

UPSTREAM_SHA = "062d45cf8bd4272ac57d7ec03fcf405e5a458211"


class WorkerSession:
    def __init__(self):
        self.started = False
        self.closed = False
        self.active: set[str] = set()

    def dispatch(self, request: Envelope) -> Envelope:
        kind = request.kind
        if kind == "hello":
            valid = request.payload["upstream_sha"] == UPSTREAM_SHA
            self.started = valid
            return Envelope("hello_ack", request.request_id, "", {
                "upstream_sha": UPSTREAM_SHA, "protocol": VERSION,
                "transport_ready": valid,
                "native_runtime_ready": False,
                "capabilities": ["qq_ingress_contract", "cancel_contract", "delivery_ack_contract"],
                "reason": "" if valid else "upstream_pin_mismatch",
            })
        if not self.started:
            return Envelope("error", request.request_id,
                            request.conversation_id, {"reason": "handshake_required"})
        if kind == "ingress":
            # No user-visible assistant reply is generated or sent here.
            # Once native Maisaka can own this turn, hand it the projection.
            return Envelope("unavailable", request.request_id,
                            request.conversation_id, {
                                "reason": "native_maisaka_adapter_not_connected",
                                "delivered": False,
                            })
        if kind == "cancel":
            self.active.discard(request.payload["target_request_id"])
            return Envelope("cancel_ack", request.request_id, request.conversation_id, {
                "target_request_id": request.payload["target_request_id"],
                "cancelled": False,
                "reason": "no_active_native_turn",
            })
        if kind == "delivery_ack":
            return Envelope("ack_result", request.request_id,
                            request.conversation_id, {
                                "accepted": False,
                                "reason": "no_native_reply_to_acknowledge",
                            })
        if kind == "shutdown":
            self.closed = True
            return Envelope("bye", request.request_id, "", {"closed": True})
        return Envelope("error", request.request_id,
                        request.conversation_id, {"reason": "unsupported_request"})


def run_worker(source=None, destination=None) -> int:
    source = source or sys.stdin.buffer
    destination = destination or sys.stdout.buffer
    worker = WorkerSession()
    while not worker.closed:
        line = source.readline(MAX_WIRE_BYTES + 1)
        if not line:
            break
        try:
            request = Envelope.from_line(line)
            response = worker.dispatch(request)
        except ProtocolError:
            # Never echo request content, keys or user text in errors.
            # Invalid/untrusted requests terminate the worker.
            return 2
        destination.write(response.to_line())
        destination.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_worker())
