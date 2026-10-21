"""Remove leading narration and present longer QQ prose as short messages.

Only the opening bracketed stage directions are stripped. Ordinary brackets,
technical explanations and code blocks are preserved. This is presentation
formatting, not rewriting user facts.
"""
from __future__ import annotations

import re

MAX_PARTS = 3
MAX_QQ_CHARS = 1200
SOFT_PART_CHARS = 220
SPLIT_SINGLE_AFTER = 285
FENCE = chr(96) * 3
_STAGE = re.compile(
    r"^(?:（([^（）\n]{1,60})）|\(([^()\n]{1,60})\)|【([^【】\n]{1,60})】|\[([^\[\]\n]{1,60})\])"
)
_STAGE_MARKER = re.compile(
    r"^(?:语气|语调|语速|声线|音调|声音|神情|表情|动作|旁白|内心|心理|"
    r"轻声|低声|柔声|轻笑|浅笑|微笑|笑着|笑了|沉默|停顿|"
    r"略作|稍作|思索|思考片刻|沉吟|叹息|叹了|微微|抬眼|"
    r"看向|望向|垂眸|摇头|点头|温柔地|平静地|缓缓地|带着|"
    r"带点|略带|压低声音|抿唇|轻轻)"
)
_ROLE = re.compile(r"^(?:庄方宜|ZhuangFY|助手)\s*[：:]\s*", re.I)
_LIST = re.compile(r"^\s*(?:[-*+] |\d+[.)] |> ?)")


def remove_leading_stage_cues(text: str) -> str:
    """Strip short leading acted directions, not meaningful parentheticals."""
    text = _ROLE.sub("", text.lstrip("\ufeff \t\r\n"), count=1)
    for _ in range(3):
        text = text.lstrip(" \t\r\n")
        stars = 2 if text.startswith("**") else (1 if text.startswith("*") else 0)
        match = _STAGE.match(text[stars:])
        if not match:
            break
        cue = next((v for v in match.groups() if v is not None), "").strip()
        if not _STAGE_MARKER.search(cue):
            break
        end = stars + match.end()
        if stars and text[end:end + stars] != "*" * stars:
            break
        text = text[end + stars:].lstrip(" \t\r\n，,：:")
    return text


def clean_reply(text: str) -> str:
    """Normalize fake Markdown hard wraps without modifying fenced code."""
    text = remove_leading_stage_cues(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    # Only strip Markdown line-ending double spaces outside literal code.
    lines = text.split("\n")
    result: list[str] = []
    fenced = False
    for raw in lines:
        if raw.lstrip().startswith((FENCE, "~~~")):
            fenced = not fenced
            result.append(raw)
            continue
        if fenced:
            result.append(raw)
            continue
        line = raw.strip()
        if line and not _LIST.match(line):
            line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
        if (line and result and result[-1] and not _LIST.match(line)
                and not _LIST.match(result[-1]) and not line.startswith("#")
                and not result[-1].startswith("#")):
            previous = result[-1]
            joiner = " " if (previous[-1].isascii() and previous[-1].isalnum()
                             and line[0].isascii() and line[0].isalnum()) else ""
            result[-1] = previous + joiner + line
        else:
            result.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(result)).strip()


def _paragraphs(text: str) -> list[str]:
    result, buffer = [], []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith((FENCE, "~~~")):
            fenced = not fenced
        if not line.strip() and not fenced and buffer:
            result.append("\n".join(buffer).strip())
            buffer = []
        elif line.strip() or fenced:
            buffer.append(line)
    if buffer:
        result.append("\n".join(buffer).strip())
    return [p for p in result if p]


def _split_sentences(text: str) -> list[str]:
    if FENCE in text or "~~~" in text or "\n" in text or _LIST.match(text):
        return [text]
    pieces = [p.strip() for p in re.split(r"(?<=[。！？!?；;])", text) if p.strip()]
    if len(pieces) < 2:
        return [text]
    groups, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) > SOFT_PART_CHARS:
            groups.append(current)
            current = piece
        else:
            current += piece
    if current:
        groups.append(current)
    return groups


def split_private_reply(text: str, max_parts: int = MAX_PARTS) -> list[str]:
    if not text:
        return []
    max_parts = max(1, min(max_parts, MAX_PARTS))
    paragraphs = _paragraphs(text)
    if len(paragraphs) == 1 and len(text) <= SPLIT_SINGLE_AFTER:
        return [text]
    if len(paragraphs) > 1 and len(text) < 120:
        return [text]
    groups: list[str] = []
    for paragraph in paragraphs:
        segments = _split_sentences(paragraph) if len(paragraph) > SPLIT_SINGLE_AFTER else [paragraph]
        for segment in segments:
            if (groups and len(groups[-1]) + len(segment) + 2 <= SOFT_PART_CHARS
                    and (len(groups) >= max_parts or len(segment) < 42)):
                groups[-1] += "\n\n" + segment
            else:
                groups.append(segment)
    while len(groups) > max_parts:
        # Preserve original order; merge the shortest neighboring messages.
        index = min(range(len(groups) - 1), key=lambda i: len(groups[i]) + len(groups[i + 1]))
        groups[index:index + 2] = [groups[index] + "\n\n" + groups[index + 1]]
    return groups


def _compact_plain_bubble(text: str) -> str:
    """Keep ordinary QQ prose readable without a blank row inside one bubble."""
    if FENCE in text or "~~~" in text or any(_LIST.match(line) for line in text.split("\n")):
        return text
    return re.sub(r"\n{2,}", "\n", text)


def format_qq_reply(reply: str, *, private: bool,
                    max_parts: int = MAX_PARTS) -> tuple[str, list[str]]:
    """One cleaned logical answer and 1-3 physical messages."""
    cleaned = clean_reply(reply[:MAX_QQ_CHARS])
    if not cleaned:
        return "", []
    parts = split_private_reply(cleaned, max_parts) if private else [cleaned]
    parts = [_compact_plain_bubble(part) for part in parts]
    return "\n\n".join(parts), parts
