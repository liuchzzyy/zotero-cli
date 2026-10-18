"""Behavioral regressions for accurate schema, stream, citation and Rich output."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.core.workspace_store import Workspace
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.models import Creator, DuplicateGroup, Item, Note
from zotero_cli.output.renderers import format_duplicates, format_item_detail, format_items, format_workspace_list


def _item() -> Item:
    return Item(
        key="ITEM0001",
        item_type="journalArticle",
        title="Test article",
        creators=[],
        abstract=None,
        date="2026",
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="2026-01-01",
        date_modified="2026-01-01",
    )


@pytest.mark.parametrize(
    ("path", "tier"),
    [
        (("collection", "delete"), "destructive"),
        (("collection", "create"), "write"),
        (("collection", "list"), "read"),
        (("trash", "restore"), "write"),
        (("config", "profile", "list"), "read"),
        (("config", "init"), "write"),
        (("config", "profile", "set"), "write"),
        (("config", "cache", "clear"), "destructive"),
        (("workspace", "list"), "read"),
        (("workspace", "new"), "write"),
        (("workspace", "index"), "write"),
        (("workspace", "embed"), "write"),
        (("workspace", "delete"), "destructive"),
    ],
)
def test_schema_dotted_paths_match_spaced_paths(path: tuple[str, ...], tier: str) -> None:
    runner = CliRunner()
    spaced = runner.invoke(main, ["schema", *path])
    dotted = runner.invoke(main, ["schema", ".".join(path)])

    assert spaced.exit_code == 0, spaced.output
    assert dotted.exit_code == 0, dotted.output
    spaced_data = json.loads(spaced.stdout)["data"]
    dotted_data = json.loads(dotted.stdout)["data"]
    assert spaced_data["safety_tier"] == tier
    assert dotted_data["safety_tier"] == tier
    assert dotted_data == spaced_data
    assert dotted_data["name"] == " ".join(path)


@pytest.mark.parametrize("command", [["list"], ["search", ""]])
@pytest.mark.parametrize("limit", [0, 1, 100])
@pytest.mark.parametrize("global_limit", [False, True])
def test_stream_reports_remaining_filtered_results(
    test_db_path: Path, command: list[str], limit: int, global_limit: bool
) -> None:
    reader = ZoteroReader(test_db_path)
    try:
        matched = reader.search("", collection="Machine Learning", item_type="journalArticle", limit=100)
    finally:
        reader.close()
    assert matched.total == len(matched.items) > 1
    filters = ["--collection", "Machine Learning", "--type", "journalArticle"]
    limit_args = ["--limit", str(limit)]
    args = [*limit_args, *command] if global_limit else [*command, *limit_args]
    result = CliRunner().invoke(
        main,
        ["--detail", "minimal", *args, *filters, "--stream"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent)},
    )

    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.stdout.splitlines()]
    emitted = records[:-1]
    assert [record["data"]["key"] for record in emitted] == [item.key for item in matched.items[:limit]]
    assert all("abstract" not in record["data"] for record in emitted)
    assert records[-1]["summary"] == {
        "count": len(emitted),
        "has_more": len(emitted) < matched.total,
    }
    assert records[-1]["meta"]["request_id"]


@pytest.mark.parametrize("author_count", [1, 2, 19, 20, 21, 25])
def test_cite_apa_keeps_all_twenty_authors_before_abbreviation(author_count: int) -> None:
    creators = [Creator("Alice Beth", f"Author{i:02}", "author") for i in range(1, author_count + 1)]
    creators.insert(0, Creator("Eve", "Editor", "editor"))
    item = replace(_item(), creators=creators)
    names = [f"Author{i:02}, A. B." for i in range(1, author_count + 1)]
    if author_count == 1:
        expected = names[0]
    elif author_count == 2:
        expected = " & ".join(names)
    elif author_count <= 20:
        expected = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        expected = ", ".join(names[:19]) + ", ... " + names[-1]

    with patch("zotero_cli.commands.items.cite.ZoteroReader", autospec=True) as reader:
        reader.return_value.get_item.return_value = item
        result = CliRunner().invoke(main, ["cite", item.key, "--style", "apa", "--no-copy"])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{expected} (2026). Test article.\n"
    reader.return_value.close.assert_called_once()


@pytest.mark.parametrize("field", ["title", "creators", "abstract", "tags", "doi", "url", "extra", "notes"])
def test_item_detail_renders_external_metadata_literally(field: str) -> None:
    literal = "[/b] [bold]literal[/bold] [link=https://example.test]link[/link]"
    item = _item()
    notes: list[Note] = []
    if field == "creators":
        item.creators = [Creator("", literal, "author")]
    elif field == "tags":
        item.tags = [literal]
    elif field == "extra":
        item.extra = {"publicationTitle": literal, "custom[/b]": literal}
    elif field == "notes":
        notes = [Note("NOTE0001", item.key, literal)]
    else:
        setattr(item, field, literal)

    output = format_item_detail(item, notes, detail="full")

    assert literal in output
    if field == "extra":
        assert "custom[/b]: " in output
        assert "Metadata:" in output
    assert "[bold cyan]" not in output


@pytest.mark.parametrize("renderer", ["items", "duplicates", "workspaces"])
@pytest.mark.parametrize("literal", ["[/b]", "[b]X"])
def test_rich_table_cells_render_external_metadata_literally(renderer: str, literal: str) -> None:
    if renderer == "items":
        item = replace(
            _item(),
            key=literal,
            title=literal,
            creators=[Creator("", literal, "author")],
            date=literal,
            item_type=literal,
        )
        output = format_items([item])
        assert output.count(literal) == 5
    elif renderer == "duplicates":
        group = DuplicateGroup([replace(_item(), key=literal, title=literal)], literal, 1.0)
        output = format_duplicates([group])
        assert output.count(literal) == 3
    else:
        workspace = Workspace(name=literal, created=literal, description=literal)
        output = format_workspace_list([workspace])
        assert output.count(literal) == 3
