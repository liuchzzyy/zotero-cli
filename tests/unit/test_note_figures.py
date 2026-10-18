"""Figures reach the model, and a text-only model degrades instead of failing."""

from __future__ import annotations

import base64
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_cli.config import AiNoteConfig
from zotero_cli.core.notes.client import AiClient
from zotero_cli.core.notes.figures import load_figures, mark_figures

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8000  # > _MIN_BYTES, real magic number
_FIGURE = [(1, "data:image/png;base64,AAA")]


def _write_figure(raw_dir: Path, name: str, payload: bytes = _PNG) -> Path:
    target = raw_dir / "images" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


class TestMarkFigures:
    def test_numbering_follows_document_order(self, tmp_path):
        md = "text\n\n![](images/a.jpg)\n\nFig 1 caption\n\n![](images/b.png)\n"
        text, figures = mark_figures(md, tmp_path)
        assert text.count("【图 1】") == 1 and text.count("【图 2】") == 1
        assert "![](" not in text
        assert [n for n, _ in figures] == [1, 2]
        assert [p.name for _, p in figures] == ["a.jpg", "b.png"]

    def test_start_continues_numbering_across_pdfs(self, tmp_path):
        _, first = mark_figures("![](images/a.jpg)", tmp_path)
        text, second = mark_figures("![](images/b.jpg)", tmp_path, start=len(first) + 1)
        assert "【图 2】" in text
        assert [n for n, _ in second] == [2]

    def test_reference_resolves_under_raw_dir(self, tmp_path):
        _, figures = mark_figures("![](images/a.jpg)", tmp_path)
        assert figures[0][1] == tmp_path / "images" / "a.jpg"

    @pytest.mark.parametrize("md", ["no images here", "![](images/x.jpg"])
    def test_text_without_usable_refs_is_unchanged(self, md, tmp_path):
        text, figures = mark_figures(md, tmp_path)
        assert figures == []
        assert text == md


class TestLoadFigures:
    def test_loads_real_file_as_data_url(self, tmp_path):
        path = _write_figure(tmp_path, "a.png")
        loaded = load_figures([(1, path)], limit=12)
        assert len(loaded) == 1
        number, url = loaded[0]
        assert number == 1
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == path.read_bytes()

    def test_skips_missing_tiny_and_oversized_files(self, tmp_path):
        missing = tmp_path / "images" / "gone.jpg"
        tiny = _write_figure(tmp_path, "tiny.jpg", b"short")
        huge = _write_figure(tmp_path, "huge.jpg", b"\xff" * (5 * 1024 * 1024 + 1))
        ok = _write_figure(tmp_path, "ok.jpg", b"\xff" * 9000)
        loaded = load_figures([(1, missing), (2, tiny), (3, huge), (4, ok)], limit=12)
        assert [n for n, _ in loaded] == [4]

    def test_skips_unsupported_suffix(self, tmp_path):
        assert load_figures([(1, _write_figure(tmp_path, "a.svg"))], limit=12) == []

    def test_limit_caps_the_payload(self, tmp_path):
        refs = [(i, _write_figure(tmp_path, f"f{i}.jpg", b"\xff" * 9000)) for i in range(1, 21)]
        assert len(load_figures(refs, limit=12)) == 12

    def test_jpeg_mime_is_not_mislabelled(self, tmp_path):
        path = _write_figure(tmp_path, "p.jpeg", b"\xff\xd8\xff" + b"\x00" * 8000)
        assert load_figures([(1, path)], limit=12)[0][1].startswith("data:image/jpeg;base64,")


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # the client mutates kwargs in place on retry
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _response(outcome)


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client(outcomes):
    """Return a client whose transport replays ``outcomes`` (text or exception)."""
    client = AiClient(AiNoteConfig(model="m1", api_key="k", base_url="http://127.0.0.1:1/v1"))
    completions = _FakeCompletions(outcomes)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


class TestAiClientFigures:
    def test_text_only_prompt_stays_a_string(self):
        client, completions = _client(["ok"])
        client.chat("prompt")
        assert completions.calls[0]["messages"][1]["content"] == "prompt"

    def test_figures_become_image_parts_in_order(self):
        client, completions = _client(["ok"])
        client.chat("prompt", figures=_FIGURE)
        content = completions.calls[0]["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": "prompt"}
        assert content[1]["type"] == "text" and "图 1" in content[1]["text"]
        assert content[2] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}

    def test_text_only_model_retries_without_images(self):
        rejection = Exception("BadRequestError: image input is not supported")
        client, completions = _client([rejection, "degraded ok"])
        assert client.chat("prompt", figures=_FIGURE) == "degraded ok"
        assert isinstance(completions.calls[0]["messages"][1]["content"], list)
        assert completions.calls[1]["messages"][1]["content"] == "prompt"

    def test_unrelated_error_skips_the_image_retry_and_uses_fallback(self):
        client, completions = _client([Exception("rate limited"), "from m2"])
        client.config.fallback_models = ["m2"]
        assert client.chat("prompt", figures=_FIGURE) == "from m2"
        # no text-only retry: the first error had nothing to do with images
        assert len(completions.calls) == 2
        assert [c["model"] for c in completions.calls] == ["m1", "m2"]
        assert isinstance(completions.calls[1]["messages"][1]["content"], list)

    def test_fallback_model_still_gets_figures_after_a_text_only_one(self):
        rejection = Exception("image input is not supported")
        client, completions = _client([rejection, Exception("boom"), "from m2"])
        client.config.fallback_models = ["m2"]
        assert client.chat("prompt", figures=_FIGURE) == "from m2"
        # m1: images then text-only; m2 must still receive the image parts
        assert isinstance(completions.calls[0]["messages"][1]["content"], list)
        assert completions.calls[1]["messages"][1]["content"] == "prompt"
        assert isinstance(completions.calls[2]["messages"][1]["content"], list)
