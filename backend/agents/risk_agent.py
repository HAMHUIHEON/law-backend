# agents/risk_agent.py
"""
RiskAgent — 법령 개정 리스크 분석 에이전트

새 법령 개정 내용을 입력하면 해당 법령 관련 기존 판례·재결례의 유효성을 재평가하고
리스크 요약 리포트를 생성합니다.

데이터 소스:
  - Chroma taxlaw_prec: 법원 판례 32,628건 (법령명으로 검색)
  - Chroma taxtr_cases: 조세심판 재결례 2,463건
  - Chroma law_articles: 세법 조문 6,687건 (개정 조문 검색)

LangGraph: CaseFinder → RiskEvaluator
"""

import json
from typing import Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph
from agents.conversation import make_history_section

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm


class RiskState(TypedDict):
    statute_name: str       # 개정된 법령명 (예: "법인세법")
    revision_summary: str   # 개정 내용 요약
    effective_date: str     # 시행일
    messages: list          # 이전 대화 [{role, content}]

    affected_court_cases: Optional[list]
    affected_taxtr_cases: Optional[list]
    revised_articles: Optional[list]
    final_report: Optional[str]


# ── 1. CaseFinder ─────────────────────────────────────────────────────────────

def case_finder_node(state: RiskState) -> dict:
    from db.chroma_search import search_taxlaw_prec, search_taxtr_cases, search_law_articles

    # 법령명 + 개정 내용으로 검색
    statute_query = state["statute_name"] + " " + state["revision_summary"][:100]

    court_cases = search_taxlaw_prec(statute_query, n=15)
    taxtr_cases = search_taxtr_cases(statute_query, n=8)
    revised_articles = search_law_articles(state["statute_name"], n=6)

    return {
        "affected_court_cases": court_cases,
        "affected_taxtr_cases": taxtr_cases,
        "revised_articles": revised_articles,
    }


# ── 2. RiskEvaluator ──────────────────────────────────────────────────────────

def risk_evaluator_node(state: RiskState) -> dict:
    court_cases = state.get("affected_court_cases") or []
    taxtr_cases = state.get("affected_taxtr_cases") or []
    revised_articles = state.get("revised_articles") or []

    if not court_cases and not taxtr_cases:
        return {
            "final_report": (
                f"'{state['statute_name']}' 관련 판례·재결례 데이터가 없습니다.\n"
                "법령명을 공식 명칭으로 입력하세요 (예: '법인세법', '국세기본법')."
            )
        }

    court_str = json.dumps(court_cases[:15], ensure_ascii=False, indent=2)
    taxtr_str = json.dumps(taxtr_cases[:8], ensure_ascii=False, indent=2)
    articles_str = json.dumps(revised_articles, ensure_ascii=False, indent=2)

    hist_section = make_history_section(state.get("messages") or [])
    prompt = (
        "당신은 세무·법률 전문가다. 법령 개정이 기존 판례·재결례에 미치는 영향을 분석하라."
        + hist_section + "\n\n"
        f"[개정 법령]\n{state['statute_name']}\n\n"
        f"[개정 내용]\n{state['revision_summary']}\n\n"
        f"[시행일]\n{state['effective_date']}\n\n"
        f"[관련 세법 조문]\n{articles_str}\n\n"
        f"[해당 법령 관련 법원 판례]\n{court_str}\n\n"
        f"[해당 법령 관련 조세심판 재결례]\n{taxtr_str}\n\n"
        "다음 구조로 리스크 보고서를 작성하라:\n\n"
        "## 개정 내용 요약\n\n"
        "## 영향 받는 판례 및 재결례 분류\n"
        "  ### 🔴 고위험: 개정 후 결론이 달라질 수 있는 판례\n"
        "  ### 🟡 주의: 법리를 재검토해야 하는 판례\n"
        "  ### 🟢 영향 없음: 개정과 무관한 쟁점의 판례\n\n"
        "## 핵심 리스크 포인트\n"
        "  개정 후 가장 주의해야 할 3가지\n\n"
        "## 실무 대응 방안\n"
        "  - 개정 전 처분 건: 경정청구 가능성\n"
        "  - 개정 후 신규 거래: 변경해야 할 사항\n"
        "  - 세무조사 대비 체크리스트"
    )

    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    return {"final_report": resp.content}


# ── Graph ─────────────────────────────────────────────────────────────────────

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(RiskState)
        g.add_node("case_finder", case_finder_node)
        g.add_node("risk_evaluator", risk_evaluator_node)
        g.set_entry_point("case_finder")
        g.add_edge("case_finder", "risk_evaluator")
        g.add_edge("risk_evaluator", END)
        _graph = g.compile()
    return _graph


class RiskAgent:
    """
    법령 개정 리스크 분석

    result = agent.run(
        statute_name="법인세법",
        revision_summary="부당행위계산 부인 요건 중 시가 범위를 ±5%에서 ±3%로 강화",
        effective_date="2025-01-01"
    )
    result["final_report"]          # 리스크 보고서 (str)
    result["affected_court_cases"]  # 영향 받는 법원 판례
    result["affected_taxtr_cases"]  # 영향 받는 조세심판 재결례
    result["revised_articles"]      # 개정 조문
    """

    def run(self, statute_name: str, revision_summary: str, effective_date: str = "", messages: list = []) -> dict:
        initial: RiskState = {
            "statute_name": statute_name,
            "revision_summary": revision_summary,
            "effective_date": effective_date or "미상",
            "messages": messages,
            "affected_court_cases": None,
            "affected_taxtr_cases": None,
            "revised_articles": None,
            "final_report": None,
        }
        return _get_graph().invoke(initial)
