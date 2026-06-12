# db/chroma_search.py — 공유 Chroma 검색 유틸리티
# taxlaw_prec (32,628 법원 판례) / taxtr_cases (2,463 조세심판) / law_articles (6,687 조문)
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
    global _ef
    if _ef is None:
        from chromadb.utils import embedding_functions
        _ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model_name="text-embedding-3-small",
        )
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


def search_law_articles(query: str, n: int = 6) -> list:
    """세법 조문(6,687건) 벡터 검색."""
    try:
        col = _get_client().get_collection("law_articles", embedding_function=_get_ef())
        results = col.query(
            query_texts=[query],
            n_results=n,
            include=["metadatas", "distances"],
        )
        docs = []
        for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
            docs.append({**meta, "similarity": round(1 - dist, 4)})
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
