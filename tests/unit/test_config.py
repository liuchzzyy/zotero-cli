import math
import os
import stat
import sys
from pathlib import Path

import pytest

from zotero_cli.config import (
    AiNoteConfig,
    AppConfig,
    ConfigError,
    EmbeddingConfig,
    LibraryResolutionError,
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
    merge_config_data,
    project_root,
    read_config_data,
    resolve_config_path,
    resolve_library_id,
    save_config,
    state_dir,
    write_config_data,
)
from zotero_cli.config.loaders import _split_models


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


def test_empty_profile_behaves_like_default_profile(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[default]\nprofile = "lab"\n\n[profile.lab]\nlibrary_id = "222"\n',
        encoding="utf-8",
    )
    assert load_config(config_file, profile="").library_id == "222"


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
temperature = 0.3
max_tokens = 16384
max_extracted_chars = 1234
timeout_seconds = 90.5
max_retries = 4
""")
    cfg = load_ai_note_config(config_file, apply_env_overrides=False)
    assert cfg.api_key == "test-key"
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "deepseek-v4-pro"
    assert cfg.reasoning_effort == "max"
    assert cfg.temperature == 0.3
    assert cfg.max_tokens == 16384
    assert cfg.max_extracted_chars == 1234
    assert cfg.timeout_seconds == 90.5
    assert cfg.max_retries == 4
    assert not hasattr(cfg, "api_mode")
    assert not hasattr(cfg, "chat_token_param")


def test_load_ai_note_config_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[ai_notes]\napi_key = "file-key"\nbase_url = "http://file/v1"\nmodel = "file-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOT_AI_API_KEY", "env-key")
    monkeypatch.setenv("ZOT_AI_BASE_URL", "http://env/v1")
    monkeypatch.setenv("ZOT_AI_MODEL", "env-model")
    monkeypatch.setenv("ZOT_AI_FALLBACK_MODELS", "a-1, b-2")
    monkeypatch.setenv("ZOT_AI_REASONING_EFFORT", "low")
    monkeypatch.setenv("ZOT_AI_MAX_EXTRACTED_CHARS", "500")
    monkeypatch.setenv("ZOT_AI_TIMEOUT", "12.5")
    monkeypatch.setenv("ZOT_AI_MAX_RETRIES", "5")

    cfg = load_ai_note_config(config_file)
    assert cfg.api_key == "env-key"
    assert cfg.base_url == "http://env/v1"
    assert cfg.model == "env-model"
    assert cfg.fallback_models == ["a-1", "b-2"]
    assert cfg.reasoning_effort == "low"
    assert cfg.max_extracted_chars == 500
    assert cfg.timeout_seconds == 12.5
    assert cfg.max_retries == 5


def test_ai_note_config_timing_defaults():
    cfg = AiNoteConfig()
    assert cfg.timeout_seconds == 300.0
    assert cfg.max_retries == 2


def test_load_ai_note_config_env_overrides_without_config_file(tmp_path, monkeypatch):
    """The overrides must work when no `[ai_notes]` block exists at all."""
    monkeypatch.setenv("ZOT_AI_API_KEY", "env-key")
    monkeypatch.setenv("ZOT_AI_MODEL", "env-model")

    cfg = load_ai_note_config(tmp_path / "absent.toml")
    assert cfg.api_key == "env-key"
    assert cfg.model == "env-model"
    assert cfg.fallback_models  # defaults survive an unset variable


def test_split_models_accepts_array_string_and_junk():
    assert _split_models(["a", " b "]) == ["a", "b"]
    assert _split_models("a, b ,,") == ["a", "b"]
    assert _split_models("") is None
    assert _split_models(None) is None
    assert _split_models(42) is None


def test_load_pdf_config_is_mineru_only(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[pdf]\nmineru_token = "token"\nmineru_model_version = "vlm"\n',
        encoding="utf-8",
    )
    cfg = load_pdf_config(config_file, apply_env_overrides=False)
    assert cfg.mineru_token == "token"
    assert cfg.mineru_model_version == "vlm"


# --- H-04: merge-preserving atomic saves -------------------------------------

RICH_CONFIG = """
[default]
profile = "lab"

[profile.personal]
library_id = "111"
api_key = "aaa"

[profile.lab]
data_dir = "/old/data"
library_id = "222"
api_key = "old-key"
semantic_scholar_api_key = "s2-key"

