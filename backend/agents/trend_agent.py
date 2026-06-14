# agents/trend_agent.py
"""
TrendAgent — 판례 트렌드 분석 에이전트

쟁점 키워드와 기간을 입력하면 연도별 납세자 승소율을 집계하고
법리 변천사를 서술합니다.

데이터 소스:
  - Chroma taxlaw_prec: NTS 법원 판례 32,628건 (attr_yr 연도 메타)
  - Chroma taxtr_cases: 조세심판 재결례 2,463건

LangGraph: DataCollector → TrendAnalyzer
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


class TrendState(TypedDict):
    query: str
    messages: list   # 이전 대화 [{role, content}]
    start_year: int
    end_year: int

    trend_data: Optional[dict]
    taxtr_sample: Optional[list]
    final_report: Optional[str]


# ── 1. DataCollector ──────────────────────────────────────────────────────────

def data_collector_node(state: TrendState) -> dict:
    from db.chroma_search import get_taxlaw_prec_stats, search_taxtr_cases

    stats = get_taxlaw_prec_stats(state["query"], n=100)

    # 기간 필터 (attr_yr 범위)
    filtered_year_stats = {
        yr: st
        for yr, st in stats["year_stats"].items()
        if yr.isdigit() and state["start_year"] <= int(yr) <= state["end_year"]
    }
    stats["year_stats"] = filtered_year_stats

    taxtr_sample = search_taxtr_cases(state["query"], n=10)

    return {
        "trend_data": stats,
        "taxtr_sample": taxtr_sample,
    }


# ── 2. TrendAnalyzer ──────────────────────────────────────────────────────────

def trend_analyzer_node(state: TrendState) -> dict:
    data = state.get("trend_data") or {}
    year_stats = data.get("year_stats") or {}
    total = data.get("total_cases", 0)
    taxtr_sample = state.get("taxtr_sample") or []

    if total == 0:
        return {
            "final_report": (
                f"'{state['query']}' 관련 판례 데이터가 없습니다. "
                "다른 검색어를 시도해보세요."
            )
        }

    stats_str = json.dumps(year_stats, ensure_ascii=False, indent=2)
    taxtr_str = json.dumps(taxtr_sample[:5], ensure_ascii=False, indent=2)
    sample_str = json.dumps(data.get("sample") or [], ensure_ascii=False, indent=2)

    hist_section = make_history_section(state.get("messages") or [])
    prompt = (
        "당신은 조세법 전문 리서치 애널리스트다.\n"
        "아래 판례 통계 데이터를 바탕으로 트렌드 분석 보고서를 작성하라."
        + hist_section + "\n\n"
        f"[분석 쟁점]\n{state['query']}\n\n"
        f"[분석 기간]\n{state['start_year']}년 ~ {state['end_year']}년\n\n"
        f"[연도별 납세자 승소율 통계 (법원 판례)]\n{stats_str}\n\n"
        f"[조세심판 재결례 샘플]\n{taxtr_str}\n\n"
        f"[대표 판례 샘플]\n{sample_str}\n\n"
        "다음 구조로 보고서를 작성하라:\n\n"
        f"## 분석 개요\n총 {total}건 법원 판례 분석\n\n"
        "## 연도별 트렌드\n"
        "연도별 승소율 변화 서술 (상승/하락 구간, 전환점)\n\n"
        "## 법리 변천사\n"
        "법원의 판단 기준이 어떻게 변했는지 서술\n\n"
        "## 조세심판 재결 경향\n"
        "심판원의 판단 방향 (인용률, 주요 논리)\n\n"
        "## 최근 트렌드 진단\n"
        "최근 3~5년의 흐름이 납세자에게 유리한가 불리한가\n\n"
        "## 실무 시사점\n"
        "이 트렌드를 어떻게 불복 전략에 활용할 것인가"
    )

    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    return {"final_report": resp.content}


# ── Graph ─────────────────────────────────────────────────────────────────────

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(TrendState)
        g.add_node("data_collector", data_collector_node)
        g.add_node("trend_analyzer", trend_analyzer_node)
        g.set_entry_point("data_collector")
        g.add_edge("data_collector", "trend_analyzer")
        g.add_edge("trend_analyzer", END)
        _graph = g.compile()
    return _graph


class TrendAgent:
    """
    판례 트렌드 분석

    result = agent.run(query="부당행위계산 부인", start_year=2015, end_year=2025)
    result["final_report"]  # 트렌드 보고서 (str)
    result["trend_data"]    # 연도별 통계 원본
    result["taxtr_sample"]  # 조세심판 재결례 샘플
    """

    def run(self, query: str, start_year: int = 2000, end_year: int = 2030, messages: list = []) -> dict:
        initial: TrendState = {
            "query": query,
            "messages": messages,
            "start_year": start_year,
            "end_year": end_year,
            "trend_data": None,
            "taxtr_sample": None,
            "final_report": None,
        }
        return _get_graph().invoke(initial)
