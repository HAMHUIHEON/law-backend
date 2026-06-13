# agents/multi_agent.py
"""
SupervisorAgent — 판례 검색 + 세법 조문 + 판례 DB 통합 멀티 에이전트

구조 (LangGraph):
  START
    └─▶ supervisor  ─ 도구 선택 + 소스별 최적화 검색쿼리 생성
          ├─▶ case_search_node      ─ LegalGraphSearch (Neo4j 벡터 + 패턴)
          ├─▶ law_search_node       ─ Chroma law_articles (14개 세법 6,687조문)
          ├─▶ taxlaw_prec_node      ─ Chroma taxlaw_prec (NTS 법원 판례 32K)
          ├─▶ taxtr_node            ─ Chroma taxtr_cases (조세심판원 2,463건)
          └─▶ synthesizer           ─ 모든 결과를 합쳐 보고서 작성
  END
"""

import json
import re
from typing import Annotated, List, Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph
from operator import add as list_add

from db.graph_search import LegalGraphSearch
from db.chroma_search import (
    search_law_articles as _chroma_law,
    search_taxlaw_prec as _chroma_prec,
    search_taxtr_cases as _chroma_taxtr,
)

# 국제조세/이전가격 관련 키워드 → ITCL 전용 fallback 쿼리 사용
_ITCL_KEYWORDS = ["이전가격", "국제조세", "정상가격", "이전가", "BEPS", "필라", "pillar",
                   "CFC", "과세가격", "국조법", "독립기업", "국외특수관계", "조세조약"]
_ITCL_LAW_QUERY = "국제조세조정법 정상가격 독립기업원칙 국외특수관계인 이전가격 과세표준 경정"
_ITCL_PREC_QUERY = "법인세 정상가격 국외특수관계법인 이전가격 과세처분 경정 취소"


def _is_itcl_query(q: str) -> bool:
    q_lower = q.lower()
    return any(kw in q_lower for kw in _ITCL_KEYWORDS)

_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm


# ── State ─────────────────────────────────────────────────────────────────────

class MultiAgentState(TypedDict):
    query: str

    # Supervisor 결정
    plan: List[str]
    done_tools: Annotated[List[str], list_add]
    iteration: int

    # 소스별 최적화 쿼리 (supervisor가 생성)
    law_query: Optional[str]
    prec_query: Optional[str]
    taxtr_query: Optional[str]

    # 도구 결과
    case_results: Optional[list]
    pattern_results: Optional[dict]
    law_articles: Optional[list]

    # Chroma 결과
    taxlaw_prec_results: Optional[list]
    taxtr_results: Optional[list]

    # 최종 보고서
    final_report: Optional[str]


# ── 1. Supervisor ─────────────────────────────────────────────────────────────

