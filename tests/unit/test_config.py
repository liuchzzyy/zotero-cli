import sys
from pathlib import Path

from zotero_cli.config import (
    AppConfig,
    EmbeddingConfig,
    RerankConfig,
    config_file_path,
    detect_zotero_data_dir,
    get_default_profile,
    list_profiles,
    load_ai_note_config,
    load_config,
    load_embedding_config,
    load_pdf_config,
    load_rerank_config,
    load_semantic_search_config,
    load_vector_store_config,
    project_root,
    save_config,
    state_dir,
)


def test_default_config():
    cfg = AppConfig()
    assert cfg.library_id == ""
    assert cfg.api_key == ""
    assert cfg.default_format == "table"
    assert cfg.default_limit == 50
    assert cfg.default_export_style == "bibtex"


def test_save_and_load_config(tmp_path):
    config_path = tmp_path / "config.toml"
    cfg = AppConfig(library_id="123", api_key="abc")
    save_config(cfg, config_path)
    loaded = load_config(config_path)
    assert loaded.library_id == "123"
    assert loaded.api_key == "abc"


def test_load_missing_config(tmp_path):
    config_path = tmp_path / "nonexistent.toml"
    cfg = load_config(config_path)
    assert cfg.library_id == ""


def test_detect_zotero_data_dir_with_override(tmp_path):
    db = tmp_path / "zotero.sqlite"
    db.touch()
    cfg = AppConfig(data_dir=str(tmp_path))
    result = detect_zotero_data_dir(cfg)
    assert result == tmp_path


def test_detect_zotero_data_dir_default(monkeypatch):
    result = detect_zotero_data_dir(AppConfig())
    if sys.platform == "win32":
        assert "Zotero" in str(result)
    else:
        assert result == Path.home() / "Zotero"


def test_config_has_write_credentials():
    cfg = AppConfig(library_id="123", api_key="abc")
    assert cfg.has_write_credentials is True
    cfg2 = AppConfig()
    assert cfg2.has_write_credentials is False


def test_get_data_dir_env_override(tmp_path, monkeypatch):
    from zotero_cli.config import get_data_dir

    monkeypatch.setenv("ZOT_DATA_DIR", str(tmp_path))
    cfg = AppConfig(data_dir="/some/other/path")
    result = get_data_dir(cfg)
    assert result == tmp_path


def test_get_data_dir_falls_back_to_config(monkeypatch, tmp_path):
    from zotero_cli.config import get_data_dir

    monkeypatch.delenv("ZOT_DATA_DIR", raising=False)
    cfg = AppConfig(data_dir=str(tmp_path))
    result = get_data_dir(cfg)
    assert result == tmp_path


def test_config_file_path_ignores_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOT_CONFIG_PATH", str(tmp_path / "config.toml"))
    root = project_root()
    assert config_file_path() == root / ".zot" / "config.toml"


def test_project_root_defaults_to_source_repo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert project_root() != tmp_path
    assert config_file_path() == project_root() / ".zot" / "config.toml"


# --- Multi-profile tests ---


def test_load_config_with_profile(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[default]
profile = "lab"

[profile.personal]
library_id = "111"
api_key = "aaa"

[profile.lab]
data_dir = "/shared/zotero"
library_id = "222"
api_key = "bbb"
""")
    cfg = load_config(config_file, profile="lab")
    assert cfg.library_id == "222"
    assert cfg.api_key == "bbb"
    assert cfg.data_dir == "/shared/zotero"


def test_load_config_default_profile(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[default]
profile = "personal"

[profile.personal]
library_id = "111"
api_key = "aaa"
""")
    cfg = load_config(config_file)
    assert cfg.library_id == "111"


def test_load_config_no_profiles_backward_compat(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[zotero]
library_id = "old"
api_key = "old_key"
""")
    cfg = load_config(config_file)
    assert cfg.library_id == "old"


def test_list_profiles_func(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[default]
profile = "personal"

[profile.personal]
library_id = "111"

[profile.lab]
library_id = "222"
""")
    profiles = list_profiles(config_file)
    assert set(profiles) == {"personal", "lab"}


