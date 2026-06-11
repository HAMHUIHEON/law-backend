# agents/multi_agent.py
"""
SupervisorAgent — 판례 검색 + ITCL 법령 분석을 결합한 멀티 에이전트

구조 (LangGraph):
  START
    └─▶ supervisor  ─ 도구 선택 (search_cases / search_itcl_law /
                                  search_taxlaw_prec / search_taxtr / finish)
          ├─▶ case_search_node      ─ LegalGraphSearch (Neo4j 벡터 + 패턴)
          ├─▶ law_search_node       ─ ITCLSearch (SemanticIssue 벡터 + 조문)
          ├─▶ taxlaw_prec_node      ─ Chroma taxlaw_prec (NTS 법원 판례 32K)
          ├─▶ taxtr_node            ─ Chroma taxtr_cases (조세심판원 2,463건)
          └─▶ synthesizer           ─ 모든 결과를 합쳐 보고서 작성
  END
"""

import json
import os
from pathlib import Path
from typing import Annotated, List, Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph
from operator import add as list_add

from db.graph_search import LegalGraphSearch
from db.itcl_search import ITCLSearch

_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm

_CHROMA_DIR = Path(__file__).parent.parent.parent / "vector_db" / "chroma"
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")


# ── Chroma helpers ────────────────────────────────────────────────────────────

def _chroma_search(collection_name: str, query: str, n: int = 8) -> list:
    """Chroma 컬렉션에서 벡터 검색, 결과를 dict 리스트로 반환."""
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=_OPENAI_KEY,
            model_name="text-embedding-3-small",
        )
        col = client.get_collection(collection_name, embedding_function=ef)
        res = col.query(query_texts=[query], n_results=min(n, col.count()))
        items = []
        for i, doc_id in enumerate(res["ids"][0]):
            meta = (res["metadatas"][0][i] if res["metadatas"] else {}) or {}
            items.append({
                "doc_id": doc_id,
                "document": (res["documents"][0][i] if res["documents"] else "") or "",
                **meta,
            })
        return items
    except Exception as e:
        return [{"error": str(e)}]


# ── State ─────────────────────────────────────────────────────────────────────

class MultiAgentState(TypedDict):
    query: str

    # Supervisor 결정
    plan: List[str]
    done_tools: Annotated[List[str], list_add]
    iteration: int

    # 도구 결과 — Neo4j + ITCL
    case_results: Optional[list]
    pattern_results: Optional[dict]
    law_issues: Optional[list]
    law_articles: Optional[list]
    law_structure: Optional[dict]

    # 도구 결과 — Chroma
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
        "아래 질문을 분석하고 필요한 검색 도구를 JSON으로 반환하라.\n\n"
        f"질문: {state['query']}\n\n"
        "사용 가능한 도구:\n"
        "  - search_cases: Neo4j 국제조세 판례 DB 벡터 검색 + 승소 패턴 분석\n"
        "  - search_itcl_law: 국제조세조정법 조문 구조 + 쟁점 검색\n"
        "  - search_taxlaw_prec: NTS taxlaw 법원 판례 32,628건 (국승/국패 분류)\n"
        "  - search_taxtr: 조세심판원 재결례 2,463건\n\n"
        "반환 형식 (JSON만, 다른 텍스트 없음):\n"
        '{"tools": ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_itcl_law"]}\n\n'
        "규칙:\n"
        "- 판례·법원 결정·국승/국패 관련 → search_cases + search_taxlaw_prec 포함\n"
        "- 조세심판·재결례·이의신청 관련 → search_taxtr 포함\n"
        "- 조문·법령·규정 해석 관련 → search_itcl_law 포함\n"
        "- 일반 전략·쟁점 분석 → 4개 모두 포함\n"
        "- 최소 2개 이상 선택"
    )
    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    try:
        plan_data = json.loads(resp.content.strip())
        tools = plan_data.get("tools") or ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_itcl_law"]
    except Exception:
        tools = ["search_cases", "search_taxlaw_prec", "search_taxtr", "search_itcl_law"]

    return {"plan": tools, "iteration": 1}


