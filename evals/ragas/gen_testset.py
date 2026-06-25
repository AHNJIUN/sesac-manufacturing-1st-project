"""RAGAS TestsetGenerator 로 1-1(단순 정비 지식 검색) 평가셋 자동 생성.

- 소스: Mill Spindle / Mill Chatter PDF(전체) + Mechanical Service Manual(1-1 토픽 청크만)
- TestsetGenerator(gpt-4o-mini)로 영어 Q&A 생성 → 카테고리(cause/inspection/maintenance) 분류 → 한국어 번역
- 출력: dataset_1_1.jsonl (track=1-1, profile=troubleshooting_rag, expect_evidence=true, GT 포함)

1-2(예측 기반)는 변수/예측이 필요해 TestsetGenerator로 못 만든다 → gen_dataset_1_2.py 참고.

실행:
    PYTHONUTF8=1 PYTHONPATH=. python evals/ragas/gen_testset.py --size 20
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / "dataset_1_1.jsonl"

FULL_PDFS = [
    "haascnc.com-Mill Spindle - Troubleshooting Guide.pdf",
    "haascnc.com-Mill Chatter - Troubleshooting - TG0100.pdf",
]
# Mechanical Service Manual(663청크)은 통째로 넣으면 무거워 1-1 점검/정비 토픽 청크만 샘플링
MANUAL_PDF = "english---mechanical-service-manual---2007.pdf"
MANUAL_KEYWORDS = ["bearing", "belt", "motor", "coolant", "cooling", "vibration",
                   "thermal", "overheat", "lubricat", "spindle", "accuracy", "runout"]
MANUAL_MAX_CHUNKS = 60


def _load_env() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v[:1] not in "\"'" and " #" in v:
            v = v.split(" #", 1)[0].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


def _shim_vertexai() -> None:
    import langchain_community.chat_models  # noqa: F401
    if "langchain_community.chat_models.vertexai" not in sys.modules:
        m = types.ModuleType("langchain_community.chat_models.vertexai")
        m.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules["langchain_community.chat_models.vertexai"] = m
    import langchain_community.llms as _llms
    if not hasattr(_llms, "VertexAI"):
        _llms.VertexAI = type("VertexAI", (), {})


def _chunk(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    out, start = [], 0
    while start < len(text):
        c = text[start:start + size].strip()
        if len(c) > 120:
            out.append(c)
        if start + size >= len(text):
            break
        start += size - overlap
    return out


def load_documents():
    from langchain_core.documents import Document
    from pypdf import PdfReader

    haas = ROOT / "document" / "haas"
    docs = []
    # 1) Spindle/Chatter 전체
    for name in FULL_PDFS:
        p = haas / name
        if not p.exists():
            print(f"⚠ PDF 없음: {p}"); continue
        text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
        for c in _chunk(text):
            docs.append(Document(page_content=c, metadata={"source": f"haas/{name}"}))
    # 2) Mechanical Service Manual: 1-1 토픽 청크만 샘플
    mp = haas / MANUAL_PDF
    if mp.exists():
        text = "\n".join(pg.extract_text() or "" for pg in PdfReader(str(mp)).pages)
        picked = 0
        for c in _chunk(text):
            low = c.lower()
            if any(k in low for k in MANUAL_KEYWORDS):
                docs.append(Document(page_content=c, metadata={"source": f"haas/{MANUAL_PDF}"}))
                picked += 1
                if picked >= MANUAL_MAX_CHUNKS:
                    break
    print(f"문서 로드: {len(docs)} chunks (Spindle/Chatter 전체 + Manual 토픽 {MANUAL_MAX_CHUNKS}개 이내)")
    return docs


def _llm(model: str):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model, temperature=0)


def classify_categories(questions_en: list[str], model: str) -> list[str]:
    """각 질문을 cause/inspection/maintenance/concept 로 분류."""
    llm = _llm(model)
    sys_msg = (
        "너는 제조 정비 질문 분류기다. 입력 질문을 다음 중 하나로만 분류해 한 단어로 답한다: "
        "cause(고장 원인), inspection(점검 방법), maintenance(정비/조치 방법), concept(개념/기타). "
        "분류 라벨만 출력."
    )
    out = []
    for i, q in enumerate(questions_en, 1):
        res = llm.invoke([("system", sys_msg), ("human", q)])
        lab = (res.content if isinstance(res.content, str) else str(res.content)).strip().lower()
        lab = next((c for c in ["cause", "inspection", "maintenance", "concept"] if c in lab), "concept")
        out.append(lab)
        print(f"  분류 {i}/{len(questions_en)}: {lab}", flush=True)
    return out


def translate_ko(texts: list[str], model: str) -> list[str]:
    llm = _llm(model)
    sys_msg = (
        "너는 제조 설비 정비 도메인 번역가다. 입력 영어를 자연스러운 한국어로 번역한다. "
        "기술 용어(스핀들/채터/툴홀더 등)는 현장 표현으로 옮기고 의미를 바꾸지 마라. 번역문만 출력."
    )
    out = []
    for i, t in enumerate(texts, 1):
        t = (t or "").strip()
        if not t:
            out.append(""); continue
        res = llm.invoke([("system", sys_msg), ("human", t)])
        out.append(res.content if isinstance(res.content, str) else str(res.content))
        print(f"  번역 {i}/{len(texts)}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS TestsetGenerator로 1-1 평가셋 생성")
    ap.add_argument("--size", type=int, default=20)
    ap.add_argument("--gen-model", default="gpt-4o-mini")
    ap.add_argument("--embed-model", default=os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-translate", action="store_true")
    args = ap.parse_args()

    _load_env(); _shim_vertexai()
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.testset import TestsetGenerator
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper

    docs = load_documents()
    if not docs:
        print("문서 없음"); return 1

    gen = TestsetGenerator(
        llm=LangchainLLMWrapper(ChatOpenAI(model=args.gen_model, temperature=0)),
        embedding_model=LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=args.embed_model)),
    )
    print(f"[gen={args.gen_model}] testset_size={args.size} 생성 중…")
    testset = gen.generate_with_langchain_docs(docs, testset_size=args.size)
    df = testset.to_pandas()
    print(f"생성 완료: {len(df)}개")

    q_en = [str(x) for x in df["user_input"].tolist()]
    r_en = [str(x) for x in df["reference"].tolist()]
    ctx = [list(x) if x is not None else [] for x in df["reference_contexts"].tolist()]

    print("카테고리 분류…"); cats = classify_categories(q_en, args.gen_model)
    if args.no_translate:
        q_ko, r_ko = q_en, r_en
    else:
        print("질문 번역…"); q_ko = translate_ko(q_en, args.gen_model)
        print("참조답변 번역…"); r_ko = translate_ko(r_en, args.gen_model)

    rows = []
    for i in range(len(df)):
        rows.append({
            "id": f"k11_{i+1:02d}",
            "track": "1-1",
            "category": cats[i],
            "question": q_ko[i],
            "ground_truth": r_ko[i],
            "profile": "troubleshooting_rag",
            "expect_evidence": True,
            "reference_contexts": ctx[i],
            "question_en": q_en[i],
            "ground_truth_en": r_en[i],
        })
    Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    from collections import Counter
    print(f"\n저장: {args.out} ({len(rows)}개) | category={dict(Counter(r['category'] for r in rows))}")
    for r in rows[:5]:
        print(f"  - [{r['category']}] {r['question']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