def test_get_default_profile_func(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[default]
profile = "lab"

[profile.lab]
library_id = "222"
""")
    assert get_default_profile(config_file) == "lab"


def test_save_and_load_config_with_backslashes(tmp_path):
    """Ensure Windows-style paths with backslashes survive save/load round-trip."""
    config_path = tmp_path / "config.toml"
    windows_path = r"C:\Users\testuser\Zotero"
    cfg = AppConfig(data_dir=windows_path, library_id="123", api_key="abc")
    save_config(cfg, config_path)
    loaded = load_config(config_path)
    assert loaded.data_dir == windows_path
    assert loaded.library_id == "123"
    assert loaded.api_key == "abc"


def test_save_and_load_config_escapes_toml_strings(tmp_path):
    config_path = tmp_path / "config.toml"
    cfg = AppConfig(
        data_dir="/tmp/O'Brien/Zotero\\library",
        library_id='user"123',
        api_key="key'with\"quotes",
    )
    save_config(cfg, config_path)
    loaded = load_config(config_path)
    assert loaded.data_dir == cfg.data_dir
    assert loaded.library_id == cfg.library_id
    assert loaded.api_key == cfg.api_key


# --- Embedding config (Gitee API only) ---


def test_load_embedding_config_from_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[embedding]
provider = "gitee"
url = "https://ai.gitee.com/v1"
api_key = "test-key"
model = "bge-m3"
batch_size = 50
""")
    cfg = load_embedding_config(path=config_file)
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1"
    assert cfg.api_key == "test-key"
    assert cfg.model == "bge-m3"
    assert cfg.batch_size == 50
    assert cfg.is_configured is True


def test_load_embedding_config_from_active_profile(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[embedding]
active = "api.gitee"

[embedding.api.gitee]
provider = "gitee"
url = "https://ai.gitee.com/v1"
api_key = "gitee-key"
model = "bge-m3"
batch_size = 10
""")
    cfg = load_embedding_config(path=config_file)
    assert cfg.active == "api.gitee"
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1"
    assert cfg.api_key == "gitee-key"
    assert cfg.model == "bge-m3"
    assert cfg.batch_size == 10


def test_load_embedding_config_defaults(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[zotero]\ndata_dir = '/tmp'\n")
    cfg = load_embedding_config(path=config_file)
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1"
    assert cfg.api_key == ""
    assert cfg.model == "bge-m3"
    assert cfg.batch_size == 50
    assert cfg.is_configured is False


def test_load_embedding_config_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[zotero]\ndata_dir = '/tmp'\n")
    monkeypatch.setenv("ZOT_EMBEDDING_PROVIDER", "gitee")
    monkeypatch.setenv("ZOT_EMBEDDING_URL", "https://ai.gitee.com/v1")
    monkeypatch.setenv("ZOT_EMBEDDING_KEY", "env-key")
    monkeypatch.setenv("ZOT_EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("ZOT_EMBEDDING_BATCH_SIZE", "3")
    cfg = load_embedding_config(path=config_file, apply_env_overrides=True)
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1"
    assert cfg.api_key == "env-key"
    assert cfg.model == "custom-model"
    assert cfg.batch_size == 3


def test_embedding_config_is_configured():
    assert EmbeddingConfig(url="http://x", api_key="k", model="m").is_configured is True
    assert EmbeddingConfig(url="http://x", api_key="", model="m").is_configured is False
    assert EmbeddingConfig(url="", api_key="k", model="m").is_configured is False


# --- Rerank config (Gitee API only) ---


def test_load_rerank_config_from_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[rerank]
provider = "gitee"
url = "https://ai.gitee.com/v1/rerank"
api_key = "rerank-key"
model = "bge-reranker-v2-m3"
batch_size = 16
""")
    cfg = load_rerank_config(path=config_file)
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1/rerank"
    assert cfg.api_key == "rerank-key"
    assert cfg.model == "bge-reranker-v2-m3"
    assert cfg.batch_size == 16
    assert cfg.is_configured is True