def _route_supervisor(state: MultiAgentState) -> str:
    done = set(state.get("done_tools") or [])
    plan = state.get("plan") or []
    for tool in plan:
        if tool not in done:
            return tool
    return "synthesizer"


# ── 2. Case Search (Neo4j) ─────────────────────────────────────────────────────

def case_search_node(state: MultiAgentState) -> dict:
    s = LegalGraphSearch()
    try:
        results = s.search_similar_issues(state["query"], top_k=6)
        patterns = s.analyze_winning_patterns(state["query"], top_k=10)
    finally:
        s.close()
    return {
        "case_results": results,
        "pattern_results": patterns,
        "done_tools": ["search_cases"],
    }


# ── 3. ITCL Law Search ────────────────────────────────────────────────────────

def law_search_node(state: MultiAgentState) -> dict:
    s = ITCLSearch()
    try:
        issues = s.search_similar_issues(state["query"], top_k=6)
        articles = s.search_articles_by_topic(state["query"], top_k=5)
    finally:
        s.close()
    return {
        "law_issues": issues,
        "law_articles": articles,
        "done_tools": ["search_itcl_law"],
    }


# ── 4. taxlaw prec Search (Chroma) ────────────────────────────────────────────

def taxlaw_prec_node(state: MultiAgentState) -> dict:
    results = _chroma_search("taxlaw_prec", state["query"], n=8)
    return {
        "taxlaw_prec_results": results,
        "done_tools": ["search_taxlaw_prec"],
    }


# ── 5. taxtr Search (Chroma) ──────────────────────────────────────────────────

def taxtr_node(state: MultiAgentState) -> dict:
    results = _chroma_search("taxtr_cases", state["query"], n=6)
    return {
        "taxtr_results": results,
        "done_tools": ["search_taxtr"],
    }


# ── 6. Synthesizer ────────────────────────────────────────────────────────────

