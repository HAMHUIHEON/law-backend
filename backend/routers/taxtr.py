# routers/taxtr.py — 조세심판원 재결례 에이전트 API
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


# ── 요청 모델 ────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    tax_type: Optional[str] = ""
    decision: Optional[str] = ""
    n_results: int = 8


class AskRequest(BaseModel):
    question: str
    messages: Optional[list] = []


# ── 엔드포인트 ───────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats():
    """재결례 DB 현황."""
    from agents.taxtr_agent import get_collection_stats
    return {"result": get_collection_stats.invoke({})}


@router.post("/search")
def search(req: SearchRequest):
    """재결례 벡터 검색."""
    from agents.taxtr_agent import search_cases
    result = search_cases.invoke({
        "query": req.query,
        "tax_type": req.tax_type or "",
        "decision": req.decision or "",
        "n_results": req.n_results,
    })
    return {"result": result}


@router.get("/case/{dem_no}")
def get_case(dem_no: str):
    """특정 재결례 전문 조회."""
    from agents.taxtr_agent import get_case_detail
    result = get_case_detail.invoke({"dem_no": dem_no})
    if "찾을 수 없습니다" in result:
        raise HTTPException(status_code=404, detail=result)
    return {"result": result}


@router.get("/trend")
def trend(tax_type: str = "", year: str = ""):
    """결정 유형 트렌드 분석."""
    from agents.taxtr_agent import analyze_trend
    result = analyze_trend.invoke({"tax_type": tax_type, "year": year})
    return {"result": result}


@router.post("/strategy")
def strategy(req: AskRequest):
    """사건 사실관계 입력 → 유사 승소 사례 + 전략."""
    from agents.taxtr_agent import find_winning_strategy
    result = find_winning_strategy.invoke({
        "fact_summary": req.question,
        "tax_type": "",
    })
    return {"result": result}


@router.post("/ask")
def ask_agent(req: AskRequest):
    """
    재결례 에이전트에게 자연어로 질문합니다.

    예시:
    - "이전가격 관련 최근 3년 심판 결정 패턴이 어떻게 되나요?"
    - "법인세 부당행위계산 부인에서 납세자가 이긴 사례가 있나요?"
    - "소득세 기타소득 관련 심판 결정 중 취소가 많은가요?"
    """
    from agents.taxtr_agent import TaxtrAgent

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question이 비어 있습니다.")

    try:
        agent = TaxtrAgent()
        answer = agent.ask(req.question, messages=req.messages or [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")

    return {"question": req.question, "answer": answer}
