"""Hybrid host<->Maisaka process protocol, v2.

ZhuangFY is the authoritative QQ sender and keeper of conversation identity.
These envelopes are DATA, not model instructions; native runtime activation is
separate. No raw images, paths, API keys or memory credentials cross IPC.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

VERSION = 2
MAX_WIRE_BYTES = 20_000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CONVERSATION_RE = re.compile(r"^qq:([1-9][0-9]*):private:([1-9][0-9]*)$")
KINDS = frozenset({"hello", "ingress", "cancel", "delivery_ack", "shutdown"})
RESULT_KINDS = frozenset({"hello_ack", "accepted", "outbound", "telemetry", "unavailable", "cancel_ack", "ack_result", "error", "bye"})


class ProtocolError(ValueError):
    pass


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ProtocolError(f"invalid {name}")
    return value


def _conversation(value: Any) -> str:
    if not isinstance(value, str) or not CONVERSATION_RE.fullmatch(value):
        raise ProtocolError("invalid private QQ conversation")
    a, b = CONVERSATION_RE.fullmatch(value).groups()
    if a == b:
        raise ProtocolError("bot and user must differ")
    return value


def _components(value: Any) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ProtocolError("invalid message components")
    ids = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"id", "type", "content", "asset_id"}:
            raise ProtocolError("unsupported component fields")
        identifier = _id(item.get("id"), "component id")
        if identifier in ids:
            raise ProtocolError("duplicate component id")
        ids.add(identifier)
        if item.get("type") == "text":
            t = item.get("content")
            if not isinstance(t, str) or not 0 < len(t) <= 3500 or "asset_id" in item:
                raise ProtocolError("invalid text component")
        elif item.get("type") == "image":
            if "content" in item or not isinstance(item.get("asset_id"), str):
                raise ProtocolError("image requires a logical asset ID")
            _id(item["asset_id"], "asset id")
        else:
            raise ProtocolError("unsupported component type")


@dataclass(frozen=True)
class Envelope:
    kind: str
    request_id: str
    conversation_id: str
    payload: dict
    version: int = VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != VERSION:
            raise ProtocolError("unsupported wire schema version")
        if self.kind not in KINDS | RESULT_KINDS:
            raise ProtocolError("unsupported kind")
        _id(self.request_id, "request id")
        if self.kind not in {"hello", "hello_ack", "shutdown", "bye"}:
            _conversation(self.conversation_id)
        elif self.conversation_id != "":
            raise ProtocolError("control events cannot carry a conversation")
        if not isinstance(self.payload, dict):
            raise ProtocolError("invalid payload")
        self._validate_payload()

    def _validate_payload(self) -> None:
        p = self.payload
        if self.kind == "ingress":
            if set(p) != {"message_id", "components", "context"}:
                raise ProtocolError("invalid ingress fields")
            _id(p["message_id"], "message id")
            _components(p["components"])
            context = p["context"]
            required = {"schema_version", "conversation_id", "source_engine",
                        "person_id", "persona", "memory_context", "qq_channel_id"}
            if not isinstance(context, dict) or set(context) != required | {"recent_context", "social"}:
                raise ProtocolError("invalid shared context")
            if (context["schema_version"] != 2
                    or context["conversation_id"] != self.conversation_id
                    or context["source_engine"] != "hybrid"):
                raise ProtocolError("inconsistent shared context")
            if (context["person_id"] is not None and
                    (not isinstance(context["person_id"], str)
                     or len(context["person_id"]) > 128)):
                raise ProtocolError("invalid person id")
            if (not isinstance(context["persona"], str)
                    or not 0 < len(context["persona"]) <= 8000
                    or not isinstance(context["memory_context"], str)
                    or len(context["memory_context"]) > 2000):
                raise ProtocolError("context budget exceeded")
            if (not isinstance(context["recent_context"], str)
                    or len(context["recent_context"]) > 1600):
                raise ProtocolError("recent context budget exceeded")
            social = context["social"]
            required_social = {"person_id", "display_name", "preferred_name",
                               "relationship_status", "relationship_enabled",
                               "relationship_tier", "relationship_version",
                               "relationship_hint", "relationship_definition"}
            if not isinstance(social, dict) or set(social) != required_social:
                raise ProtocolError("invalid social context")
            if social["person_id"] != context["person_id"]:
                raise ProtocolError("cross-person social context")
            for name, limit in (("display_name", 80), ("preferred_name", 80),
                                ("relationship_tier", 16), ("relationship_definition", 300)):
                value = social[name]
                if value is not None and (not isinstance(value, str) or len(value) > limit):
                    raise ProtocolError("invalid social " + name)
            if social["relationship_status"] not in {
                "ready", "unenrolled", "no_consent", "unavailable",
            }:
                raise ProtocolError("invalid relationship status")
            if type(social["relationship_enabled"]) is not bool:
                raise ProtocolError("invalid relationship enabled flag")
            version = social["relationship_version"]
            if version is not None and (type(version) is not int or version < 0):
                raise ProtocolError("invalid relationship version")
            if not isinstance(social["relationship_hint"], str) or len(social["relationship_hint"]) > 1200:
                raise ProtocolError("invalid relationship hint")
            if social["relationship_status"] != "ready" and (
                social["relationship_enabled"] or social["relationship_tier"] is not None
                or version is not None or social["relationship_hint"]
                or social["relationship_definition"] is not None
            ):
                raise ProtocolError("unverified relationship content")
            if social["relationship_status"] == "ready" and (
                not social["relationship_enabled"] or
                social["relationship_tier"] not in {"L0", "L1", "L2", "L3", "L4"}
                or version is None
            ):
                raise ProtocolError("incomplete relationship snapshot")
            bot = CONVERSATION_RE.fullmatch(self.conversation_id).group(1)
            if context["qq_channel_id"] != f"qq:{bot}":
                raise ProtocolError("cross-channel context")
        elif self.kind == "outbound":
            if set(p) != {"reply_id", "text", "cause_event_id"}:
                raise ProtocolError("invalid native outbound fields")
            _id(p["reply_id"], "native reply id")
            _id(p["cause_event_id"], "cause event id")
            if (not isinstance(p["text"], str) or
                    not 0 < len(p["text"]) <= 3500):
                raise ProtocolError("invalid native reply text")
        elif self.kind == "accepted":
            if set(p) not in ({"message_id", "native_runtime"},
                              {"message_id", "native_runtime", "observations"}):
                raise ProtocolError("invalid native acceptance")
            _id(p["message_id"], "accepted message id")
            if p["native_runtime"] is not True:
                raise ProtocolError("cannot claim native acceptance without runtime")
            if "observations" in p:
                items = p["observations"]
                if not isinstance(items, list) or len(items) > 2:
                    raise ProtocolError("invalid image observation list")
                for item in items:
                    if not isinstance(item, dict) or set(item) != {
                        "asset_id", "description", "cache_hit", "description_ready"
                    }:
                        raise ProtocolError("invalid image observation")
                    _id(item["asset_id"], "observation asset ID")
                    if (not isinstance(item["description"], str)
                            or len(item["description"]) > 2500
                            or type(item["cache_hit"]) is not bool
                            or type(item["description_ready"]) is not bool):
                        raise ProtocolError("invalid native image observation fields")
        elif self.kind == "telemetry":
            if set(p) != {"phase", "summary", "cause_event_id"}:
                raise ProtocolError("invalid native telemetry fields")
            if (p["phase"] not in {"阶段", "规划", "输入历史", "模型推理",
                                   "规划正文", "工具执行", "行为结论"}
                    or not isinstance(p["summary"], str)
                    or len(p["summary"]) > 1200):
                raise ProtocolError("invalid native telemetry summary")
            _id(p["cause_event_id"], "telemetry cause")
        elif self.kind == "cancel":
            if set(p) != {"target_request_id"}:
                raise ProtocolError("invalid cancel")
            _id(p["target_request_id"], "target request")
        elif self.kind == "delivery_ack":
            if set(p) != {"reply_id", "delivered", "platform_message_id"}:
                raise ProtocolError("invalid delivery acknowledgement")
            _id(p["reply_id"], "reply id")
            if type(p["delivered"]) is not bool:
                raise ProtocolError("invalid delivered flag")
            if (not isinstance(p["platform_message_id"], str)
                    or len(p["platform_message_id"]) > 128
                    or (p["delivered"] and not p["platform_message_id"])):
                raise ProtocolError("invalid platform message id")
        elif self.kind == "hello":
            if set(p) != {"upstream_sha"} or not isinstance(p["upstream_sha"], str):
                raise ProtocolError("expected upstream pin")

    def to_line(self) -> bytes:
        value = {"version": self.version, "kind": self.kind,
                 "request_id": self.request_id,
                 "conversation_id": self.conversation_id, "payload": self.payload}
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        if len(encoded) + 1 > MAX_WIRE_BYTES:
            raise ProtocolError("wire packet too large")
        return encoded + b"\n"

    @classmethod
    def from_line(cls, data: bytes) -> "Envelope":
        if not isinstance(data, bytes) or not data or len(data) > MAX_WIRE_BYTES:
            raise ProtocolError("wire packet too large or missing")
        try:
            obj = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError("invalid JSON") from exc
        if not isinstance(obj, dict) or set(obj) != {
            "version", "kind", "request_id", "conversation_id", "payload"
        }:
            raise ProtocolError("unexpected wire envelope")
        return cls(**obj)


def inbound_from_host(context: Any, *, event_id: str, message_id: str,
                      components: list[dict]) -> Envelope:
    """Only call after QQ Host independently validated source and permissions."""
    if not hasattr(context, "as_projection"):
        raise ProtocolError("missing verified host context")
    return Envelope("ingress", event_id, context.conversation_id, {
        "message_id": message_id, "components": components,
        "context": context.as_projection(),
    })
