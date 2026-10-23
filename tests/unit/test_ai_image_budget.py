"""发送前图片压缩与双预算选择的真实图片回归测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from zotero_cli.ai_notes import evidence as limits
from zotero_cli.ai_notes.errors import EvidenceBudgetError
from zotero_cli.ai_notes.evidence import build_evidence, plan_request


def _document(tmp_path: Path, kind: str, images: list[bytes]):
    root = tmp_path / kind
    image_dir = root / "raw" / "images"
    image_dir.mkdir(parents=True)
    blocks = []
    for index, data in enumerate(images):
        (image_dir / f"{index}.jpg").write_bytes(data)
        blocks.append({"type": "image", "img_path": f"images/{index}.jpg", "page_idx": index})
    return kind.upper(), kind, SimpleNamespace(root=root, content_list=blocks)


def _jpeg(tmp_path: Path) -> bytes:
    image = Image.new("RGB", (800, 500), (95, 110, 125))
    target = tmp_path / "source.jpg"
    image.save(target, format="JPEG", quality=95)
    return target.read_bytes()


def test_oversized_main_reencodes_without_changing_original(tmp_path, monkeypatch):
    original = _jpeg(tmp_path) + b"x" * 80_000
    monkeypatch.setattr(limits, "MAX_IMAGE_BYTES", 30_000)
    document = _document(tmp_path, "main", [original])
    source = document[2].root / "raw" / "images" / "0.jpg"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = plan_request(build_evidence([document]), "prompt", "group/deepseek")
    assert len(plan.images) == 1
    assert len(plan.images[0][1]) < 30_000 * 4 // 3 + 100
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_uncompressible_main_fails_without_sending(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_IMAGE_BYTES", 100)
    document = _document(tmp_path, "main", [_jpeg(tmp_path) + b"x" * 80_000])
    with pytest.raises(EvidenceBudgetError, match="required main image"):
        plan_request(build_evidence([document]), "prompt", "group/deepseek")


def test_si_byte_budget_uses_available_space_without_dropping_main(tmp_path, monkeypatch):
    image = _jpeg(tmp_path)
    main = _document(tmp_path, "main", [image])
    si = _document(tmp_path, "supplementary", [image] * 5)
    prompt = "文献证据"
    from zotero_cli.ai_notes.evidence import _image_record, _payload_bytes
    from zotero_cli.ai_notes.images import encode_image

    smallest = encode_image(main[2].root / "raw" / "images" / "0.jpg", limits.MAX_IMAGE_BYTES, force=True)
    assert smallest is not None
    capacity = _payload_bytes(prompt, [_image_record(1, smallest), _image_record(2, smallest)]) + 100
    monkeypatch.setattr(limits, "MAX_REQUEST_BYTES", capacity)
    plan = plan_request(build_evidence([main, si]), prompt, "group/deepseek")
    assert plan.images[0][0] == 1
    assert 0 < len(plan.si_images) < 5
    assert plan.request_bytes <= limits.MAX_REQUEST_BYTES


def test_main_byte_budget_excess_fails(tmp_path, monkeypatch):
    main = _document(tmp_path, "main", [_jpeg(tmp_path)])
    monkeypatch.setattr(limits, "MAX_REQUEST_BYTES", 100)
    with pytest.raises(EvidenceBudgetError, match="request body"):
        plan_request(build_evidence([main]), "prompt", "group/deepseek")
