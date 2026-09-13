from zotero_cli_agent.models import Collection, Creator, Item, SearchResult


def test_creator_full_name():
    c = Creator(first_name="John", last_name="Doe", creator_type="author")
    assert c.full_name == "John Doe"


def test_creator_full_name_last_only():
    c = Creator(first_name="", last_name="Doe", creator_type="author")
    assert c.full_name == "Doe"


def test_item_creation():
    item = Item(
        key="ABC123",
        item_type="journalArticle",
        title="Test Paper",
        creators=[Creator("Jane", "Doe", "author")],
        abstract="An abstract.",
        date="2025",
        url=None,
        doi="10.1234/test",
        tags=["ML", "NLP"],
        collections=["COL1"],
        date_added="2025-01-01",
        date_modified="2025-01-02",
        extra={},
    )
    assert item.key == "ABC123"
    assert item.creators[0].full_name == "Jane Doe"


def test_collection_tree():
    child = Collection(key="C2", name="Sub", parent_key="C1", children=[])
    parent = Collection(key="C1", name="Root", parent_key=None, children=[child])
    assert parent.children[0].name == "Sub"


def test_search_result():
    sr = SearchResult(items=[], total=0, query="test")
    assert sr.total == 0
