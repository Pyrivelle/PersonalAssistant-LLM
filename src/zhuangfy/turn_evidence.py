"""Write derived evidence only after the host confirms all reply parts.

Keep image observations separate from user corrections. Callers supply a
verified conversation and sender identity, never model-inferred values.
"""
from __future__ import annotations

from pathlib import Path
import re
from .evidence_store import EvidenceStore
from .image_meaning import ImageMeaningStore


def commit_delivered_evidence(
    root: Path,
    *,
    conversation_id: str,
    sender_id: str,
    turn_id: str,
    image_message_id: str = "",
    image_hash: str = "",
    visual_summary: str = "",
    tool_summary: str = "",
    meaning_hash: str = "",
    meaning_text: str = "",
    meaning_source_message_id: str = "",
) -> None:
    if not conversation_id or not sender_id or not turn_id:
        raise ValueError("verified turn identifiers are required")
    # Validate the entire payload *before* any database writes. Otherwise
    # malformed image data could leave the evidence row half committed.
    for digest in (image_hash, meaning_hash):
        if digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid image digest")
    if image_hash and not image_message_id:
        raise ValueError("image message identity required")
    if meaning_hash and (not meaning_text or not meaning_source_message_id):
        raise ValueError("explicit user correction provenance required")
    if visual_summary or tool_summary:
        store = EvidenceStore(root / "context.sqlite3")
        if visual_summary:
            store.save(conversation_id, turn_id, "vision", visual_summary)
        if tool_summary:
            store.save(conversation_id, turn_id, "tool", tool_summary)
    if image_hash or meaning_hash:
        store = ImageMeaningStore(root / "image_meanings.sqlite3")
        if image_hash:
            store.record_image(conversation_id, sender_id, image_message_id, image_hash)
        if meaning_hash:
            store.save(conversation_id, meaning_hash, meaning_text,
                       meaning_source_message_id)
