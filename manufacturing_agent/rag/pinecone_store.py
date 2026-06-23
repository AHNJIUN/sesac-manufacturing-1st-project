from __future__ import annotations
import os
from typing import Optional

from openai import OpenAI
from pinecone import Pinecone

_index = None
_oai: OpenAI | None = None


def _get_clients():
    global _index, _oai
    if _index is None:
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        _index = pc.Index(os.environ.get("PINECONE_INDEX_NAME", "sesacline-agent-docs"))
        _oai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _index, _oai


def vector_search(query: str, k: int = 3, type_filter: Optional[str] = None) -> list[dict]:
    """chroma.py의 vector_search()와 동일한 반환 포맷."""
    index, oai = _get_clients()
    model = os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")

    emb = oai.embeddings.create(input=[query], model=model).data[0].embedding

    pinecone_filter = {"type": {"$eq": type_filter}} if type_filter else None

    res = index.query(
        vector=emb,
        top_k=k,
        include_metadata=True,
        filter=pinecone_filter,
    )

    return [
        {
            "id": match.id,
            "text": (match.metadata or {}).get("text", ""),
            "type": (match.metadata or {}).get("type"),
            "source": (match.metadata or {}).get("source"),
            "chunk_index": (match.metadata or {}).get("chunk_index"),
            "score": float(match.score),
        }
        for match in res.matches
    ]
