"""Configuration: paths, typed settings models, loaders, and Zotero resolution."""

from __future__ import annotations

from . import loaders, models, paths, zotero
from .loaders import (
    get_default_profile,
    list_profiles,
    load_ai_note_config,
    load_config,
    load_embedding_config,
    load_pdf_config,
    load_rerank_config,
    load_semantic_search_config,
    load_vector_store_config,
    save_config,
)
from .models import (
    AiNoteConfig,
    AppConfig,
    EmbeddingConfig,
    PdfConfig,
    RerankConfig,
    SemanticSearchConfig,
    VectorStoreConfig,
)
from .paths import (
    CONFIG_FILENAME,
    SETTINGS_DIRNAME,
    STATE_DIRNAME,
    config_file_path,
    project_root,
    settings_dir,
    state_dir,
)
from .zotero import (
    detect_zotero_data_dir,
    get_data_dir,
    get_prefs_js_path,
    resolve_crossref_mailto,
    resolve_library_id,
    resolve_semantic_scholar_api_key,
    resolve_write_credentials,
)

__all__ = [
    "CONFIG_FILENAME",
    "SETTINGS_DIRNAME",
    "STATE_DIRNAME",
    "AiNoteConfig",
    "AppConfig",
    "EmbeddingConfig",
    "PdfConfig",
    "RerankConfig",
    "SemanticSearchConfig",
    "VectorStoreConfig",
    "config_file_path",
    "detect_zotero_data_dir",
    "get_data_dir",
    "get_default_profile",
    "get_prefs_js_path",
    "list_profiles",
    "load_ai_note_config",
    "load_config",
    "load_embedding_config",
    "load_pdf_config",
    "load_rerank_config",
    "load_semantic_search_config",
    "load_vector_store_config",
    "project_root",
    "resolve_crossref_mailto",
    "resolve_library_id",
    "resolve_semantic_scholar_api_key",
    "resolve_write_credentials",
    "save_config",
    "settings_dir",
    "state_dir",
    "loaders",
    "models",
    "paths",
    "zotero",
]
