"""One-host/one-native-worker image handoff without untrusted file paths on IPC.

Raw QQ bytes are staged in the isolated Hybrid instance, addressed ONLY by a
SHA-256-derived asset_id. Native ImageManager remains sole owner of the final
image and description DB. Stage files can be deleted only after ingress ACK.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import uuid

MAX_IMAGE_BYTES = 12_000_000
ASSET = re.compile(r"^img_([0-9a-f]{64})$")


def _digest(asset_id: str) -> str:
    match = ASSET.fullmatch(asset_id) if isinstance(asset_id, str) else None
    if match is None:
        raise ValueError("invalid image asset ID")
    return match.group(1)


def _image_header(raw: bytes) -> bool:
    return (raw.startswith(b"\xff\xd8\xff") or
            raw.startswith(b"\x89PNG\r\n\x1a\n") or
            raw.startswith((b"GIF87a", b"GIF89a")) or
            (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))


def validate_image(raw: bytes) -> str:
    if not isinstance(raw, bytes) or not 32 <= len(raw) <= MAX_IMAGE_BYTES:
        raise ValueError("invalid or oversized image bytes")
    if not _image_header(raw):
        raise ValueError("unsupported image signature")
    return hashlib.sha256(raw).hexdigest()


def staging_dir(instance: Path) -> Path:
    return instance.resolve() / "incoming_images"


def stage_image(instance: Path, raw: bytes) -> str:
    digest = validate_image(raw)
    asset_id = "img_" + digest
    folder = staging_dir(instance)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (asset_id + ".bin")
    if path.is_symlink():
        raise ValueError("image stage symlink forbidden")
    if path.exists():
        # Existing file is never blindly trusted; heal corrupted stage content.
        try:
            with path.open("rb") as handle:
                previous = handle.read(MAX_IMAGE_BYTES + 1)
            if validate_image(previous) == digest:
                return asset_id
        except ValueError:
            pass
    temp = folder / ("." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return asset_id


def load_staged_image(instance: Path, asset_id: str) -> bytes:
    expected = _digest(asset_id)
    path = staging_dir(instance) / (asset_id + ".bin")
    # Reject symlinks; no user-supplied absolute path is used or resolved.
    if path.is_symlink():
        raise ValueError("image stage symlink forbidden")
    with path.open("rb") as file:
        raw = file.read(MAX_IMAGE_BYTES + 1)
    if validate_image(raw) != expected:
        raise ValueError("image content hash mismatch")
    return raw


def discard_staged_image(instance: Path, asset_id: str) -> None:
    _digest(asset_id)
    path = staging_dir(instance) / (asset_id + ".bin")
    if path.is_symlink():
        raise ValueError("image stage symlink forbidden")
    path.unlink(missing_ok=True)
