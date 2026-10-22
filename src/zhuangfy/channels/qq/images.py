"""Fetch OneBot image references and prepare bounded JPEG input for vision."""

from __future__ import annotations

import base64
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter


MAX_DOWNLOAD_BYTES = 12_000_000
MAX_IMAGE_PIXELS = 25_000_000
MAX_MODEL_BYTES = 2_500_000


@dataclass(frozen=True)
class ImageReference:
    file: str
    url: str = ""


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme in {"http", "https"} and not parsed.username
            and not parsed.password and parsed.port in {None, 80, 443}
            and any(host == root or host.endswith("." + root)
                    for root in ("qq.com", "qq.com.cn", "qpic.cn", "gtimg.cn")))


class _QqRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise ValueError("图片下载跳转到了非 QQ 图片地址")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _download(url: str) -> bytes:
    if not _allowed_url(url):
        raise ValueError("图片地址不是受支持的 QQ 图片地址")
    opener = urllib.request.build_opener(_QqRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "ZhuangFY/0.1"})
    with opener.open(request, timeout=15) as response:
        size = response.headers.get("Content-Length")
        if size and int(size) > MAX_DOWNLOAD_BYTES:
            raise ValueError("图片超过 12 MB")
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("图片为空或超过 12 MB")
    return data


def _encode_jpeg(raw: bytes) -> bytes:
    source = QBuffer()
    source.setData(raw)
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS:
        raise ValueError("图片尺寸无效或超过 2500 万像素")
    if max(size.width(), size.height()) > 2048:
        reader.setScaledSize(size.scaled(2048, 2048, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull() or image.width() * image.height() > MAX_IMAGE_PIXELS:
        raise ValueError("无法读取图片")
    # JPEG has no alpha channel; place transparent PNGs on white.
    canvas = QImage(image.size(), QImage.Format.Format_RGB32)
    canvas.fill(Qt.GlobalColor.white)
    painter = QPainter(canvas)
    painter.drawImage(0, 0, image)
    painter.end()
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not canvas.save(output, "JPEG", 88):
        raise ValueError("无法处理图片")
    result = bytes(output.data())
    if len(result) > MAX_MODEL_BYTES:
        raise ValueError("处理后的图片仍超过 2.5 MB")
    return result


def image_data_url(url: str) -> str:
    jpeg = _encode_jpeg(_download(url))
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
