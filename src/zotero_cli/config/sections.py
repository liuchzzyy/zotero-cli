"""从 config.toml 加载的类型化配置分节。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .runtime import built_in_defaults

_DEFAULTS = built_in_defaults()


@dataclass
class AppConfig:
    data_dir: str = ""
    library_id: str = ""
    api_key: str = ""
    semantic_scholar_api_key: str = ""
    crossref_mailto: str = ""
    default_format: str = _DEFAULTS["output"]["default_format"]
    default_limit: int = _DEFAULTS["output"]["limit"]
    default_export_style: str = _DEFAULTS["export"]["default_style"]
    prefs_js_path: str = ""


@dataclass
class EmbeddingConfig:
    active: str = ""
    provider: str = "gitee"
    url: str = "https://ai.gitee.com/v1"
    api_key: str = ""
    model: str = _DEFAULTS["embedding"]["model"]
    batch_size: int = _DEFAULTS["embedding"]["batch_size"]
    timeout_seconds: float = _DEFAULTS["embedding"]["timeout_seconds"]

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key and self.model)


@dataclass
class RerankConfig:
    active: str = ""
    provider: str = "gitee"
    url: str = "https://ai.gitee.com/v1/rerank"
    api_key: str = ""
    model: str = _DEFAULTS["rerank"]["model"]
    batch_size: int = _DEFAULTS["rerank"]["batch_size"]
    timeout_seconds: float = _DEFAULTS["rerank"]["timeout_seconds"]

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key and self.model)


@dataclass
class VectorStoreConfig:
    provider: str = _DEFAULTS["vector_store"]["provider"]
    path: str = _DEFAULTS["vector_store"]["path"]


@dataclass
class SemanticSearchConfig:
    candidate_k: int = _DEFAULTS["semantic_search"]["candidate_k"]
    top_k: int = _DEFAULTS["semantic_search"]["top_k"]
    rrf_k: int = _DEFAULTS["semantic_search"]["rrf_k"]
    semantic_weight: float = _DEFAULTS["semantic_search"]["semantic_weight"]
    bm25_weight: float = _DEFAULTS["semantic_search"]["bm25_weight"]


@dataclass
class PdfConfig:
    mineru_token: str = ""
    mineru_model_version: str = _DEFAULTS["pdf"]["mineru_model_version"]
    mineru: dict = field(default_factory=lambda: dict(_DEFAULTS["pdf"]["mineru"]))
    limits: dict = field(default_factory=lambda: dict(_DEFAULTS["pdf"]["limits"]))
    cache: dict = field(default_factory=lambda: dict(_DEFAULTS["pdf"]["cache"]))


@dataclass
class AiNoteConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = _DEFAULTS["ai_notes"]["model"]
    fallback_models: list[str] = field(default_factory=lambda: list(_DEFAULTS["ai_notes"]["fallback_models"]))
    reasoning_effort: str = _DEFAULTS["ai_notes"]["reasoning_effort"]
    temperature: float = _DEFAULTS["ai_notes"]["temperature"]
    max_tokens: int = _DEFAULTS["ai_notes"]["max_tokens"]
    max_extracted_chars: int = _DEFAULTS["ai_notes"]["max_extracted_chars"]
    timeout_seconds: float = _DEFAULTS["ai_notes"]["timeout_seconds"]
    max_retries: int = _DEFAULTS["ai_notes"]["max_retries"]
    budget: dict = field(default_factory=lambda: dict(_DEFAULTS["ai_notes"]["budget"]))
    images: dict = field(default_factory=lambda: dict(_DEFAULTS["ai_notes"]["images"]))
    routes: dict = field(default_factory=lambda: dict(_DEFAULTS["ai_notes"]["routes"]))
