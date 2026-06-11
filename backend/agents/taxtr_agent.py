# agents/taxtr_agent.py
# 조세심판원 재결례 에이전트 (taxtr_cases Chroma 컬렉션)
import os
from pathlib import Path

from langchain.tools import tool
from langchain_core.messages import HumanMessage

from utils.llm import get_llm, DEFAULT_MODEL

_CHROMA_DIR = Path(__file__).parent.parent.parent / "vector_db" / "chroma"
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
_COLLECTION = "taxtr_cases"


def _get_col():
    import chromadb
    from chromadb.utils import embedding_functions
    client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
    ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=_OPENAI_KEY or os.getenv("OPENAI_API_KEY", ""),
        model_name="text-embedding-3-small",
    )
    return client.get_collection(_COLLECTION, embedding_function=ef)


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
def get_collection_stats() -> str:
    """재결례 DB 현황을 반환합니다."""
    try:
        col = _get_col()
        total = col.count()
        sample = col.get(limit=200, include=["metadatas"])
        tax_types: dict = {}
        decisions: dict = {}
        for m in sample["metadatas"] or []:
            tt = m.get("tax_type", "기타")
            d = m.get("decision", "기타")
            tax_types[tt] = tax_types.get(tt, 0) + 1
            decisions[d] = decisions.get(d, 0) + 1
        top_tt = sorted(tax_types.items(), key=lambda x: -x[1])[:5]
        top_d = sorted(decisions.items(), key=lambda x: -x[1])[:5]
        lines = [
            f"총 {total:,}건의 조세심판 재결례 보유",
            "",
            "주요 세목 (샘플 기준):",
            *[f"  {k}: {v}건" for k, v in top_tt],
            "",
            "결정 유형 (샘플 기준):",
            *[f"  {k}: {v}건" for k, v in top_d],
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"통계 조회 실패: {e}"


@tool
def search_cases(query: str, tax_type: str = "", decision: str = "", n_results: int = 8) -> str:
    """조세심판 재결례를 벡터 검색합니다.

    Args:
        query: 검색 쿼리
        tax_type: 세목 필터 (선택)
        decision: 결정 유형 필터 (선택)
        n_results: 반환 건수 (기본 8)
    """
    try:
        col = _get_col()
        where = None
        filters = []
        if tax_type:
            filters.append({"tax_type": {"$eq": tax_type}})
        if decision:
            filters.append({"decision": {"$eq": decision}})
        if len(filters) == 1:
            where = filters[0]
        elif len(filters) > 1:
            where = {"$and": filters}

        kwargs = {"query_texts": [query], "n_results": min(n_results, col.count())}
        if where:
            kwargs["where"] = where
        res = col.query(**kwargs, include=["metadatas", "documents", "distances"])

        lines = []
        for i, doc_id in enumerate(res["ids"][0]):
            m = (res["metadatas"][0][i] if res["metadatas"] else {}) or {}
            doc = (res["documents"][0][i] if res["documents"] else "") or ""
            dist = res["distances"][0][i] if res["distances"] else None
            sim = f"{(1 - dist) * 100:.0f}%" if dist is not None else "N/A"
            lines.append(
                f"[{i+1}] {m.get('case_no', '')} | {m.get('tax_type', '')} | "
                f"{m.get('decision', '')} | {m.get('decision_date', '')} | 유사도 {sim}\n"
                f"    제목: {m.get('title', '')}\n"
                f"    청구번호: {m.get('dem_no', doc_id)}"
            )
        return "\n\n".join(lines) if lines else "검색 결과 없음"
    except Exception as e:
        return f"검색 실패: {e}"


@tool
def get_case_detail(dem_no: str) -> str:
    """특정 재결례 전문을 조회합니다.

    Args:
        dem_no: 청구번호 (dem_no)
    """
    try:
        col = _get_col()
        try:
            r = col.get(where={"dem_no": {"$eq": dem_no}}, include=["metadatas", "documents"])
        except Exception:
            r = col.get(ids=[dem_no], include=["metadatas", "documents"])
        if not r["ids"]:
            return f"재결례를 찾을 수 없습니다: {dem_no}"
        m = r["metadatas"][0]
        doc = r["documents"][0]
        return (
            f"청구번호: {m.get('dem_no', '')}\n"
            f"사건번호: {m.get('case_no', '')}\n"
            f"세목: {m.get('tax_type', '')}\n"
            f"결정: {m.get('decision', '')}\n"
            f"결정일: {m.get('decision_date', '')}\n"
            f"관련법령: {m.get('related_laws', '')}\n"
            f"제목: {m.get('title', '')}\n\n"
            f"[재결 요지]\n{doc}"
        )
    except Exception as e:
        return f"조회 실패: {e}"


@tool
def analyze_trend(tax_type: str = "", year: str = "") -> str:
    """재결 트렌드를 분석합니다.

    Args:
        tax_type: 세목 필터 (선택)
        year: 연도 필터 (선택, 예: '2023')
    """
    try:
        col = _get_col()
        where = None
        filters = []
        if tax_type:
            filters.append({"tax_type": {"$eq": tax_type}})
        if len(filters) == 1:
            where = filters[0]

        kwargs = {"limit": 500, "include": ["metadatas"]}
        if where:
            kwargs["where"] = where
        r = col.get(**kwargs)

        yr_count: dict = {}
        d_count: dict = {}
        for m in r["metadatas"] or []:
            dd = m.get("decision_date", "")
            yr = dd[:4] if dd else "미상"
            d = m.get("decision", "미상")
            yr_count[yr] = yr_count.get(yr, 0) + 1
            d_count[d] = d_count.get(d, 0) + 1

        yr_lines = sorted(yr_count.items(), key=lambda x: x[0], reverse=True)[:6]
        d_lines = sorted(d_count.items(), key=lambda x: -x[1])

        lines = [
            f"분석 대상: {sum(yr_count.values())}건",
            "",
            "연도별 분포:",
            *[f"  {k}년: {v}건" for k, v in yr_lines],
            "",
            "결정 유형:",
            *[f"  {k}: {v}건" for k, v in d_lines],
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"트렌드 분석 실패: {e}"


@tool
def find_winning_strategy(fact_summary: str, tax_type: str = "") -> str:
    """사건 요약 → 유사 납세자 유리 재결례 + 전략.

    Args:
        fact_summary: 사건 사실관계 요약
        tax_type: 세목 (선택)
    """
    try:
        col = _get_col()
        where = {"decision": {"$in": ["인용", "취소", "일부인용", "경정"]}}
        if tax_type:
            where = {"$and": [where, {"tax_type": {"$eq": tax_type}}]}
        res = col.query(
            query_texts=[fact_summary],
            n_results=min(6, col.count()),
            where=where,
            include=["metadatas", "documents"],
        )
        lines = []
        for i, doc_id in enumerate(res["ids"][0]):
            m = (res["metadatas"][0][i] if res["metadatas"] else {}) or {}
            doc = (res["documents"][0][i] if res["documents"] else "") or ""
            lines.append(
                f"[{i+1}] {m.get('case_no', '')} ({m.get('tax_type', '')} | {m.get('decision', '')} | {m.get('decision_date', '')})\n"
                f"    {m.get('title', '')}\n"
                f"    {doc[:150]}..."
            )
        return "\n\n".join(lines) if lines else "유사 납세자 유리 재결례를 찾지 못했습니다."
    except Exception as e:
        return f"검색 실패: {e}"


# ── Agent ─────────────────────────────────────────────────────────────────────

class TaxtrAgent:
    """자연어 질문 → Chroma taxtr_cases 검색 → GPT 답변 에이전트."""

    def ask(self, question: str) -> str:
        try:
            col = _get_col()
            res = col.query(
                query_texts=[question],
                n_results=min(8, col.count()),
                include=["metadatas", "documents"],
            )
            context_parts = []
            for i, doc_id in enumerate(res["ids"][0]):
                m = (res["metadatas"][0][i] if res["metadatas"] else {}) or {}
                doc = (res["documents"][0][i] if res["documents"] else "") or ""
                context_parts.append(
                    f"[{i+1}] 청구번호: {m.get('dem_no', '')} | 사건: {m.get('case_no', '')} | "
                    f"세목: {m.get('tax_type', '')} | 결정: {m.get('decision', '')} ({m.get('decision_date', '')})\n"
                    f"제목: {m.get('title', '')}\n"
                    f"{doc[:400]}"
                )
            context = "\n\n---\n\n".join(context_parts) or "(관련 재결례 없음)"

            llm = get_llm(model=DEFAULT_MODEL, temperature=0)
            prompt = (
                "당신은 세법 전문 AI 어시스턴트입니다. "
                "아래 조세심판원 재결례 자료를 바탕으로 질문에 답하세요.\n\n"
                f"[관련 재결례]\n{context}\n\n"
                f"[질문]\n{question}\n\n"
                "[답변 지침]\n"
                "- 재결례 자료에 근거해 구체적으로 답변\n"
                "- 청구번호·결정 유형(인용/기각 등)을 명시\n"
                "- 심판원 결정 패턴과 실무적 시사점 포함\n"
                "- 제공된 데이터 외 사실 생성 금지"
            )
            resp = llm.invoke([HumanMessage(content=prompt)])
            return resp.content
        except Exception as e:
            return f"에이전트 오류: {e}"
