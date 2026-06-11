# routers/agent.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from agents.insight_agent import InsightAgent
from agents.multi_agent import SupervisorAgent
from services.pipeline import get_current_user_id

router = APIRouter()
_agent: "InsightAgent | None" = None
_supervisor: "SupervisorAgent | None" = None

def _get_agent() -> InsightAgent:
    global _agent
    if _agent is None:
        _agent = InsightAgent()
    return _agent

def _get_supervisor() -> SupervisorAgent:
    global _supervisor
    if _supervisor is None:
        _supervisor = SupervisorAgent()
    return _supervisor


class InsightRequest(BaseModel):
    query: str
    case_id: Optional[str] = None  # 예: "2023누1234" — 없으면 검색+패턴 분석만 수행


class MultiRequest(BaseModel):
    query: str


@router.post("/insight")
def run_insight(
    req: InsightRequest,
    user_id: str = Depends(get_current_user_id),
):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")

    result = _get_agent().run(query=req.query, case_id=req.case_id)
    return result


@router.post("/multi")
def run_multi(
    req: MultiRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    SupervisorAgent: 판례 DB + ITCL 법령 레이어를 결합한 멀티 에이전트.
    InsightAgent보다 풍부한 법령 컨텍스트(SemanticIssue + 관련 조문)를 포함합니다.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")

    result = _get_supervisor().run(query=req.query)
    return result
