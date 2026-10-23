"""仅在发送前无损重编码图片；原始 MinerU 文件保持不变。"""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_PIXELS = 50_000_000
MAX_SOURCE_BYTES = 80 * 1024 * 1024
_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


@dataclass(frozen=True)
class EncodedImage:
    """发送候选及本地可审计参数；不包含原图副本。"""

    data: bytes
    mime: str
    width: int
    height: int
    source_bytes: int
    source_sha256: str
    encoding: str

    def manifest(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "source_bytes": self.source_bytes,
            "sent_bytes": len(self.data),
            "source_sha256": self.source_sha256,
            "sent_sha256": hashlib.sha256(self.data).hexdigest(),
            "encoding": self.encoding,
        }


def encode_image(path: Path, max_bytes: int, *, force: bool = False) -> EncodedImage | None:
    """验证解码后选取原图或无损候选；不降低分辨率或 JPEG 质量。"""
    if path.stat().st_size > MAX_SOURCE_BYTES:
        return None
    raw = path.read_bytes()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format != _FORMATS.get(path.suffix.lower()) or getattr(source, "n_frames", 1) != 1:
                    return None
                if source.width * source.height > MAX_PIXELS or max(source.size) > 50_000:
                    return None
                source.load()
                # 真正的 1x1 退化产物单独识别；不能用文件字节数误删小而有效的图。
                if source.width == 1 and source.height == 1:
                    return EncodedImage(b"", "", 1, 1, len(raw), hashlib.sha256(raw).hexdigest(), "placeholder")
                fmt = str(source.format)
                image = ImageOps.exif_transpose(source)
                mime = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}[fmt]
                digest = hashlib.sha256(raw).hexdigest()
                if not force and len(raw) <= max_bytes:
                    return EncodedImage(raw, mime, image.width, image.height, len(raw), digest, "original")
                # 用无损 PNG 重编码，保留像素、调色板、透明度与 ICC，避免损坏科学图细节。
                output = BytesIO()
                if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}:
                    return (
                        EncodedImage(raw, mime, image.width, image.height, len(raw), digest, "original")
                        if len(raw) <= max_bytes
                        else None
                    )
                image.save(output, format="PNG", optimize=True, icc_profile=source.info.get("icc_profile"))
                data = output.getvalue()
                if len(data) <= max_bytes and len(data) < len(raw):
                    return EncodedImage(data, "png", image.width, image.height, len(raw), digest, "lossless_png")
                if len(raw) <= max_bytes:
                    return EncodedImage(raw, mime, image.width, image.height, len(raw), digest, "original")
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None
    return None