def supervisor_node(state: MultiAgentState) -> dict:
    if state["plan"] and state["iteration"] > 0:
        return {"iteration": state["iteration"] + 1}

    prompt = (
        "당신은 세법 법률 리서치 슈퍼바이저다.\n"
        "아래 질문을 분석해 필요한 검색 도구와 각 소스별 최적화 검색쿼리를 JSON으로 반환하라.\n\n"
        f"질문: {state['query']}\n\n"
        "반환 형식 (JSON만, 다른 텍스트 없음):\n"
        '{\n'
        '  "tools": ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_law"],\n'
        '  "law_query": "세법 조문 검색용 — 법령명·조문·핵심 법적 개념 키워드 중심으로 확장",\n'
        '  "prec_query": "법원 판례 검색용 — 세목·사실관계·핵심 쟁점 키워드 중심으로 확장",\n'
        '  "taxtr_query": "조세심판 재결례 검색용 — 처분유형·세목·쟁점 키워드 중심으로 확장"\n'
        '}\n\n'
        "사용 가능한 도구:\n"
        "  - search_cases: Neo4j 국제조세 판례 DB 벡터 검색 + 승소 패턴 분석\n"
        "  - search_law: 세법 조문 검색 (14개 세법 법+령+규칙 6,687조문)\n"
        "  - search_taxlaw_prec: NTS taxlaw 법원 판례 32,628건 (국승/국패 분류)\n"
        "  - search_taxtr: 조세심판원 재결례 2,463건\n\n"
        "도구 선택 규칙:\n"
        "  - 판례·법원 결정·국승/국패 → search_cases + search_taxlaw_prec 포함\n"
        "  - 조세심판·재결례 → search_taxtr 포함\n"
        "  - 조문·법령 해석 → search_law 포함\n"
        "  - 일반 전략·쟁점 분석 → 4개 모두 포함 (최소 2개)\n\n"
        "쿼리 최적화 규칙:\n"
        "  - law_query: 법령명+핵심 법적 개념 조합. 이전가격/국제조세/정상가격 관련이면 반드시\n"
        "    '국제조세조정법 정상가격 독립기업원칙 국외특수관계인'을 포함할 것.\n"
        "  - prec_query: 세목+사실관계 확장. 이전가격이면 '법인세 이전가격 정상가격 국외특수관계법인 과세처분'.\n"
        "  - taxtr_query: 처분 유형+쟁점 확장. 이전가격이면 '이전가격 과세처분 조세심판 정상가격'.\n"
        "  ※ 쿼리는 한국어 세법 DB 벡터 검색용이므로 최대한 구체적 법적 용어를 사용할 것."
    )
    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    try:
        content = resp.content.strip()
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        plan_data = json.loads(json_match.group() if json_match else content)
        tools = plan_data.get("tools") or ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_law"]
        law_query = plan_data.get("law_query") or state["query"]
        prec_query = plan_data.get("prec_query") or state["query"]
        taxtr_query = plan_data.get("taxtr_query") or state["query"]
    except Exception:
        tools = ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_law"]
        law_query = prec_query = taxtr_query = state["query"]

    return {
        "plan": tools,
        "iteration": 1,
        "law_query": law_query,
        "prec_query": prec_query,
        "taxtr_query": taxtr_query,
    }


def _route_supervisor(state: MultiAgentState) -> str:
    done = set(state.get("done_tools") or [])
    plan = state.get("plan") or []
    for t in plan:
        if t not in done:
            return t
    return "synthesizer"


# ── 2. Case Search (Neo4j) ─────────────────────────────────────────────────────

def case_search_node(state: MultiAgentState) -> dict:
    try:
        s = LegalGraphSearch()
        try:
            results = s.search_similar_issues(state["query"], top_k=6)
            patterns = s.analyze_winning_patterns(state["query"], top_k=10)
        finally:
            s.close()
    except Exception as e:
        print(f"[case_search_node] Neo4j 검색 실패: {e}")
        results = []
        patterns = {}
    return {
        "case_results": results,
        "pattern_results": patterns,
        "done_tools": ["search_cases"],
    }


# ── 3. Law Articles Search (Chroma, 14개 세법) ────────────────────────────────

def law_search_node(state: MultiAgentState) -> dict:
    query = state.get("law_query") or state["query"]
    results = _chroma_law(query, n=8)

    # 이전가격/국제조세 쿼리면 ITCL 전용 fallback 검색으로 보강
    if _is_itcl_query(state["query"]):
        itcl_results = _chroma_law(_ITCL_LAW_QUERY, n=6)
        seen = {(r.get("law_name", ""), r.get("article_no", "")) for r in results}
        for r in itcl_results:
            key = (r.get("law_name", ""), r.get("article_no", ""))
            if key not in seen:
                results.append(r)
                seen.add(key)

    return {
        "law_articles": results[:10],
        "done_tools": ["search_law"],
    }


# ── 4. taxlaw prec Search (Chroma) ────────────────────────────────────────────

def taxlaw_prec_node(state: MultiAgentState) -> dict:
    query = state.get("prec_query") or state["query"]
    results = _chroma_prec(query, n=8)

    # 이전가격/국제조세 쿼리면 ITCL 전용 prec 추가 검색
    if _is_itcl_query(state["query"]):
        itcl_results = _chroma_prec(_ITCL_PREC_QUERY, n=5)
        seen = {r.get("case_no") or r.get("doc_id", "") for r in results}
        for r in itcl_results:
            key = r.get("case_no") or r.get("doc_id", "")
            if key and key not in seen:
                results.append(r)
                seen.add(key)

    return {
        "taxlaw_prec_results": results[:10],
        "done_tools": ["search_taxlaw_prec"],
    }


# ── 5. taxtr Search (Chroma) ──────────────────────────────────────────────────

def taxtr_node(state: MultiAgentState) -> dict:
    query = state.get("taxtr_query") or state["query"]
    results = _chroma_taxtr(query, n=6)
    return {
        "taxtr_results": results,
        "done_tools": ["search_taxtr"],
    }


