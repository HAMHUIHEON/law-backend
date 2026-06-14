# agents/itcl_agent.py
"""
ITCLAgent — 이전가격(국제조세) 전문 에이전트

특수관계자 거래 정보를 입력하면 정상가격 산출 방법 판단 + 리스크 평가를 반환합니다.

데이터 소스:
  - Chroma taxlaw_prec: 이전가격 관련 법원 판례 32,628건
  - Chroma law_articles: 국제조세조정법·세법 조문 6,687건
  - Neo4j ITCLSearch: ITCL IntegratedSnapshot (65개 버전, 법령 구조 그래프)

LangGraph: Searcher → ITCLAnalyzer
"""

import json
from typing import Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph
from agents.conversation import build_context_query, make_history_section

_llm = None

ITCL_SYSTEM = (
    "당신은 국제조세·이전가격 전문 세무사다. "
    "OECD 이전가격 지침, 국제조세조정에 관한 법률, 관련 판례에 정통하다. "
    "정상가격 산출 방법(CUP, RPM, COST+, TNMM, PSM)의 적용 기준을 명확히 알고 있다."
)

# 거래 유형별 우선 적용 방법 (OECD TPG 2022 + 국조법 제5조 기준)
PREFERRED_METHODS: dict[str, list[str]] = {
    "유형자산 매각": ["CUP", "TNMM"],
    "무형자산 양도": ["CUP", "PSM"],
    "무형자산 라이선스": ["CUP", "PSM"],
    "용역 제공": ["COST+", "TNMM"],
    "금전 대여": ["CUP"],           # 정상이자율 — 비교가능 이자율 우선
    "금전 차입": ["CUP"],
    "원자재·완제품 매매": ["CUP", "RPM"],
    "기타": ["TNMM"],               # 가장 범용적인 방법
}

TRANSACTION_TYPE_LABELS = list(PREFERRED_METHODS.keys())

# APA 적합성 기준
APA_THRESHOLD_KRW = 5_000_000_000  # 50억원 이상 거래 시 APA 고려

METHOD_DESCRIPTIONS = {
    "CUP": "비교가능 비통제 가격법 (Comparable Uncontrolled Price) — 비교 대상 독립 거래 가격이 있을 때 최우선",
    "RPM": "재판매 가격법 (Resale Price Method) — 완제품 구입 후 재판매하는 판매 법인에 적합",
    "COST+": "원가 가산법 (Cost Plus Method) — 반제품·원자재 공급, 용역 제공 법인에 적합",
    "TNMM": "거래순이익률법 (Transactional Net Margin Method) — 가장 범용적, 비교가능성 요건 낮음",
    "PSM": "이익분할법 (Profit Split Method) — 무형자산이 양사에 공유될 때, 또는 다른 방법 적용 불가 시",
}


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm


class ITCLState(TypedDict):
    query: str
    messages: list   # 이전 대화 [{role, content}]
    transaction_type: str     # TRANSACTION_TYPE_LABELS 중 하나
    related_party_country: str  # 상대방 국가 (조세조약 확인용)
    transaction_amount_krw: int  # 거래 금액 (원화, APA 고려 기준)
    transaction_year: str     # 거래 연도 (법령 시점 참고용)

    preferred_methods: list[str]  # 거래 유형 기반 사전 결정
    court_cases: Optional[list]
    law_articles: Optional[list]
    itcl_issues: Optional[list]

    final_report: Optional[str]


# ── 1. Searcher ───────────────────────────────────────────────────────────────

def searcher_node(state: ITCLState) -> dict:
    from db.chroma_search import search_taxlaw_prec, search_law_articles

    # 거래 유형을 쿼리에 반영
    tx_type = state.get("transaction_type") or "기타"
    itcl_query = build_context_query(
        f"이전가격 특수관계자 국제조세 {tx_type} {state['query']}",
        state.get("messages") or [],
    )

    court_cases = search_taxlaw_prec(itcl_query, n=8)
    law_articles = search_law_articles(itcl_query, n=8)  # 국조법 조문 우선

    # ITCLSearch (Neo4j ITCL 그래프) — 실패 시 graceful 처리
    itcl_issues = []
    try:
        from db.itcl_search import ITCLSearch
        itcl = ITCLSearch()
        itcl_issues = itcl.search_issues(state["query"], top_k=5) or []
        itcl.close()
    except Exception:
        pass

    # Citation Guard를 위한 preferred_methods 결정
    preferred = PREFERRED_METHODS.get(tx_type, ["TNMM"])

    return {
        "preferred_methods": preferred,
        "court_cases": court_cases,
        "law_articles": law_articles,
        "itcl_issues": itcl_issues,
    }


# ── 2. ITCLAnalyzer ───────────────────────────────────────────────────────────

