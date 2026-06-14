# routers/itcl.py — 이전가격·국제조세 전문 에이전트 API
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from agents.itcl_agent import ITCLAgent
        _agent = ITCLAgent()
    return _agent


class ITCLRequest(BaseModel):
    query: str
    transaction_type: Optional[str] = "기타"
    related_party_country: Optional[str] = ""
    transaction_amount_krw: Optional[int] = 0
    transaction_year: Optional[str] = ""
    messages: Optional[list] = []


@router.post("/ask")
def itcl_ask(req: ITCLRequest):
    """
    특수관계자 거래 정보 → 정상가격 산출 방법(CUP/RPM/COST+/TNMM/PSM) 판단 + 리스크 평가.

    transaction_type 선택지:
    - 유형자산 매각 / 무형자산 양도 / 무형자산 라이선스
    - 용역 제공 / 금전 대여 / 금전 차입
    - 원자재·완제품 매매 / 기타
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")
    try:
        result = _get_agent().run(
            query=req.query.strip(),
            transaction_type=req.transaction_type or "기타",
            related_party_country=req.related_party_country or "",
            transaction_amount_krw=req.transaction_amount_krw or 0,
            transaction_year=req.transaction_year or "",
            messages=req.messages or [],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")
    return {
        "query": req.query,
        "transaction_type": req.transaction_type,
        "preferred_methods": result.get("preferred_methods"),
        "final_report": result.get("final_report"),
        "court_cases": result.get("court_cases"),
        "law_articles": result.get("law_articles"),
        "itcl_issues": result.get("itcl_issues"),
    }