# ── 6. Synthesizer ────────────────────────────────────────────────────────────

def synthesizer_node(state: MultiAgentState) -> dict:
    case_block = _fmt_cases(state.get("case_results") or [])
    pattern_block = _fmt_pattern(state.get("pattern_results") or {})
    law_block = _fmt_law_articles(state.get("law_articles") or [])
    prec_block = _fmt_chroma_prec(state.get("taxlaw_prec_results") or [])
    taxtr_block = _fmt_chroma_taxtr(state.get("taxtr_results") or [])

    has_law = bool(state.get("law_articles"))
    has_prec = bool(state.get("taxlaw_prec_results"))
    has_taxtr = bool(state.get("taxtr_results"))
    law_section = f"\n[세법 조문 검색 결과 (14개 세법)]\n{law_block}\n" if has_law else ""
    prec_section = f"\n[NTS 법원 판례 (taxlaw) 검색 결과]\n{prec_block}\n" if has_prec else ""
    taxtr_section = f"\n[조세심판원 재결례 검색 결과]\n{taxtr_block}\n" if has_taxtr else ""

    prompt = (
        "당신은 세법 전문 리서치 센터의 수석 분석관이다.\n"
        "아래 판례·재결례·법령 분석 결과를 종합해 통합 실무 보고서를 작성하라.\n\n"
        f"[분석 요청]\n{state['query']}\n\n"
        f"[Neo4j 판례 검색 결과]\n{case_block}\n\n"
        f"[승소/패소 패턴]\n{pattern_block}\n"
        f"{law_section}"
        f"{prec_section}"
        f"{taxtr_section}"
        "[보고서 구성 — 반드시 아래 순서]\n"
        "1. 핵심 요약 (2~3문장): 판례·재결례 흐름과 법령 구조를 한 번에 정리\n"
        "2. 관련 법령 조문 (2~4 bullet): 인용된 조문 번호·제목·적용 맥락\n"
        "3. 주요 판례·재결례 시사점 (3~6 bullet): 출처(법원/심판원)·결론·핵심 법리\n"
        "4. 승소 전략 포인트 (3~5 bullet): 패턴·조문 근거 포함 구체적 전략\n"
        "5. 리스크 경고 (2~3 bullet): 납세자·과세관청 각각의 리스크\n"
        "6. 실무 체크리스트 (3~5개): 즉시 행동 가능한 항목\n\n"
        "[엄격한 규칙]\n"
        "- 제공된 데이터에 없는 판례·조문·사실 생성 절대 금지\n"
        "- 구체적 조문 번호(제X조)와 판례/재결 번호 반드시 명시\n"
        "- 법원 판례(국승/국패)와 심판원 재결례를 구분해서 서술\n"
        "- 관련 없는 판례는 '유사 판례 미발견'으로 명시하고 생략\n"
        "- 판결문 문체 금지 → 실무자 보고서 톤"
    )

    result = _get_llm().invoke([HumanMessage(content=prompt)])
    return {"final_report": result.content}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_cases(cases: list) -> str:
    if not cases:
        return "(검색 결과 없음)"
    seen: set = set()
    lines: list = []
    for c in cases:
        cid = c.get("case_id") or c.get("case_number", "")
        if cid in seen or len(lines) >= 6:
            continue
        seen.add(cid)
        lines.append(
            f"• [{c.get('case_number', '')}] {c.get('court_name', '')} "
            f"{c.get('judgment_date', '')} → {c.get('conclusion', '')} | "
            f"쟁점: {str(c.get('issue', ''))[:60]}"
        )
    return "\n".join(lines) or "(없음)"


def _fmt_pattern(pattern: dict) -> str:
    if not pattern:
        return "(패턴 분석 없음)"
    cases = pattern.get("related_cases", [])
    win_keywords = {"인용", "납세자 승", "취소", "경정"}
    win = sum(
        1 for c in cases
        if any(kw in str(c.get("conclusion", "")) for kw in win_keywords)
    )
    statutes = pattern.get("statutes_cited", [])
    top_statutes = ", ".join(s["statute"] for s in statutes[:4]) if statutes else "없음"
    return (
        f"분석 판례 {len(cases)}건 | 납세자 유리 {win}건 / 과세관청 유리 {len(cases) - win}건\n"
        f"주요 인용 법령: {top_statutes}"
    )


