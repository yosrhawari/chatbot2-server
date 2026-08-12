import logging

logger = logging.getLogger(__name__)

import re
import threading
import torch
import numpy as np
from typing import Sequence, List

from sentence_transformers import CrossEncoder
from langchain_ollama import ChatOllama
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import BaseDocumentCompressor, Document
from langchain_core.callbacks import Callbacks

from config import (
    MODEL_NAME,
    LLM_BASE_URL,
    EMBEDDING_MODEL_NAME,
    RERANKER_MODEL_NAME,
    NUM_CTX,
    NUM_PREDICT,
    RERANKER_BATCH_SIZE,
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
logger.info(f"[INFO] Running on {DEVICE}")


def _ollama_base_url(url: str) -> str:
    """Accept either …:11434/v1 (OpenAI-compat) or …:11434 (native) and
    normalise to the native form that ChatOllama expects."""
    u = (url or "").rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    return u or "http://localhost:11434"


# ChatOllama (native driver) so num_ctx and num_predict actually propagate.
# The OpenAI-compat endpoint silently drops these on many Ollama versions,
# which caused every prompt to be truncated to Ollama's 2048-token default.
llm_client = ChatOllama(
    base_url=_ollama_base_url(LLM_BASE_URL),
    model=MODEL_NAME,
    temperature=0,
    num_ctx=NUM_CTX,
    num_predict=NUM_PREDICT,
)

logger.info(
    f"[INFO] LLM ready: model={MODEL_NAME} num_ctx={NUM_CTX} num_predict={NUM_PREDICT}"
)


class E5Embeddings(HuggingFaceEmbeddings):
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        prefixed = [f"passage: {t}" if not t.startswith("passage: ") else t for t in texts]
        return super().embed_documents(prefixed)

    def embed_query(self, text: str) -> List[float]:
        prefixed = f"query: {text}" if not text.startswith("query: ") else text
        return super().embed_query(prefixed)


logger.info(f"[INFO] Loading embeddings: {EMBEDDING_MODEL_NAME}")
embedding_function = E5Embeddings(
    model_name=EMBEDDING_MODEL_NAME,
    model_kwargs={"device": DEVICE},
    encode_kwargs={"normalize_embeddings": True},
)
logger.info("[INFO] Embeddings ready.")


class CrossEncoderReranker(BaseDocumentCompressor):
    model_name: str
    device: str
    top_n: int = 5
    batch_size: int = 8
    _model: object = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model = CrossEncoder(self.model_name, device=self.device)

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks = None,
    ) -> Sequence[Document]:
        if not documents:
            return []
        pairs = [(query, doc.page_content) for doc in documents]
        scores = self._model.predict(
            pairs,
            show_progress_bar=False,
            batch_size=self.batch_size,
        )
        ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in ranked[: self.top_n]]


logger.info(f"[INFO] Loading reranker: {RERANKER_MODEL_NAME}")
reranker_compressor = CrossEncoderReranker(
    model_name=RERANKER_MODEL_NAME,
    device=DEVICE,
    batch_size=RERANKER_BATCH_SIZE,
)
logger.info(f"[INFO] Reranker ready (batch_size={RERANKER_BATCH_SIZE}).")


# --- SEMANTIC CACHE ---------------------------------------------------------
# Threshold for the (secondary) embedding-similarity match. Kept high because a
# cache serves a stored ANSWER: two queries that differ only in a material
# detail (e.g. "garantie produit A" vs "produit B") can still embed very
# closely, so a loose threshold would serve wrong answers. The PRIMARY hit path
# is now an exact match on the normalised (condensed) key, which is risk-free;
# the embedding layer only rescues trivial variants (whitespace/casing/accents).
try:
    from config import SEMANTIC_CACHE_THRESHOLD as _DEFAULT_THRESHOLD
except ImportError:
    _DEFAULT_THRESHOLD = 0.97


class SemanticCache:
    def __init__(self, embedding_func, threshold: float = _DEFAULT_THRESHOLD, max_size: int = 1000):
        self.embedding_func = embedding_func
        self.threshold = threshold
        self.max_size = max_size
        self._lock = threading.Lock()
        self._responses = []
        self._matrix = None
        # Normalised key text for each stored response + a key -> position index
        # for O(1) exact-match lookups. The router now keys the cache by the
        # history-independent CONDENSED query, so an exact key match is safe to
        # serve across sessions.
        self._keys = []
        self._key_index = {}

    @staticmethod
    def _norm_key(query: str) -> str:
        return re.sub(r"\s+", " ", (query or "").strip().lower())

    def get_cached_response(self, query: str):
        norm = self._norm_key(query)
        with self._lock:
            if self._matrix is None or len(self._responses) == 0:
                return None
            # 1. Exact (normalised) key match -- risk-free, no embedding needed.
            idx = self._key_index.get(norm)
            if idx is not None:
                logger.info("[CACHE HIT] Exact condensed-key match")
                return self._responses[idx]
            # Snapshot matrix + responses together so their indices stay aligned
            # even if another thread mutates the cache after we release the lock.
            matrix = self._matrix
            responses = list(self._responses)

        # E5 embeddings are already L2-normalised (normalize_embeddings=True), so
        # cosine similarity == dot product. Dropping the redundant per-call norm
        # (np.linalg.norm over the whole matrix + query) saves work on every miss.
        query_emb = np.asarray(self.embedding_func.embed_query(query), dtype=np.float32)
        similarities = matrix @ query_emb

        best_idx = int(np.argmax(similarities))
        if similarities[best_idx] >= self.threshold:
            logger.info(f"[CACHE HIT] Similarity: {similarities[best_idx]:.4f}")
            return responses[best_idx]
        return None

    def add_to_cache(self, query: str, response: dict):
        norm = self._norm_key(query)
        query_emb = np.asarray(self.embedding_func.embed_query(query), dtype=np.float32)
        with self._lock:
            self._responses.append(response)
            self._keys.append(norm)
            if self._matrix is None:
                self._matrix = query_emb.reshape(1, -1)
            else:
                self._matrix = np.vstack([self._matrix, query_emb])

            if len(self._responses) > self.max_size:
                overflow = len(self._responses) - self.max_size
                self._responses = self._responses[overflow:]
                self._keys = self._keys[overflow:]
                self._matrix = self._matrix[overflow:]

            # Positions shift after eviction -> rebuild the index. Later entries
            # win on duplicate keys, so we always serve the freshest response.
            self._key_index = {k: i for i, k in enumerate(self._keys)}

    def clear(self):
        with self._lock:
            self._responses = []
            self._matrix = None
            self._keys = []
            self._key_index = {}


semantic_cache = SemanticCache(embedding_func=embedding_function)
