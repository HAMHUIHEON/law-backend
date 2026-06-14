# routers/agent.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from agents.insight_agent import InsightAgent
from agents.multi_agent import SupervisorAgent

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
    case_id: Optional[str] = None
    messages: Optional[list] = []


class MultiRequest(BaseModel):
    query: str
    messages: Optional[list] = []


@router.post("/insight")
def run_insight(req: InsightRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")
    try:
        result = _get_agent().run(query=req.query, case_id=req.case_id, messages=req.messages or [])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")


@router.post("/multi")
def run_multi(req: MultiRequest):
    """
    SupervisorAgent: Neo4j 판례 + Chroma 법령·판례·재결례 통합 멀티 에이전트.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")
    try:
        result = _get_supervisor().run(query=req.query, messages=req.messages or [])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")