[profile.lab.output]
default_format = "json"
limit = 25

[ai_notes]
api_key = "ai-key"
model = "file-model"

[pdf]
mineru_token = "mineru-token"

[embedding]
active = "api.gitee"

[embedding.api.gitee]
api_key = "emb-key"
batch_size = 10

[rerank.api.gitee]
api_key = "rerank-key"

[semantic_search]
candidate_k = 12
top_k = 5
rrf_k = 30

[vector_store]
path = "/tmp/vectors"
"""


def test_save_config_preserves_all_sections_and_active_profile(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(RICH_CONFIG, encoding="utf-8")
    original = config.read_text(encoding="utf-8")

    save_config(AppConfig(data_dir="/new/data", library_id="999", api_key="new-key"), config)

    loaded = load_config(config)
    assert loaded.library_id == "999"
    assert loaded.api_key == "new-key"
    assert loaded.data_dir == "/new/data"
    # Scalar defaults must not clobber values the caller did not touch.
    assert loaded.default_format == "json"
    assert loaded.default_limit == 25

    data = read_config_data(config)
    assert data["default"]["profile"] == "lab"
    assert data["profile"]["personal"] == {"library_id": "111", "api_key": "aaa"}
    assert data["profile"]["lab"]["api_key"] == "new-key"
    assert data["profile"]["lab"]["semantic_scholar_api_key"] == "s2-key"
    assert data["ai_notes"] == {"api_key": "ai-key", "model": "file-model"}
    assert data["pdf"] == {"mineru_token": "mineru-token"}
    assert data["embedding"]["api"]["gitee"]["api_key"] == "emb-key"
    assert data["rerank"]["api"]["gitee"]["api_key"] == "rerank-key"
    assert data["semantic_search"] == {"candidate_k": 12, "top_k": 5, "rrf_k": 30}
    assert data["vector_store"] == {"path": "/tmp/vectors"}

    backup = tmp_path / "config.toml.bak"
    assert backup.read_text(encoding="utf-8") == original


def test_save_config_never_drops_existing_credentials(tmp_path):
    config = tmp_path / "config.toml"
    save_config(AppConfig(library_id="123", api_key="secret-key"), config)

    save_config(AppConfig(library_id="123"), config)

    assert load_config(config).api_key == "secret-key"
    assert read_config_data(config)["zotero"]["api_key"] == "secret-key"


def test_save_config_private_permissions_and_atomic_write(tmp_path):
    config_dir = tmp_path / "nested" / ".zot"
    config = config_dir / "config.toml"
    save_config(AppConfig(library_id="1", api_key="secret"), config)

    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700

    save_config(AppConfig(library_id="2", api_key="secret2"), config)
    assert (config_dir / "config.toml.bak").exists()
    # No temp files left behind by the atomic write.
    assert sorted(path.name for path in config_dir.iterdir()) == ["config.toml", "config.toml.bak"]


def test_merge_config_data_reports_changed_keys():
    merged, changed = merge_config_data({}, AppConfig(library_id="42", api_key="k"))
    assert merged["zotero"]["library_id"] == "42"
    assert "zotero.library_id" in changed
    assert "zotero.api_key" in changed


def test_save_config_rejects_unknown_explicit_profile(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(RICH_CONFIG, encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown config profile"):
        save_config(AppConfig(library_id="1"), config, profile="typo")


def test_save_config_can_create_explicit_profile(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[zotero]\nlibrary_id = '1'\n", encoding="utf-8")
    save_config(AppConfig(library_id="7", api_key="seven"), config, profile="lab", allow_new_profile=True)
    data = read_config_data(config)
    assert data["profile"]["lab"]["library_id"] == "7"
    assert data["default"]["profile"] == "lab"
    assert data["zotero"]["library_id"] == "1"


# --- M-14: typed config errors ----------------------------------------------


def test_malformed_toml_raises_configuration_error(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[zotero\nlibrary_id = "1"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="Malformed TOML"):
        load_config(config)


@pytest.mark.parametrize(
    ("text", "loader_name", "match"),
    [
        ('profile = "not-a-table"\n', "config", r"\[profile\] must be a table"),
        ("[default]\nprofile = 5\n", "config", "profile must be a string"),
        ("[zotero]\nlibrary_id = 5\n", "config", "library_id must be a string"),
        ("[ai_notes]\nmodel = 3\n", "ai", "ai_notes.model must be a string"),
        ('[embedding]\nbatch_size = "many"\n', "embedding", "batch_size must be an integer"),
        ("[embedding]\nbatch_size = 0\n", "embedding", "batch_size must be >= 1"),
        ("[rerank]\nbatch_size = -1\n", "rerank", "batch_size must be >= 1"),
        ("[semantic_search]\nrrf_k = 0\n", "semantic", "rrf_k must be >= 1"),
        ("[semantic_search]\ncandidate_k = -3\n", "semantic", "candidate_k must be >= 1"),
        ("[semantic_search]\ntop_k = 0\n", "semantic", "top_k must be >= 1"),
        ('[semantic_search]\nsemantic_weight = "heavy"\n', "semantic", "semantic_weight must be a number"),
    ],
)
def test_invalid_config_types_raise_configuration_error(tmp_path, text, loader_name, match):
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    loaders = {
        "config": load_config,
        "ai": load_ai_note_config,
        "embedding": load_embedding_config,
        "rerank": load_rerank_config,
        "semantic": load_semantic_search_config,
    }
    with pytest.raises(ConfigError, match=match):
        loaders[loader_name](config)


def test_invalid_env_override_raises_configuration_error(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOT_EMBEDDING_BATCH_SIZE", "many")
    with pytest.raises(ConfigError, match="ZOT_EMBEDDING_BATCH_SIZE"):
        load_embedding_config(tmp_path / "absent.toml", apply_env_overrides=True)


def test_resolve_config_path_rejects_explicit_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Config file not found"):
        resolve_config_path(tmp_path / "missing.toml", must_exist=True)


def test_resolve_config_path_repo_default_may_be_missing(monkeypatch, tmp_path):
    missing = tmp_path / "missing.toml"
    monkeypatch.setattr("zotero_cli.config.paths.config_file_path", lambda start=None: missing)
    assert resolve_config_path() == missing


def test_save_config_activates_explicit_profile_without_default(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[zotero]\nlibrary_id = "1"\n\n[profile.lab]\nlibrary_id = "2"\n', encoding="utf-8")
    save_config(AppConfig(library_id="22"), config, profile="lab")

    data = read_config_data(config)
    assert data["default"]["profile"] == "lab"
    assert data["profile"]["lab"]["library_id"] == "22"
    assert load_config(config).library_id == "22"


# --- M-02: typed group-library resolution errors -----------------------------


def test_resolve_library_id_missing_group_raises_typed_error(monkeypatch, tmp_path):
    class FakeReader:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def resolve_group_library_id(self, _group_id: int) -> int | None:
            return None

        def close(self) -> None:
            pass

    monkeypatch.setattr("zotero_cli.zotero.reader.ZoteroReader", FakeReader)
    with pytest.raises(LibraryResolutionError) as error:
        resolve_library_id(tmp_path / "zotero.sqlite", {"library_type": "group", "group_id": "42"})
    assert error.value.code == "not_found"
    assert error.value.retryable is False
    assert "42" in str(error.value)


def test_resolve_library_id_user_library_does_not_open_the_db(monkeypatch, tmp_path):
    def fail(*_args: object, **_kwargs: object):
        raise AssertionError("user library resolution must not read the database")

    monkeypatch.setattr("zotero_cli.zotero.reader.ZoteroReader", fail)
    assert resolve_library_id(tmp_path / "missing.sqlite", {"library_type": "user"}) == 1


# --- M-14: env path expansion ------------------------------------------------


def test_zot_data_dir_env_is_expanded(monkeypatch, tmp_path):
    from zotero_cli.config import get_data_dir

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZOT_DATA_DIR", "~/ZoteroData")
    assert get_data_dir(AppConfig()) == tmp_path / "ZoteroData"


def test_zot_prefs_js_path_env_is_expanded(monkeypatch, tmp_path):
    from zotero_cli.config import get_prefs_js_path

    prefs = tmp_path / "prefs.js"
    prefs.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ZOT_PREFS_JS_PATH", "~/prefs.js")
    assert get_prefs_js_path(AppConfig()) == prefs


# --- task-31: pre-existing foreign/sticky parent directories ----------------


def test_save_config_accepts_preexisting_sticky_parent_without_chmod(tmp_path):
    sticky = tmp_path / "sticky"
    sticky.mkdir()
    os.chmod(sticky, 0o1777)
    before = stat.S_IMODE(sticky.stat().st_mode)

    config = sticky / "config.toml"
    save_config(AppConfig(library_id="42", api_key="secret-abc"), config)

    # The pre-existing parent keeps its (sticky) mode: it is not ours to chmod.
    assert stat.S_IMODE(sticky.stat().st_mode) == before
    assert stat.S_IMODE(config.stat().st_mode) == 0o600

    save_config(AppConfig(library_id="43", api_key="secret-abc"), config)
    backup = sticky / "config.toml.bak"
    assert backup.exists()


def test_save_config_ignores_chmod_permission_error_on_existing_parent(tmp_path, monkeypatch):
    parent = tmp_path / "foreign"
    parent.mkdir()
    os.chmod(parent, 0o1777)
    real_chmod = os.chmod
    calls: list[Path] = []

    def tracking_chmod(path: str | os.PathLike[str], mode: int, *args: object, **kwargs: object) -> None:
        target = Path(path)
        calls.append(target)
        if target == parent:
            raise PermissionError(1, "Operation not permitted", str(target))
        real_chmod(path, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "chmod", tracking_chmod)
    config = parent / "config.toml"
    save_config(AppConfig(library_id="42", api_key="secret-abc"), config)

    # A foreign/sticky parent must never be chmod-ed (that is what failed in /tmp).
    assert parent not in calls
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert load_config(config).library_id == "42"


def test_save_config_creates_new_parent_dirs_with_0700(tmp_path):
    created_dir = tmp_path / "created" / "config"
    save_config(AppConfig(library_id="1"), created_dir / "config.toml")
    assert stat.S_IMODE(created_dir.stat().st_mode) == 0o700


# --- H-07: invalid/blank default profile fails closed ------------------------

MIXED_TYPO_CONFIG = """
[default]
profile = "typo"

