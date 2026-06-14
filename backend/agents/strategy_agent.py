# agents/strategy_agent.py
"""
StrategyAgent — 의뢰인 사건 전략 에이전트

의뢰인이 제출한 사건 요약을 받아 유사 판례를 분석하고
경정청구 / 조세심판 / 소송 중 최적 전략을 권고합니다.

데이터 소스:
  - Chroma taxlaw_prec: NTS 법원 판례 32,628건
  - Chroma taxtr_cases: 조세심판 재결례 2,463건
  - Chroma law_articles: 세법 조문 6,687건

LangGraph: FactExtractor → CaseSearcher → Strategist
"""

import json
from datetime import datetime, timedelta
from typing import Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph
from agents.conversation import build_context_query, make_history_section

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm


def _compute_deadlines(disposition_date: str, tax_type: str = "") -> dict:
    """
    불복 기한 계산 (국세기본법 기준).
    disposition_date: "YYYY-MM-DD" 또는 "YYYY.MM.DD"
    """
    if not disposition_date:
        return {}
    try:
        date_str = disposition_date.replace(".", "-")
        base = datetime.strptime(date_str, "%Y-%m-%d")
        today = datetime.now()
        days_elapsed = (today - base).days

        deadlines = {
            "처분일": base.strftime("%Y-%m-%d"),
            "이의신청_마감": (base + timedelta(days=90)).strftime("%Y-%m-%d"),
            "심판청구_마감": (base + timedelta(days=90)).strftime("%Y-%m-%d"),
            "행정소송_마감": "심판청구 결정 통지일로부터 90일 이내",
        }

        # 경정청구 기한 (신고세목별 상이)
        if any(t in tax_type for t in ["법인세", "소득세", "부가가치세", "종합소득"]):
            deadlines["경정청구_마감"] = f"법정신고기한으로부터 5년 이내 (국세기본법 제45조의2)"
        else:
            deadlines["경정청구_마감"] = "법정신고기한으로부터 5년 이내 (세목별 확인 필요)"

        # 이미 기한이 지났는지 경고
        overdue = []
        for key in ["이의신청_마감", "심판청구_마감"]:
            try:
                dl = datetime.strptime(deadlines[key], "%Y-%m-%d")
                if today > dl:
                    overdue.append(key.replace("_마감", ""))
            except Exception:
                pass

        if overdue:
            deadlines["⚠️_경고"] = f"경과 {days_elapsed}일 — {', '.join(overdue)} 기한 초과 가능성. 반드시 확인 필요."

        return deadlines
    except Exception:
        return {"경고": f"처분일 파싱 실패 ({disposition_date}) — 직접 확인 필요"}


def _win_rate_summary(cases: list) -> str:
    """유사 판례에서 납세자 승소율 계산."""
    if not cases:
        return "판례 데이터 없음"
    win_kw = {"취소", "인용", "승소", "원고 승", "납세자 승"}
    total = len(cases)
    wins = sum(1 for c in cases if any(kw in str(c.get("decision", "")) for kw in win_kw))
    rate = round(wins / total * 100, 1) if total else 0
    return f"유사 판례 {total}건 중 납세자 승소 {wins}건 ({rate}%)"


class StrategyState(TypedDict):
    client_summary: str
    disposition_date: str   # "YYYY-MM-DD" 또는 "YYYY.MM.DD" — 기한 계산용
    tax_amount: str         # 처분 세액 (참고용)
    already_filed: bool     # 이미 이의신청/심판청구 제출 여부
    messages: list          # 이전 대화 [{role, content}]

    key_facts: str
    legal_issues: list
    tax_type: str
    deadlines: dict

    court_cases: Optional[list]
    taxtr_cases: Optional[list]
    law_articles: Optional[list]

    final_report: Optional[str]


# ── 1. FactExtractor ──────────────────────────────────────────────────────────