def synthesizer_node(state: MultiAgentState) -> dict:
    case_block = _fmt_cases(state.get("case_results") or [])
    pattern_block = _fmt_pattern(state.get("pattern_results") or {})
    law_block = _fmt_law_issues(state.get("law_issues") or [])
    article_block = _fmt_articles(state.get("law_articles") or [])
    prec_block = _fmt_chroma_prec(state.get("taxlaw_prec_results") or [])
    taxtr_block = _fmt_chroma_taxtr(state.get("taxtr_results") or [])

    has_prec = bool(state.get("taxlaw_prec_results"))
    has_taxtr = bool(state.get("taxtr_results"))
    prec_section = f"\n[NTS 법원 판례 (taxlaw) 검색 결과]\n{prec_block}\n" if has_prec else ""
    taxtr_section = f"\n[조세심판원 재결례 검색 결과]\n{taxtr_block}\n" if has_taxtr else ""

    prompt = (
        "당신은 세법 전문 리서치 센터의 수석 분석관이다.\n"
        "아래 판례·재결례·법령 분석 결과를 종합해 통합 실무 보고서를 작성하라.\n\n"
        f"[분석 요청]\n{state['query']}\n\n"
        f"[Neo4j 판례 검색 결과]\n{case_block}\n\n"
        f"[승소/패소 패턴]\n{pattern_block}\n"
        f"{prec_section}"
        f"{taxtr_section}"
        f"[ITCL 관련 쟁점 (법령 레이어)]\n{law_block}\n\n"
        f"[인용 조문]\n{article_block}\n\n"
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


def _fmt_law_issues(issues: list) -> str:
    if not issues:
        return "(ITCL 쟁점 없음)"
    lines = []
    for i in issues[:5]:
        sim = i.get("similarity", 0)
        lines.append(
            f"• [{i.get('issue_id', '')}] {i.get('issue_title', '')} "
            f"(유사도 {sim:.0%})\n  {str(i.get('issue_summary', ''))[:80]}"
        )
    return "\n".join(lines)


def _fmt_articles(articles: list) -> str:
    if not articles:
        return "(관련 조문 없음)"
    lines = []
    for a in articles[:8]:
        art_id = a.get("article_id", "")
        title = a.get("article_title") or ""
        scope = a.get("scope", "")
        related = a.get("related_issue", "")
        if art_id.startswith("ART_"):
            num = art_id[4:].replace("_", "의")
            display = f"제{num}조"
        else:
            display = art_id
        lines.append(f"• {scope} {display} {title}  ← {related}")
    return "\n".join(lines)


def _fmt_chroma_prec(items: list) -> str:
    if not items or (len(items) == 1 and "error" in items[0]):
        err = items[0].get("error", "") if items else ""
        return f"(NTS 판례 검색 불가: {err})" if err else "(결과 없음)"
    lines = []
    for it in items[:6]:
        case_no = it.get("case_no") or it.get("doc_id", "")
        decision = it.get("decision", "")
        tax_type = it.get("tax_type", "")
        title = it.get("title") or it.get("document", "")[:60]
        lines.append(f"• [{case_no}] {tax_type} | {decision} | {title}")
    return "\n".join(lines) or "(없음)"


def _fmt_chroma_taxtr(items: list) -> str:
    if not items or (len(items) == 1 and "error" in items[0]):
        err = items[0].get("error", "") if items else ""
        return f"(조세심판 검색 불가: {err})" if err else "(결과 없음)"
    lines = []
    for it in items[:5]:
        dem_no = it.get("dem_no") or it.get("doc_id", "")
        decision = it.get("decision_type") or it.get("decision", "")
        title = it.get("title") or it.get("document", "")[:60]
        lines.append(f"• [{dem_no}] {decision} | {title}")
    return "\n".join(lines) or "(없음)"


# ── Graph ─────────────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(MultiAgentState)
    g.add_node("supervisor", supervisor_node)
    g.add_node("search_cases", case_search_node)
    g.add_node("search_itcl_law", law_search_node)
    g.add_node("search_taxlaw_prec", taxlaw_prec_node)
    g.add_node("search_taxtr", taxtr_node)
    g.add_node("synthesizer", synthesizer_node)

    g.set_entry_point("supervisor")
    g.add_conditional_edges(
        "supervisor",
        _route_supervisor,
        {
            "search_cases": "search_cases",
            "search_itcl_law": "search_itcl_law",
            "search_taxlaw_prec": "search_taxlaw_prec",
            "search_taxtr": "search_taxtr",
            "synthesizer": "synthesizer",
        },
    )
    g.add_edge("search_cases", "supervisor")
    g.add_edge("search_itcl_law", "supervisor")
    g.add_edge("search_taxlaw_prec", "supervisor")
    g.add_edge("search_taxtr", "supervisor")
    g.add_edge("synthesizer", END)
    return g.compile()


# ── Public API ────────────────────────────────────────────────────────────────

class SupervisorAgent:
    """
    판례 DB + ITCL 법령 레이어 + NTS taxlaw 판례 + 조세심판 재결례를 결합한 멀티 에이전트.

    소스:
      - Neo4j LegalGraphSearch (국제조세 판례)
      - Chroma taxlaw_prec (NTS 법원 판례 32,628건)
      - Chroma taxtr_cases (조세심판원 재결례 2,463건)
      - ITCL SemanticIssue + Article

    Usage:
        agent = SupervisorAgent()
        result = agent.run("이전가격 정상가격 산정 방법론 분쟁에서 납세자 유리 전략은?")
    """

    def __init__(self):
        self.graph = _build_graph()

    def run(self, query: str) -> dict:
        initial: MultiAgentState = {
            "query": query,
            "plan": [],
            "done_tools": [],
            "iteration": 0,
            "case_results": None,
            "pattern_results": None,
            "law_issues": None,
            "law_articles": None,
            "law_structure": None,
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
            "law_context": {
                "related_issues": final.get("law_issues") or [],
                "articles": final.get("law_articles") or [],
            },
            "tools_used": final.get("done_tools") or [],
        }