[zotero]
library_id = "legacy-999"
api_key = "legacy-secret"
"""


def test_invalid_default_profile_fails_closed_on_read(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(MIXED_TYPO_CONFIG, encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown config profile 'typo'"):
        load_config(path)


def test_invalid_default_profile_fails_closed_on_write(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(MIXED_TYPO_CONFIG, encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown config profile 'typo'"):
        save_config(AppConfig(library_id="42"), path)
    # The blocking write must leave the file untouched.
    assert read_config_data(path)["zotero"]["library_id"] == "legacy-999"


def test_profiles_without_default_profile_fails_closed(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="no default profile"):
        load_config(path)
    with pytest.raises(ConfigError, match="no default profile"):
        save_config(AppConfig(library_id="2"), path)


def test_blank_default_profile_fails_closed(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[default]\nprofile = "   "\n\n[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="no default profile"):
        load_config(path)


def test_default_profile_pointing_to_missing_profile_fails_closed(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[default]\nprofile = "gone"\n\n[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown config profile 'gone'"):
        load_config(path)


def test_legacy_config_without_profiles_still_loads(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[zotero]\nlibrary_id = "legacy"\n', encoding="utf-8")
    assert load_config(path).library_id == "legacy"


def test_blank_explicit_profile_uses_configured_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[default]\nprofile = "lab"\n\n[profile.lab]\nlibrary_id = "222"\n', encoding="utf-8")
    assert load_config(path, profile="   ").library_id == "222"


# --- M-19: TOML writer supports valid TOML value types -----------------------

EXTENDED_TYPES_CONFIG = """
[profile.main]
library_id = "1"
created = 2024-01-02T03:04:05.000006+00:00
day = 2024-01-02
clock = 03:04:05.000006
positive_inf = inf
negative_inf = -inf
not_a_number = nan

