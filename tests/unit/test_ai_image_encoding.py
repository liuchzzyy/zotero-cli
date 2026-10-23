"""压缩候选必须保持解码像素与科学图细节。"""

from io import BytesIO

from PIL import Image, ImageChops, ImageDraw
from tests.support import jpeg_bytes

from zotero_cli.ai_notes.images import encode_image


def test_lossless_reencoding_preserves_labels_and_pixels(tmp_path):
    path = tmp_path / "figure.png"
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((45, 30), "Fig S1: current = 0.1 A g-1; scale bar 100 nm", fill="black")
    for y in range(100, 700, 7):
        draw.line((100, y, 1100, y + 1), fill="black", width=1)
    image.save(path, compress_level=0)
    original = path.read_bytes()
    candidate = encode_image(path, 200_000)
    assert candidate is not None
    assert candidate.encoding == "lossless_png"
    assert len(candidate.data) < len(original)
    with Image.open(BytesIO(candidate.data)) as decoded:
        assert decoded.size == image.size
        assert ImageChops.difference(image, decoded).getbbox() is None
    assert path.read_bytes() == original


def test_lossless_reencoding_preserves_alpha_and_palette(tmp_path):
    for mode in ("RGBA", "P"):
        path = tmp_path / f"{mode}.png"
        image = Image.new("RGBA", (300, 200), (45, 120, 200, 75))
        ImageDraw.Draw(image).text((12, 12), "Scale 20 nm", fill=(0, 0, 0, 255))
        if mode == "P":
            image = image.quantize(colors=16)
        image.save(path, compress_level=0)
        candidate = encode_image(path, 20_000)
        assert candidate is not None
        assert candidate.encoding == "lossless_png"
        with Image.open(BytesIO(candidate.data)) as decoded:
            assert decoded.size == image.size
            assert decoded.convert("RGBA").tobytes() == image.convert("RGBA").tobytes()


def test_lossless_reencoding_preserves_16bit_values(tmp_path):
    path = tmp_path / "16bit.png"
    image = Image.new("I;16", (200, 100))
    image.putpixel((0, 0), 65535)
    image.putpixel((100, 50), 32769)
    image.save(path, compress_level=0)
    candidate = encode_image(path, 5_000)
    assert candidate is not None
    assert candidate.encoding == "lossless_png"
    with Image.open(BytesIO(candidate.data)) as decoded:
        assert decoded.getpixel((0, 0)) == 65535
        assert decoded.getpixel((100, 50)) == 32769
        assert decoded.tobytes() == image.tobytes()


def test_corrupt_under_limit_image_is_not_accepted(tmp_path):
    path = tmp_path / "corrupt.jpg"
    path.write_bytes(b"not a jpeg" * 200)
    assert encode_image(path, 5 * 1024 * 1024) is None


def test_false_extension_is_rejected(tmp_path):
    path = tmp_path / "mislabeled.png"
    path.write_bytes(jpeg_bytes())
    assert encode_image(path, 5 * 1024 * 1024) is None
