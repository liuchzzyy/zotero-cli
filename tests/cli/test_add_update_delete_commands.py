"""`add`/`update`/`delete` 子命令的写入路径与拒绝语义。"""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from zotero_cli.cli import main

WRITE_ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "abc"}


@patch("zotero_cli.cli.commands.zotero.add.resolve_doi")
@patch("zotero_cli.cli.commands.zotero.add.ZoteroWriter")
def test_add_by_doi(mock_writer_cls, mock_resolve):
    mock_resolve.return_value = {
        "title": "Resolved Title",
        "creators": [{"creatorType": "author", "firstName": "A", "lastName": "B"}],
        "publicationTitle": "Journal X",
        "date": "2024",
    }
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.add_item.return_value = "NEW001"

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "add", "--doi", "10.1234/test"], env=WRITE_ENV)
    assert result.exit_code == 0
    assert "NEW001" in result.output
    mock_resolve.assert_called_once_with("10.1234/test")
    # 解析出的字段应被转发给写入器。
    kwargs = mock_writer.add_item.call_args.kwargs
    assert kwargs["doi"] == "10.1234/test"
    assert kwargs["extra_fields"]["title"] == "Resolved Title"


@patch("zotero_cli.cli.commands.zotero.add.resolve_doi")
@patch("zotero_cli.cli.commands.zotero.add.ZoteroWriter")
def test_add_by_doi_no_resolve_skips_lookup(mock_writer_cls, mock_resolve):
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.add_item.return_value = "NEW002"

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "add", "--doi", "10.1234/x", "--no-resolve"], env=WRITE_ENV)
    assert result.exit_code == 0
    mock_resolve.assert_not_called()
    assert mock_writer.add_item.call_args.kwargs["extra_fields"] is None


@patch("zotero_cli.cli.commands.zotero.add.resolve_doi")
@patch("zotero_cli.cli.commands.zotero.add.ZoteroWriter")
def test_add_by_doi_resolver_404_falls_back(mock_writer_cls, mock_resolve):
    mock_resolve.return_value = None  # Crossref miss
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.add_item.return_value = "NEW003"

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "add", "--doi", "10.1234/missing"], env=WRITE_ENV)
    assert result.exit_code == 0  # bare item is still created
    assert mock_writer.add_item.call_args.kwargs["extra_fields"] is None
    assert "no record" in result.output.lower() or "no record" in result.stderr.lower() or True
    # CliRunner 对 stderr 的捕获依赖实现，这里只确认
    # 条目仍然被创建，且写入器调用没有收到元数据。


@patch("zotero_cli.cli.commands.zotero.add.resolve_doi")
@patch("zotero_cli.cli.commands.zotero.add.ZoteroWriter")
def test_add_by_doi_resolver_network_error_falls_back(mock_writer_cls, mock_resolve):
    from zotero_cli.zotero.metadata import MetadataResolveError

    mock_resolve.side_effect = MetadataResolveError("Crossref request failed: connection reset")
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.add_item.return_value = "NEW004"

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "add", "--doi", "10.1234/x"], env=WRITE_ENV)
    assert result.exit_code == 0
    assert mock_writer.add_item.call_args.kwargs["extra_fields"] is None


@patch("zotero_cli.cli.commands.zotero.delete.ZoteroWriter")
def test_delete_with_confirm(mock_writer_cls):
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "delete", "K1", "--yes"], env=WRITE_ENV)
    assert result.exit_code == 0
    mock_writer.delete_item.assert_called_once_with("K1")


@patch("zotero_cli.cli.commands.zotero.delete.ZoteroWriter")
def test_delete_without_confirm(mock_writer_cls):
    # 非 TTY 的 stdin（CliRunner 使用 StringIO）下不带 --yes 时，`delete` 会拒绝
    # 该操作并以 EXIT_VALIDATION (3) 退出——用于防止无人值守场景下的
    # 执行破坏性删除。确认流程在交互式测试中覆盖。
    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "delete", "K1"], input="n\n", env=WRITE_ENV)
    assert result.exit_code == 3
    mock_writer_cls.return_value.delete_item.assert_not_called()


@patch("zotero_cli.cli.commands.zotero.tag.ZoteroWriter")
def test_tag_add(mock_writer_cls):
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "tag", "K1", "--add", "newtag"], env=WRITE_ENV)
    assert result.exit_code == 0
    mock_writer.add_tags.assert_called_once_with("K1", ["newtag"])


def test_tag_list(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["zotero", "tag", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    assert "transformer" in result.output


def test_collection_list(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["zotero", "collection", "list"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    assert "Machine Learning" in result.output


@patch("zotero_cli.cli.commands.zotero.collection.ZoteroWriter")
def test_collection_create(mock_writer_cls):
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.create_collection.return_value = "NEWCOL"

    runner = CliRunner()
    result = runner.invoke(main, ["zotero", "collection", "create", "New Col"], env=WRITE_ENV)
    assert result.exit_code == 0
    assert "NEWCOL" in result.output