[profile.main.inline]
mode = "fast"
retries = 2

[[profile.main.entries]]
name = "a"

[[profile.main.entries]]
name = "b"
"""


def test_toml_writer_round_trips_extended_toml_types(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXTENDED_TYPES_CONFIG, encoding="utf-8")
    before = read_config_data(path)

    save_config(AppConfig(library_id="2"), path, profile="main")
    after = read_config_data(path)
    main = after["profile"]["main"]
    assert main["library_id"] == "2"
    for key in ("created", "day", "clock"):
        assert main[key] == before["profile"]["main"][key]
    assert math.isinf(main["positive_inf"]) and main["positive_inf"] > 0
    assert math.isinf(main["negative_inf"]) and main["negative_inf"] < 0
    assert math.isnan(main["not_a_number"])
    assert main["entries"] == before["profile"]["main"]["entries"]
    assert main["inline"] == before["profile"]["main"]["inline"]

    # The writer is deterministic: an unchanged save keeps the same bytes.
    first_bytes = path.read_bytes()
    save_config(AppConfig(library_id="2"), path, profile="main")
    assert path.read_bytes() == first_bytes


def test_toml_writer_reports_unsupported_values_actionably(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(ConfigError, match=r"extra\.value.*bytes"):
        write_config_data(path, {"extra": {"value": b"raw-bytes"}})
    assert not path.exists()


# --- M-20: invalid UTF-8 config is a configuration error ---------------------


def test_invalid_utf8_config_raises_configuration_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b'[zotero]\nlibrary_id = "\xff\xfe"\n')
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(path)


# --- M-24/M-18: no-op saves and symlinked config paths -----------------------


def test_write_config_data_returns_backup_and_skips_identical_content(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    data = read_config_data(path)

    backup = write_config_data(path, data)
    assert backup == path.with_name("config.toml.bak")
    assert backup is not None and backup.exists()
    file_mtime = path.stat().st_mtime_ns
    backup_mtime = backup.stat().st_mtime_ns

    assert write_config_data(path, data) is None
    assert path.stat().st_mtime_ns == file_mtime
    assert backup.stat().st_mtime_ns == backup_mtime


def test_save_config_writes_through_symlink_target(tmp_path):
    real_dir = tmp_path / "dotfiles"
    real_dir.mkdir()
    target = real_dir / "config.toml"
    target.write_text('[profile.main]\nlibrary_id = "orig"\n', encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)

    save_config(AppConfig(library_id="new"), link, profile="main")

    assert link.is_symlink()
    assert load_config(link).library_id == "new"
    backup = real_dir / "config.toml.bak"
    assert backup.exists()
    assert 'library_id = "orig"' in backup.read_text(encoding="utf-8")


# --- TOOL-01: profile-aware section loaders ----------------------------------

PROFILE_SECTIONS_CONFIG = """
[embedding]
active = "api.gitee"

