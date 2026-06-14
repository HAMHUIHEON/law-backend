# routers/trend.py — 판례 트렌드 분석 API
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from agents.trend_agent import TrendAgent
        _agent = TrendAgent()
    return _agent


class TrendRequest(BaseModel):
    query: str
    start_year: Optional[int] = 2000
    end_year: Optional[int] = 2030
    messages: Optional[list] = []


@router.post("/ask")
def trend_ask(req: TrendRequest):
    """쟁점 키워드 → 연도별 납세자 승소율 + 법리 변천사 분석."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")
    try:
        result = _get_agent().run(
            query=req.query.strip(),
            start_year=req.start_year or 2000,
            end_year=req.end_year or 2030,
            messages=req.messages or [],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")
    return {
        "query": req.query,
        "final_report": result.get("final_report"),
        "trend_data": result.get("trend_data"),
        "taxtr_sample": result.get("taxtr_sample"),
    }
