"""Optional, disabled-by-default embedding support."""

from app.embeddings.base import (
    EmbeddingBatch,
    EmbeddingClient,
    EmbeddingContractNotConfiguredError,
    EmbeddingUnavailableError,
)
from app.embeddings.hchat import HChatEmbeddingClient, build_embedding_client
from app.embeddings.index import (
    EmbeddingIndex,
    IndexMetadata,
    IndexMismatchError,
    load_index,
    save_index,
)
from app.embeddings.mock import DeterministicMockEmbeddingClient

__all__ = [
    "DeterministicMockEmbeddingClient",
    "EmbeddingBatch",
    "EmbeddingClient",
    "EmbeddingContractNotConfiguredError",
    "EmbeddingIndex",
    "EmbeddingUnavailableError",
    "HChatEmbeddingClient",
    "IndexMetadata",
    "IndexMismatchError",
    "build_embedding_client",
    "load_index",
    "save_index",
]