def fact_extractor_node(state: StrategyState) -> dict:
    prompt = (
        "당신은 세무·법률 전문가다. 아래 의뢰인 사건 요약을 분석해 JSON으로만 반환하라.\n\n"
        f"사건 요약:\n{state['client_summary']}\n\n"
        "반환 형식 (JSON만, 다른 텍스트 없음):\n"
        '{"key_facts": "핵심 사실관계 요약 (3~5문장)", '
        '"legal_issues": ["쟁점 1 (법적 쟁점 형태)", "쟁점 2"], '
        '"tax_type": "세목 (예: 법인세, 소득세, 부가가치세, 국제조세, 상속세)"}'
    )
    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    try:
        data = json.loads(resp.content.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        data = {
            "key_facts": state["client_summary"],
            "legal_issues": [state["client_summary"][:100]],
            "tax_type": "미분류",
        }

    tax_type = data.get("tax_type", "미분류")
    deadlines = _compute_deadlines(state.get("disposition_date", ""), tax_type)

    return {
        "key_facts": data.get("key_facts", ""),
        "legal_issues": data.get("legal_issues") or [state["client_summary"][:100]],
        "tax_type": tax_type,
        "deadlines": deadlines,
    }


# ── 2. CaseSearcher ───────────────────────────────────────────────────────────

def case_searcher_node(state: StrategyState) -> dict:
    from db.chroma_search import search_taxlaw_prec, search_taxtr_cases, search_law_articles

    combined_query = build_context_query(
        " ".join(state["legal_issues"]) + " " + state.get("tax_type", ""),
        state.get("messages") or [],
    )

    court_cases = search_taxlaw_prec(combined_query, n=10)
    taxtr_cases = search_taxtr_cases(combined_query, n=6)
    law_articles = search_law_articles(combined_query, n=5)

    return {
        "court_cases": court_cases,
        "taxtr_cases": taxtr_cases,
        "law_articles": law_articles,
    }


# ── 3. Strategist ─────────────────────────────────────────────────────────────

def strategist_node(state: StrategyState) -> dict:
    court_str = json.dumps(state.get("court_cases") or [], ensure_ascii=False, indent=2)
    taxtr_str = json.dumps(state.get("taxtr_cases") or [], ensure_ascii=False, indent=2)
    law_str = json.dumps(state.get("law_articles") or [], ensure_ascii=False, indent=2)

    win_summary = _win_rate_summary(state.get("court_cases") or [])
    deadlines = state.get("deadlines") or {}
    deadline_str = "\n".join(f"  - {k}: {v}" for k, v in deadlines.items())
    already_filed_note = "⚠️ 이미 이의신청/심판청구 제출됨 — 중복 제출 불가 경로 있음" if state.get("already_filed") else ""

    hist_section = make_history_section(state.get("messages") or [])
    prompt = (
        "당신은 국세청 경력 10년의 세무사 겸 조세전문 변호사다.\n"
        "아래 의뢰인 사건과 판례·재결례·조문 분석 결과를 바탕으로 전략 보고서를 작성하라."
        + hist_section + "\n\n"
        f"[의뢰인 사건 요약]\n{state['client_summary']}\n\n"
        f"[핵심 사실관계]\n{state['key_facts']}\n\n"
        f"[세목] {state.get('tax_type', '미분류')}"
        + (f"\n[처분 세액] {state['tax_amount']}" if state.get("tax_amount") else "") + "\n\n"
        f"[⏰ 불복 기한 — 반드시 보고서에 포함]\n{deadline_str}\n"
        + (f"\n{already_filed_note}\n" if already_filed_note else "") + "\n"
        f"[주요 쟁점]\n" + "\n".join(f"- {i}" for i in state["legal_issues"]) + "\n\n"
        f"[유사 판례 승소율 통계]\n{win_summary}\n\n"
        f"[유사 법원 판례 (검색된 데이터 — 인용 시 case_no 번호만 사용)]\n{court_str}\n\n"
        f"[유사 조세심판 재결례 (검색된 데이터)]\n{taxtr_str}\n\n"
        f"[관련 세법 조문 (검색된 데이터)]\n{law_str}\n\n"
        "다음 구조로 보고서를 작성하라:\n\n"
        "## 1. 사건 개요\n\n"
        "## 2. 핵심 쟁점\n\n"
        "## 3. 불복 기한 정리 (⚠️ 최우선)\n"
        "  기한별 날짜와 절차 의의를 반드시 명시. 이미 경과한 기한은 경고 표시.\n\n"
        "## 4. 유사 판례·재결례 분석\n"
        "  판례번호/재결번호, 결론, 이 사건과의 유사점·차이점\n"
        f"  승소율: {win_summary}\n\n"
        "## 5. 전략 권고\n"
        "  ### 경정청구 (가능 여부, 기한, 예상 승산)\n"
        "  ### 이의신청 (강·약점, 효과)\n"
        "  ### 심판청구 (강·약점, 재결 경향, 예상 승산)\n"
        "  ### 행정소송 (강·약점, 전심 요건)\n"
        "  ### ✅ 최종 권고: 어떤 경로를 먼저 선택해야 하는가 (이유 포함)\n\n"
        "## 6. 관련 법령 근거\n\n"
        "## 7. 리스크 포인트 (납세자·과세관청 각각)\n\n"
        "## 8. 즉시 준비해야 할 증거·서류 체크리스트\n\n"
        "[규칙] 위 검색 데이터에 없는 판례·조문 번호 생성 금지. 불확실한 승산은 '확인 필요'로 처리."
    )

    resp = _get_llm().invoke([HumanMessage(content=prompt)])

    # Citation Guard 적용
    from utils.citation_guard import apply_citation_guard
    guarded, _ = apply_citation_guard(
        resp.content,
        state.get("court_cases") or [],
        state.get("taxtr_cases") or [],
    )

    return {"final_report": guarded}


# ── Graph ─────────────────────────────────────────────────────────────────────

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(StrategyState)
        g.add_node("fact_extractor", fact_extractor_node)
        g.add_node("case_searcher", case_searcher_node)
        g.add_node("strategist", strategist_node)
        g.set_entry_point("fact_extractor")
        g.add_edge("fact_extractor", "case_searcher")
        g.add_edge("case_searcher", "strategist")
        g.add_edge("strategist", END)
        _graph = g.compile()
    return _graph


class StrategyAgent:
    """
    의뢰인 사건 요약 → 유사 판례 검색 → 전략 권고 (경정청구/이의신청/심판청구/행정소송)

    result = agent.run(
        client_summary="...",
        disposition_date="2025-03-15",   # YYYY-MM-DD — 불복 기한 자동 계산
        tax_amount="500,000,000원",       # 참고용
        already_filed=False,             # 이미 불복 절차 진행 중인지
    )
    result["final_report"]   # 전략 보고서 (불복 기한 포함)
    result["court_cases"]    # 유사 법원 판례 목록
    result["taxtr_cases"]    # 유사 재결례 목록
    result["law_articles"]   # 관련 조문
    result["deadlines"]      # 불복 기한 dict
    """

    def run(
        self,
        client_summary: str,
        disposition_date: str = "",
        tax_amount: str = "",
        already_filed: bool = False,
        messages: list = [],
    ) -> dict:
        initial: StrategyState = {
            "client_summary": client_summary,
            "disposition_date": disposition_date,
            "tax_amount": tax_amount,
            "already_filed": already_filed,
            "messages": messages,
            "key_facts": "",
            "legal_issues": [],
            "tax_type": "",
            "deadlines": {},
            "court_cases": None,
            "taxtr_cases": None,
            "law_articles": None,
            "final_report": None,
        }
        return _get_graph().invoke(initial)
