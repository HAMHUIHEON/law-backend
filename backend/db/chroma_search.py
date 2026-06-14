# db/chroma_search.py — 공유 Chroma 검색 유틸리티
# taxlaw_prec (32,628 법원 판례) / taxtr_cases (2,463 조세심판) / law_articles (6,687 조문)
# pdf_court_cases (PDF 판례 임베딩) — scripts/embed_pdf_cases.py 로 생성
# DB는 text-embedding-3-small (1536차원)으로 빌드됨 → 쿼리도 동일 모델 사용 필수

import os
from pathlib import Path

_CHROMA_DIR = Path(
    os.environ.get("CHROMA_DIR")
    or str(Path(__file__).parent.parent.parent / "vector_db" / "chroma")
)
_client = None
_ef = None


def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
    return _client


def _get_ef():
    """openai v1.x 호환 커스텀 임베딩 함수. chromadb 내장 OpenAIEmbeddingFunction은 v0.x API 사용으로 실패."""
    global _ef
    if _ef is None:
        from chromadb import EmbeddingFunction
        from openai import OpenAI

        _api_key = os.environ.get("OPENAI_API_KEY", "")
        _model = "text-embedding-3-small"

        class _EF(EmbeddingFunction):
            def __call__(self, input):  # noqa: A002
                client = OpenAI(api_key=_api_key)
                resp = client.embeddings.create(input=input, model=_model)
                return [item.embedding for item in resp.data]

        _ef = _EF()
    return _ef


def search_taxlaw_prec(
    query: str,
    n: int = 8,
    filter_winning: bool = False,
) -> list:
    """NTS 법원 판례(32,628건) 벡터 검색.

    filter_winning=True 시 취소/인용/승소 결론 판례만 반환.
    """
    try:
        col = _get_client().get_collection("taxlaw_prec", embedding_function=_get_ef())
        results = col.query(
            query_texts=[query],
            n_results=min(n * 3 if filter_winning else n, 50),
            include=["metadatas", "distances"],
        )
        docs = []
        for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
            if filter_winning:
                decision = (meta.get("decision") or "").lower()
                if not any(k in decision for k in ["취소", "인용", "승소", "원고 승", "납세자 승"]):
                    continue
            docs.append({**meta, "similarity": round(1 - dist, 4)})
            if len(docs) >= n:
                break
        return docs
    except Exception:
        return []


def search_taxtr_cases(
    query: str,
    n: int = 8,
    filter_favorable: bool = False,
) -> list:
    """조세심판 재결례(2,463건) 벡터 검색.

    filter_favorable=True 시 인용/취소/감액 재결만 반환.
    """
    try:
        col = _get_client().get_collection("taxtr_cases", embedding_function=_get_ef())
        results = col.query(
            query_texts=[query],
            n_results=min(n * 3 if filter_favorable else n, 50),
            include=["metadatas", "distances"],
        )
        docs = []
        for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
            if filter_favorable:
                dtype = (meta.get("decision_type") or "").lower()
                if not any(k in dtype for k in ["인용", "취소", "감액"]):
                    continue
            docs.append({**meta, "similarity": round(1 - dist, 4)})
            if len(docs) >= n:
                break
        return docs
    except Exception:
        return []


def search_law_articles(query: str, n: int = 6, target_date: str = "") -> list:
    """세법 조문(6,687건) 벡터 검색. document 텍스트 포함.

    target_date: "YYYYMMDD" 형식. 지정 시 해당 날짜에 유효한 조문만 반환.
      effective_date <= target_date AND (abolition_date == "" OR abolition_date > target_date)
    """
    try:
        col = _get_client().get_collection("law_articles", embedding_function=_get_ef())
        # 날짜 필터: target_date 있을 때만 적용
        where = None
        if target_date:
            td = target_date.replace("-", "")  # "2024-01-01" → "20240101"
            where = {"$and": [
                {"effective_date": {"$lte": td}},
                {"$or": [
                    {"abolition_date": {"$eq": ""}},
                    {"abolition_date": {"$gt": td}},
                ]},
            ]}
        kwargs: dict = {
            "query_texts": [query],
            "n_results": n,
            "include": ["metadatas", "distances", "documents"],
        }
        if where:
            kwargs["where"] = where
        results = col.query(**kwargs)
        docs = []
        documents = results.get("documents", [[]])[0] or []
        for i, (meta, dist) in enumerate(zip(results["metadatas"][0], results["distances"][0])):
            doc_text = documents[i] if i < len(documents) else ""
            docs.append({**meta, "document": doc_text or "", "similarity": round(1 - dist, 4)})
        return docs
    except Exception:
        return []


def search_inquiry_cases(query: str, n: int = 6) -> list:
    """국세청 질의회신(inquiry_cases 컬렉션) 벡터 검색."""
    try:
        col = _get_client().get_collection("inquiry_cases", embedding_function=_get_ef())
        results = col.query(
            query_texts=[query],
            n_results=min(n, 50),
            include=["metadatas", "distances", "documents"],
        )
        docs = []
        documents = results.get("documents", [[]])[0] or []
        for i, (meta, dist) in enumerate(zip(results["metadatas"][0], results["distances"][0])):
            doc_text = documents[i] if i < len(documents) else ""
            docs.append({**meta, "document": (doc_text or "")[:600], "similarity": round(1 - dist, 4)})
        return docs
    except Exception:
        return []


def search_pdf_court_cases(query: str, n: int = 6) -> list:
    """PDF 판례 임베딩 벡터 검색 (pdf_court_cases 컬렉션)."""
    try:
        col = _get_client().get_collection("pdf_court_cases", embedding_function=_get_ef())
        results = col.query(
            query_texts=[query],
            n_results=min(n, 50),
            include=["metadatas", "distances", "documents"],
        )
        docs = []
        documents = results.get("documents", [[]])[0] or []
        for i, (meta, dist) in enumerate(zip(results["metadatas"][0], results["distances"][0])):
            doc_text = documents[i] if i < len(documents) else ""
            docs.append({**meta, "document": (doc_text or "")[:600], "similarity": round(1 - dist, 4)})
        return docs
    except Exception:
        return []


def get_taxlaw_prec_stats(query: str, n: int = 50) -> dict:
    """연도별 판례 승소율 통계 (TrendAgent용)."""
    try:
        col = _get_client().get_collection("taxlaw_prec", embedding_function=_get_ef())
        results = col.query(
            query_texts=[query],
            n_results=n,
            include=["metadatas"],
        )
        metas = results["metadatas"][0]
        year_stats: dict[str, dict] = {}
        for m in metas:
            yr = str(m.get("attr_yr") or "미상")[:4]
            if yr not in year_stats:
                year_stats[yr] = {"total": 0, "taxpayer_win": 0}
            year_stats[yr]["total"] += 1
            decision = (m.get("decision") or "").lower()
            if any(k in decision for k in ["취소", "인용", "승소", "원고 승"]):
                year_stats[yr]["taxpayer_win"] += 1
        # 승소율 계산
        for yr, st in year_stats.items():
            total = st["total"]
            st["win_rate"] = round(st["taxpayer_win"] / total * 100, 1) if total else 0.0
        return {
            "total_cases": len(metas),
            "year_stats": dict(sorted(year_stats.items())),
            "sample": metas[:5],
        }
    except Exception:
        return {"total_cases": 0, "year_stats": {}, "sample": []}
