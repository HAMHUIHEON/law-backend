# routers/taxlaw_prec.py — 세법 법원 판례 에이전트 API (taxlaw.nts.go.kr)
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    tax_type: Optional[str] = ""
    decision: Optional[str] = ""
    n_results: int = 8


class AskRequest(BaseModel):
    question: str
    messages: Optional[list] = []


@router.get("/stats")
def get_stats():
    """법원 판례 DB 현황."""
    from agents.taxlaw_prec_agent import get_collection_stats
    return {"result": get_collection_stats.invoke({})}


@router.post("/search")
def search(req: SearchRequest):
    """법원 판례 벡터 검색."""
    from agents.taxlaw_prec_agent import search_court_cases
    result = search_court_cases.invoke({
        "query": req.query,
        "tax_type": req.tax_type or "",
        "decision": req.decision or "",
        "n_results": req.n_results,
    })
    return {"result": result}


@router.get("/case/{doc_id}")
def get_case(doc_id: str):
    """특정 판례 전문 조회."""
    from agents.taxlaw_prec_agent import get_case_detail
    result = get_case_detail.invoke({"doc_id": doc_id})
    if "찾을 수 없습니다" in result:
        raise HTTPException(status_code=404, detail=result)
    return {"result": result}


@router.get("/trend")
def trend(tax_type: str = "", decision: str = ""):
    """결정 트렌드 분석."""
    from agents.taxlaw_prec_agent import analyze_trend
    result = analyze_trend.invoke({"tax_type": tax_type, "decision": decision})
    return {"result": result}


@router.post("/winning")
def winning(req: AskRequest):
    """사건 사실관계 입력 → 유사 납세자 승소 판례 + 전략."""
    from agents.taxlaw_prec_agent import find_winning_cases
    result = find_winning_cases.invoke({
        "fact_summary": req.question,
        "tax_type": "",
    })
    return {"result": result}


@router.post("/ask")
def ask_agent(req: AskRequest):
    """
    법원 판례 에이전트에게 자연어로 질문합니다.

    예시:
    - "부가가치세 매입세액 불공제 관련 납세자가 이긴 판례 있나요?"
    - "이전가격 정상가격 산출 방법 관련 최근 판례 트렌드는?"
    - "법인세 부당행위계산 부인 처분에서 국패 비율이 어떻게 되나요?"
    """
    from agents.taxlaw_prec_agent import TaxlawPrecAgent

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question이 비어 있습니다.")

    try:
        agent = TaxlawPrecAgent()
        answer = agent.ask(req.question, messages=req.messages or [])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")

    return {"question": req.question, "answer": answer}