def analyzer_node(state: ITCLState) -> dict:
    cases_str = json.dumps(state.get("court_cases") or [], ensure_ascii=False, indent=2)
    law_str = json.dumps(state.get("law_articles") or [], ensure_ascii=False, indent=2)
    issues_str = json.dumps(state.get("itcl_issues") or [], ensure_ascii=False, indent=2)

    tx_type = state.get("transaction_type") or "기타"
    preferred = state.get("preferred_methods") or ["TNMM"]
    country = state.get("related_party_country") or ""
    amount_krw = state.get("transaction_amount_krw") or 0
    tx_year = state.get("transaction_year") or ""

    # 우선 방법 설명 구성
    preferred_desc = "\n".join(
        f"  ✅ {m}: {METHOD_DESCRIPTIONS.get(m, '')}" for m in preferred
    )
    other_methods = [m for m in METHOD_DESCRIPTIONS if m not in preferred]
    other_desc = "\n".join(
        f"  ❌ {m}: {METHOD_DESCRIPTIONS.get(m, '')} — 이 거래 유형({tx_type})에는 부적합"
        for m in other_methods
    )

    # APA 권고 여부
    apa_note = ""
    if amount_krw >= APA_THRESHOLD_KRW:
        apa_note = f"\n⚡ **거래 금액 {amount_krw:,}원 — 사전가격합의(APA) 신청 검토 권고** (국조법 제14조)"

    # 조세조약 참고
    treaty_note = f"\n📋 상대방 국가 [{country}] — 한·{country} 조세조약의 이전가격 조항 별도 검토 필요" if country else ""

    # 법령 시점 참고
    year_note = f"\n📅 거래 연도 [{tx_year}] 기준 국조법 조문 적용 — 현행법과 다를 수 있으니 개정 이력 확인 필요" if tx_year else ""

    hist_section = make_history_section(state.get("messages") or [])
    prompt = (
        f"{ITCL_SYSTEM}\n\n"
        "아래 정보를 바탕으로 이전가격 분석 보고서를 작성하라."
        + hist_section + "\n\n"
        f"[질의/거래 정보]\n{state['query']}\n"
        f"[거래 유형] {tx_type}"
        + (f"\n[상대방 국가] {country}" if country else "")
        + (f"\n[거래 금액] {amount_krw:,}원" if amount_krw else "")
        + (f"\n[거래 연도] {tx_year}" if tx_year else "")
        + f"{apa_note}{treaty_note}{year_note}\n\n"
        f"[관련 법원 판례 (검색된 데이터만 인용)]\n{cases_str}\n\n"
        f"[국제조세조정법 관련 세법 조문]\n{law_str}\n\n"
        f"[ITCL 법령 쟁점 (Neo4j)]\n{issues_str}\n\n"
        "다음 구조로 보고서를 작성하라:\n\n"
        "## 1. 거래 개요 및 쟁점\n\n"
        f"## 2. 정상가격 산출 방법 검토\n\n"
        f"### 권장 방법 ({tx_type} 거래 기준)\n"
        f"{preferred_desc}\n\n"
        f"  각 권장 방법의 적용 요건, 비교가능성 확보 방안, 실무 적용 시 주의사항을 상세히 서술\n\n"
        f"### 기타 방법 (적용 불가 사유)\n"
        f"{other_desc}\n\n"
        "  각 방법이 이 거래에 부적합한 구체적 이유 1~2문장씩\n\n"
        "## 3. 관련 판례 시사점\n"
        "  유사 거래에서 법원이 어떤 방법을 인정했는지 — 검색된 판례번호 명시\n\n"
        "## 4. 법령 근거\n"
        "  국제조세조정법 관련 조문 (검색된 조문만)\n\n"
        "## 5. 리스크 평가\n"
        "  - 과세관청이 문제 삼을 가능성이 높은 부분\n"
        "  - 이전가격 세무조사 대비 체크리스트\n"
        + (f"  - APA 신청 검토 (국조법 제14조)\n" if amount_krw >= APA_THRESHOLD_KRW else "")
        + "\n"
        "## 6. 권고 사항\n\n"
        "[규칙] 검색 데이터에 없는 판례번호·조문번호 생성 금지. "
        "비교가능 회사/거래의 실제 이익률 데이터는 별도 확인이 필요함을 명시."
    )

    resp = _get_llm().invoke([HumanMessage(content=prompt)])

    # Citation Guard
    from utils.citation_guard import apply_citation_guard
    guarded, _ = apply_citation_guard(
        resp.content,
        state.get("court_cases") or [],
    )

    return {"final_report": guarded}


# ── Graph ─────────────────────────────────────────────────────────────────────

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(ITCLState)
        g.add_node("searcher", searcher_node)
        g.add_node("analyzer", analyzer_node)
        g.set_entry_point("searcher")
        g.add_edge("searcher", "analyzer")
        g.add_edge("analyzer", END)
        _graph = g.compile()
    return _graph


class ITCLAgent:
    """
    이전가격·국제조세 전문 분석

    result = agent.run(
        query="A사가 해외 특수관계법인에 제품을 시가보다 낮은 가격으로 공급...",
        transaction_type="원자재·완제품 매매",   # TRANSACTION_TYPE_LABELS 중 선택
        related_party_country="싱가포르",
        transaction_amount_krw=8_000_000_000,    # 80억원
        transaction_year="2023",
    )
    result["final_report"]    # 이전가격 분석 보고서 (거래 유형별 적합 방법 강조)
    result["court_cases"]     # 관련 판례
    result["law_articles"]    # 관련 조문
    result["itcl_issues"]     # ITCL 법령 쟁점 (Neo4j)
    result["preferred_methods"] # 이 거래에 적합한 방법 목록

    거래 유형 선택지:
    """ + "\n    ".join(f'"{t}"' for t in TRANSACTION_TYPE_LABELS)

    def run(
        self,
        query: str,
        transaction_type: str = "기타",
        related_party_country: str = "",
        transaction_amount_krw: int = 0,
        transaction_year: str = "",
        messages: list = [],
    ) -> dict:
        initial: ITCLState = {
            "query": query,
            "messages": messages,
            "transaction_type": transaction_type,
            "related_party_country": related_party_country,
            "transaction_amount_krw": transaction_amount_krw,
            "transaction_year": transaction_year,
            "preferred_methods": [],
            "court_cases": None,
            "law_articles": None,
            "itcl_issues": None,
            "final_report": None,
        }
        return _get_graph().invoke(initial)
