"""Channel-neutral message data for Agent V2."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True, slots=True)
class MessagePart:
    kind: Literal["text","image","emoji","audio","file","mention","quote"]
    text: str = ""
    media_ref: str = ""

@dataclass(frozen=True, slots=True)
class UnifiedMessage:
    channel: str
    account_id: str
    session_id: str
    session_kind: Literal["private","group"]
    sender_id: str
    message_id: str
    received_at: float
    parts: tuple[MessagePart,...]
    reply_to_id: str = ""

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if p.kind=="text").strip()

    @property
    def is_bare_image(self) -> bool:
        kinds=[p.kind for p in self.parts if p.kind not in ("mention","quote") and (p.kind!="text" or p.text.strip())]
        return bool(kinds) and all(k=="image" for k in kinds)

    @property
    def is_pure_emoji(self) -> bool:
        kinds=[p.kind for p in self.parts if p.kind not in ("mention","quote") and (p.kind!="text" or p.text.strip())]
        return bool(kinds) and all(k=="emoji" for k in kinds)

def from_qq_incoming(incoming: object, *, bot_id: int, event: dict, received_at: float) -> UnifiedMessage:
    """Only call after QQ adapter allowlist and group-mention verification."""
    kind=incoming.kind
    user_id=str(incoming.user_id)
    sid=f"qq:{bot_id}:group:{incoming.group_id}" if kind=="group" else f"qq:{bot_id}:private:{user_id}"
    raw=event.get("message")
    parts=[]
    quote=""
    mapping={"image":"image","face":"emoji","mface":"emoji","record":"audio","file":"file","at":"mention","reply":"quote"}
    if isinstance(raw,list):
        for segment in raw:
            if not isinstance(segment,dict): continue
            t=segment.get("type")
            data=segment.get("data") or {}
            if not isinstance(data,dict): continue
            if t=="text" and data.get("text"): parts.append(MessagePart("text",text=str(data["text"])))
            elif t in mapping:
                if t=="reply": quote=str(data.get("id") or "")
                parts.append(MessagePart(mapping[t],text=str(data.get("qq") or "") if t=="at" else quote if t=="reply" else "",media_ref=str(data.get("file") or data.get("id") or data.get("emoji_id") or "")))
    else:
        count=int(getattr(incoming,"image_count",0))
        body=str(getattr(incoming,"text",""))
        if body and not (count and body.startswith("[图片")): parts.append(MessagePart("text",text=body))
        ref=str(getattr(getattr(incoming,"image",None),"file","") or "")
        parts.extend(MessagePart("image",media_ref=ref) for _ in range(count))
    if not parts: parts=[MessagePart("text",text=str(getattr(incoming,"text","")))]
    return UnifiedMessage("qq",str(bot_id),sid,kind,user_id,str(incoming.message_id),received_at,tuple(parts),quote)
