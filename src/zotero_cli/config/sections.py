"""从 config.toml 加载的类型化配置分节。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AppConfig:
    data_dir: str = ""
    library_id: str = ""
    api_key: str = ""
    semantic_scholar_api_key: str = ""
    crossref_mailto: str = ""
    default_format: str = "table"
    default_limit: int = 50
    default_export_style: str = "bibtex"
    prefs_js_path: str = ""


@dataclass
class EmbeddingConfig:
    active: str = ""
    provider: str = "gitee"
    url: str = "https://ai.gitee.com/v1"
    api_key: str = ""
    model: str = "bge-m3"
    batch_size: int = 50

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key and self.model)


@dataclass
class RerankConfig:
    active: str = ""
    provider: str = "gitee"
    url: str = "https://ai.gitee.com/v1/rerank"
    api_key: str = ""
    model: str = "bge-reranker-v2-m3"
    batch_size: int = 16

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key and self.model)


@dataclass
class VectorStoreConfig:
    provider: str = "qdrant_local"
    path: str = ".zot/pdf_rag/_qdrant"


@dataclass
class SemanticSearchConfig:
    candidate_k: int = 150
    top_k: int = 20
    rrf_k: int = 60
    semantic_weight: float = 0.8
    bm25_weight: float = 0.2


@dataclass
class PdfConfig:
    mineru_token: str = ""
    mineru_model_version: str = "vlm"


@dataclass
class AiNoteConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "group/daily"
    fallback_models: list[str] = field(default_factory=lambda: ["group/deepseek", "group/advanced"])
    reasoning_effort: str = "high"
    temperature: float = 0.7
    max_tokens: int = 0
    max_extracted_chars: int = 250000
    timeout_seconds: float = 300.0
    max_retries: int = 2
