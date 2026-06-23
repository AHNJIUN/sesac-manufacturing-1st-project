#!/usr/bin/env python
"""Upsert document chunks into Pinecone.

Usage:
    python scripts/build_pinecone.py            # 증분 upsert (이미 있는 ID는 건너뜀)
    python scripts/build_pinecone.py --reset    # 인덱스 전체 삭제 후 재빌드
    python scripts/build_pinecone.py --dry-run  # 계획만 출력, 실제 업로드 없음
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.build_chroma import load_document_chunks, load_dotenv

from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

EMBED_BATCH_SIZE = 64
UPSERT_BATCH_SIZE = 100
DEFAULT_INDEX_NAME = "sesacline-agent-docs"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
DIMENSION = 1536


def get_embeddings(texts: list[str], client: OpenAI, model: str) -> list[list[float]]:
    result = client.embeddings.create(input=texts, model=model)
    return [r.embedding for r in result.data]


def main() -> int:
    parser = argparse.ArgumentParser(description="Upsert document chunks into Pinecone.")
    parser.add_argument("--reset", action="store_true", help="인덱스 전체 삭제 후 재빌드")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력, 실제 업로드 없음")
    args = parser.parse_args()

    load_dotenv()

    api_key = os.environ.get("PINECONE_API_KEY")
    if not api_key:
        print("ERROR: PINECONE_API_KEY가 .env에 없습니다.")
        return 1

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY가 .env에 없습니다.")
        return 1

    index_name = os.environ.get("PINECONE_INDEX_NAME", DEFAULT_INDEX_NAME)
    embed_model = os.environ.get("OPENAI_EMBED_MODEL", DEFAULT_EMBED_MODEL)

    pc = Pinecone(api_key=api_key)
    oai = OpenAI(api_key=openai_key)

    print(f"Pinecone 인덱스: {index_name}")
    print(f"임베딩 모델: {embed_model}")

    # 인덱스 존재 확인 / 생성
    existing = [idx.name for idx in pc.list_indexes()]
    if index_name not in existing:
        print(f"인덱스 '{index_name}' 없음 → 생성 중...")
        pc.create_index(
            name=index_name,
            dimension=DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        print("인덱스 초기화 대기 중...")
        time.sleep(5)
    else:
        print(f"인덱스 '{index_name}' 기존 사용")

    index = pc.Index(index_name)

    if args.reset:
        print("인덱스 전체 삭제 중...")
        index.delete(delete_all=True)
        time.sleep(2)

    # 문서 청크 로드
    chunks = load_document_chunks()
    print(f"문서 청크 수: {len(chunks)}")
    if not chunks:
        print("document/ 폴더에 문서가 없습니다.")
        return 1

    # 이미 업로드된 ID 조회 (증분 빌드)
    chunk_ids = [c["id"] for c in chunks]
    existing_ids: set[str] = set()
    for i in range(0, len(chunk_ids), 1000):
        resp = index.fetch(ids=chunk_ids[i:i + 1000])
        existing_ids |= set(resp.vectors.keys())

    new_chunks = [c for c in chunks if c["id"] not in existing_ids]
    print(f"기존 업로드: {len(existing_ids)}개 / 신규: {len(new_chunks)}개")

    if args.dry_run:
        print("Dry run — 실제 업로드 없음")
        return 0

    if not new_chunks:
        print("모두 최신 상태. 업로드 불필요.")
        return 0

    # 임베딩 + upsert
    total = 0
    for i in range(0, len(new_chunks), EMBED_BATCH_SIZE):
        batch = new_chunks[i:i + EMBED_BATCH_SIZE]
        embeddings = get_embeddings([c["text"] for c in batch], oai, embed_model)

        vectors = []
        for chunk, emb in zip(batch, embeddings):
            vectors.append({
                "id": chunk["id"],
                "values": emb,
                "metadata": {
                    **chunk["metadata"],
                    "text": chunk["text"][:1000],
                },
            })

        for j in range(0, len(vectors), UPSERT_BATCH_SIZE):
            index.upsert(vectors=vectors[j:j + UPSERT_BATCH_SIZE])

        total += len(batch)
        print(f"  업로드 {total}/{len(new_chunks)}")

    stats = index.describe_index_stats()
    print(f"\n완료 — 인덱스 총 벡터: {stats['total_vector_count']}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