def _fmt_law_articles(articles: list) -> str:
    if not articles:
        return "(관련 조문 없음)"
    lines = []
    for a in articles[:8]:
        law_name = a.get("law_name", "")
        scope = a.get("scope", "")
        art_no = a.get("article_no", "")
        title = a.get("title", "")
        domain = a.get("domain", "")
        doc = (a.get("document") or "")[:120]
        scope_label = {"LAW": "법", "DECREE": "시행령", "RULE": "시행규칙"}.get(scope, scope)
        lines.append(
            f"• {law_name} {scope_label} 제{art_no}조 {title}"
            + (f" [{domain}]" if domain else "")
            + (f"\n  {doc}" if doc else "")
        )
    return "\n".join(lines) or "(없음)"


def _fmt_chroma_prec(items: list) -> str:
    if not items:
        return "(결과 없음)"
    lines = []
    for it in items[:6]:
        case_no = it.get("case_no") or it.get("doc_id", "")
        decision = it.get("decision", "")
        tax_type = it.get("tax_type", "")
        title = it.get("title") or it.get("document", "")[:60]
        sim = it.get("similarity", 0)
        lines.append(f"• [{case_no}] {tax_type} | {decision} | 유사도:{sim:.2f} | {title}")
    return "\n".join(lines) or "(없음)"


def _fmt_chroma_taxtr(items: list) -> str:
    if not items:
        return "(결과 없음)"
    lines = []
    for it in items[:5]:
        case_no = it.get("case_no") or it.get("dem_no") or it.get("doc_id", "")
        decision = it.get("decision_type") or it.get("decision", "")
        title = it.get("title") or it.get("document", "")[:60]
        sim = it.get("similarity", 0)
        lines.append(f"• [{case_no}] {decision} | 유사도:{sim:.2f} | {title}")
    return "\n".join(lines) or "(없음)"


# ── Graph ─────────────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(MultiAgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("search_cases", case_search_node)
    g.add_node("search_law", law_search_node)
    g.add_node("search_taxlaw_prec", taxlaw_prec_node)
    g.add_node("search_taxtr", taxtr_node)
    g.add_node("synthesizer", synthesizer_node)

    g.set_entry_point("supervisor")
    g.add_conditional_edges(
        "supervisor",
        _route_supervisor,
        {
            "search_cases": "search_cases",
            "search_law": "search_law",
            "search_taxlaw_prec": "search_taxlaw_prec",
            "search_taxtr": "search_taxtr",
            "synthesizer": "synthesizer",
        },
    )
    g.add_edge("search_cases", "supervisor")
    g.add_edge("search_law", "supervisor")
    g.add_edge("search_taxlaw_prec", "supervisor")
    g.add_edge("search_taxtr", "supervisor")
    g.add_edge("synthesizer", END)
    return g.compile()


# ── Public API ────────────────────────────────────────────────────────────────

class SupervisorAgent:
    """
    판례 DB + 세법 조문 + NTS taxlaw 판례 + 조세심판 재결례를 결합한 멀티 에이전트.

    소스:
      - Neo4j LegalGraphSearch (국제조세 판례)
      - Chroma taxlaw_prec (NTS 법원 판례 32,628건)
      - Chroma taxtr_cases (조세심판원 재결례 2,463건)
      - Chroma law_articles (14개 세법 조문 6,687건)
    """

    def __init__(self):
        self.graph = _build_graph()

    def run(self, query: str) -> dict:
        initial: MultiAgentState = {
            "query": query,
            "plan": [],
            "done_tools": [],
            "iteration": 0,
            "law_query": None,
            "prec_query": None,
            "taxtr_query": None,
            "case_results": None,
            "pattern_results": None,
            "law_articles": None,
            "taxlaw_prec_results": None,
            "taxtr_results": None,
            "final_report": None,
        }
        final = self.graph.invoke(initial)

        return {
            "query": final["query"],
            "final_report": final["final_report"],
            "case_context": {
                "search_results": final.get("case_results") or [],
                "pattern_results": final.get("pattern_results") or {},
            },
            "taxlaw_prec_context": final.get("taxlaw_prec_results") or [],
            "taxtr_context": final.get("taxtr_results") or [],
            "law_articles_context": final.get("law_articles") or [],
            "tools_used": final.get("done_tools") or [],
        }