def test_load_rerank_config_from_active_profile(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[rerank]
active = "api.gitee"

[rerank.api.gitee]
provider = "gitee"
url = "https://ai.gitee.com/v1/rerank"
api_key = "gitee-key"
model = "bge-reranker-v2-m3"
batch_size = 16
""")
    cfg = load_rerank_config(path=config_file)
    assert cfg.active == "api.gitee"
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1/rerank"
    assert cfg.api_key == "gitee-key"
    assert cfg.model == "bge-reranker-v2-m3"
    assert cfg.batch_size == 16
    assert cfg.is_configured is True


def test_load_rerank_config_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[rerank]\nprovider = ''\n")
    monkeypatch.setenv("ZOT_RERANK_PROVIDER", "gitee")
    monkeypatch.setenv("ZOT_RERANK_URL", "https://ai.gitee.com/v1/rerank")
    monkeypatch.setenv("ZOT_RERANK_KEY", "rerank-key")
    monkeypatch.setenv("ZOT_RERANK_MODEL", "custom-reranker")
    monkeypatch.setenv("ZOT_RERANK_BATCH_SIZE", "3")
    cfg = load_rerank_config(path=config_file, apply_env_overrides=True)
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1/rerank"
    assert cfg.api_key == "rerank-key"
    assert cfg.model == "custom-reranker"
    assert cfg.batch_size == 3


def test_rerank_config_defaults():
    cfg = RerankConfig()
    assert cfg.provider == "gitee"
    assert cfg.url == "https://ai.gitee.com/v1/rerank"
    assert cfg.model == "bge-reranker-v2-m3"
    assert cfg.batch_size == 16


# --- Vector store + semantic search config ---


def test_load_vector_store_config(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[vector_store]
provider = "qdrant_local"
path = ".workspace/_qdrant"
""")
    cfg = load_vector_store_config(config_file)
    assert cfg.provider == "qdrant_local"
    assert cfg.path == ".workspace/_qdrant"


def test_load_vector_store_config_defaults(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[zotero]\ndata_dir = '/tmp'\n")
    cfg = load_vector_store_config(config_file)
    assert cfg.provider == "qdrant_local"
    assert cfg.path == ".workspace/_qdrant"


def test_load_semantic_search_config(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[semantic_search]
candidate_k = 200
top_k = 10
rrf_k = 30
semantic_weight = 0.7
bm25_weight = 0.3
""")
    cfg = load_semantic_search_config(config_file)
    assert cfg.candidate_k == 200
    assert cfg.top_k == 10
    assert cfg.rrf_k == 30
    assert cfg.semantic_weight == 0.7
    assert cfg.bm25_weight == 0.3


def test_load_semantic_search_config_defaults(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[zotero]\ndata_dir = '/tmp'\n")
    cfg = load_semantic_search_config(config_file)
    assert cfg.candidate_k == 150
    assert cfg.top_k == 20
    assert cfg.rrf_k == 60
    assert cfg.semantic_weight == 0.8
    assert cfg.bm25_weight == 0.2


def test_repo_local_config_path():
    root = project_root()
    assert config_file_path() == root / ".zot" / "config.toml"


def test_repo_local_state_dir():
    root = project_root()
    assert state_dir() == root / ".zot" / "state"


def test_load_ai_note_config_from_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[ai_notes]
api_key = "test-key"
base_url = "https://api.example.com/v1"
model = "deepseek-v4-pro"
reasoning_effort = "max"
api_mode = "chat"
chat_token_param = "max_tokens"
temperature = 0.3
max_tokens = 16384
max_extracted_chars = 1234
""")
    cfg = load_ai_note_config(config_file)
    assert cfg.api_key == "test-key"
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.reasoning_effort == "max"
    assert cfg.api_mode == "chat"
    assert cfg.chat_token_param == "max_tokens"
    assert cfg.temperature == 0.3
    assert cfg.max_tokens == 16384
    assert cfg.max_extracted_chars == 1234


def test_load_pdf_config_is_mineru_only(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[pdf]\nmineru_token = "token"\nmineru_model_version = "vlm"\n',
        encoding="utf-8",
    )
    cfg = load_pdf_config(config_file, apply_env_overrides=False)
    assert cfg.mineru_token == "token"
    assert cfg.mineru_model_version == "vlm"