[embedding.api.gitee]
provider = "gitee"
url = "https://top/v1"
api_key = "top-key"
model = "top-model"
batch_size = 20

[profile.lab.embedding.api.gitee]
url = "https://lab/v1"
api_key = "lab-key"

[rerank]
batch_size = 4

[profile.lab.rerank]
batch_size = 9

[vector_store]
provider = "qdrant_local"
path = "/top/vectors"

[profile.lab.vector_store]
path = "/lab/vectors"

[semantic_search]
candidate_k = 11

[profile.lab.semantic_search]
candidate_k = 22
"""


def test_section_loaders_prefer_profile_scoped_sections(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(PROFILE_SECTIONS_CONFIG, encoding="utf-8")

    top_embedding = load_embedding_config(path)
    assert top_embedding.url == "https://top/v1"
    assert top_embedding.api_key == "top-key"
    assert top_embedding.model == "top-model"
    assert top_embedding.batch_size == 20

    lab_embedding = load_embedding_config(path, profile="lab")
    assert lab_embedding.url == "https://lab/v1"
    assert lab_embedding.api_key == "lab-key"

    assert load_rerank_config(path).batch_size == 4
    assert load_rerank_config(path, profile="lab").batch_size == 9

    assert load_vector_store_config(path).path == "/top/vectors"
    assert load_vector_store_config(path, profile="lab").path == "/lab/vectors"

    top_semantic = load_semantic_search_config(path)
    assert top_semantic.candidate_k == 11
    assert top_semantic.top_k == 20
    lab_semantic = load_semantic_search_config(path, profile="lab")
    assert lab_semantic.candidate_k == 22
    assert lab_semantic.top_k == 20  # missing profile key falls back to top-level


def test_section_loaders_unknown_profile_raises_configuration_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(PROFILE_SECTIONS_CONFIG, encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown config profile 'missing'"):
        load_vector_store_config(path, profile="missing")
