"""Conversation states, speech chunks and conservative echo screening."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from enum import Enum


class InteractionState(str, Enum):
    IDLE = "Idle"
    LISTENING = "Listening"
    THINKING = "Thinking"
    SPEAKING = "Speaking"


_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])")
_NORMALIZE = re.compile(r"[^\w\u3400-\u9fff]+", re.UNICODE)


def speech_chunks(text: str, limit: int = 90) -> list[str]:
    """Split a complete response into short utterances for interruptible playback."""
    if limit < 10:
        raise ValueError("语音分段上限至少为 10 字")
    pieces: list[str] = []
    for paragraph in text.splitlines():
        pieces.extend(piece.strip() for piece in _SENTENCE_END.split(paragraph) if piece.strip())
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if len(current) + len(piece) <= limit:
            current += piece
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(piece) > limit:
            # Prefer a natural pause; hard-split only when a clause is very long.
            cut = max(piece.rfind(mark, 0, limit + 1) for mark in ("，", ",", "、", " ")) + 1
            if cut < limit // 3:
                cut = limit
            chunks.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        current = piece
    if current:
        chunks.append(current)
    return chunks


def likely_playback_echo(recognized: str, answer: str) -> bool:
    """Reject text that is likely the assistant's own audible speech.

    This is a text-level safety net, not acoustic echo cancellation. Speaker mode
    also requires the wake name for barge-in while the assistant is speaking.
    """
    heard = _NORMALIZE.sub("", recognized).casefold()
    spoken = _NORMALIZE.sub("", answer).casefold()
    if not spoken or not heard:
        return False
    if len(heard) < 3:
        return len(heard) >= 2 and heard in spoken
    if heard in spoken:
        return True
    if len(heard) >= 5 and SequenceMatcher(None, heard, spoken, autojunk=False).find_longest_match().size >= max(5, int(len(heard) * 0.8)):
        return True
    return False
