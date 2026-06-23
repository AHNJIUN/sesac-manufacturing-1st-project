from __future__ import annotations
from manufacturing_agent._common import *  # noqa: F401,F403
from manufacturing_agent.config import *  # noqa: F401,F403

# ---------- 2) Evidence RAG 런타임: 임베딩된 ChromaDB 검색만 수행 ----------
import hashlib

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chromadb.utils import embedding_functions

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
LOCAL_EMBED_DIM = 384


class LocalHashEmbeddingFunction(EmbeddingFunction[Documents]):
    """외부 모델 다운로드 없이 동작하는 로컬 임베딩 함수."""

    def __call__(self, input: Documents) -> Embeddings:
        vectors = []
        for text in input:
            vec = [0.0] * LOCAL_EMBED_DIM
            tokens = re.findall(r"[A-Za-z가-힣0-9_]+", text.lower())
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                idx = int.from_bytes(digest[:4], "little") % LOCAL_EMBED_DIM
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vec[idx] += sign
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


def build_embedding_function():
    """01_embed_documents_chroma.ipynb의 임베딩 함수와 동일해야 한다.
    Chat LLM 사용 여부와 embedding collection 선택은 분리한다.
    USE_OPENAI_EMBEDDINGS=true일 때만 OpenAI embedding collection을 사용한다.
    """
    if USE_OPENAI_EMBEDDINGS:
        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.environ["OPENAI_API_KEY"], model_name=EMBED_MODEL), "manufacturing_document_chunks_openai", f"OpenAI({EMBED_MODEL})"
    return LocalHashEmbeddingFunction(), "manufacturing_document_chunks_local_hash", f"LocalHash({LOCAL_EMBED_DIM})"


_embed_fn, _collection_name, _embed_label = build_embedding_function()
_chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
try:
    _chroma_collection = _chroma_client.get_collection(
        _collection_name, embedding_function=_embed_fn)
except Exception as e:
    raise RuntimeError(
        f"ChromaDB 컬렉션 '{_collection_name}'을 찾을 수 없습니다. "
        "먼저 01_embed_documents_chroma.ipynb를 실행해 document/를 임베딩하세요."
    ) from e



def vector_search(query: str, k: int = 3, type_filter: Optional[str] = None) -> list[dict]:
    """이미 임베딩된 ChromaDB 컬렉션에서 관련 문서 top-k 검색."""
    where = {"type": type_filter} if type_filter else None
    res = _chroma_collection.query(query_texts=[query], n_results=k, where=where)
    docs = res.get("documents", [[]])[0]
    ids = res.get("ids", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    distances = res.get("distances", [[]])[0] if res.get("distances") else [0.0] * len(docs) # 거리 계산 방법: 1 - cosine similarity
    out = []
    for i, doc in enumerate(docs):
        meta = metas[i] or {}
        out.append({
            "id": ids[i],
            "text": doc,
            "type": meta.get("type"),
            "source": meta.get("source"),
            "chunk_index": meta.get("chunk_index"),
            "score": 1.0 - float(distances[i]),
        })
    return out


# ---------- 벡터 백엔드 분기 ----------
# .env의 VECTOR_BACKEND 값에 따라 pinecone_store의 vector_search로 교체한다.
# rag_service.py는 이 모듈에서 vector_search를 import하므로 변경 불필요.
_VECTOR_BACKEND = os.environ.get("VECTOR_BACKEND", "chroma").lower()
if _VECTOR_BACKEND == "pinecone":
    from manufacturing_agent.rag.pinecone_store import vector_search  # noqa: F811
