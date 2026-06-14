# agents/insight_agent.py
#
# Plan → Execute → Reflect → Report
#
# 인간 전문가(변호사/세무사)의 사고 흐름을 그대로 구현:
#   1. Planner   — 질문 분해, 검색 쿼리 + 관련 법령 추출
#   2. Executor  — 단일 Neo4j 연결로 검색·패턴분석·법령조회 일괄 실행
#   3. Insight   — case_id 제공 시 원문 기반 ExportC deep analysis
#   4. Critic    — 결과 충분성 평가, 미달이면 Executor 재실행 (최대 1회)
#   5. Reporter  — 수집된 증거 전체를 실무 보고서로 종합
#
# 기존 multi_agent.py 대비 개선점:
#   - Planner 추가: 쿼리를 맹목적으로 넘기지 않고 검색 전략을 먼저 수립
#   - 연결 재사용: LegalGraphSearch를 매 tool 호출마다 열고 닫지 않음
#   - Critic loop: 결과가 부족하면 검색어를 확장해 재시도
#   - 컨텍스트 연결: Reporter가 모든 이전 결과를 구조화된 형태로 수신
#   - 보고서 품질: 판례번호·결론 등 구체적 근거 포함 강제

import json
import os
from typing import List, Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph

from agents.conversation import build_context_query, make_history_section
from bravo.models_bravo import IssueLogic
from db.graph_search import LegalGraphSearch
from export.export_chain import ExportCChain
from export.models_export import ExportCInput
try:
    from utils.cache import load_cache
except ImportError:
    def load_cache(case_id, filename):  # noqa: E302
        return None

def _chroma_law_search(query: str, n: int = 6) -> list:
    """Chroma law_articles에서 관련 조문 검색. chroma_search.py의 공유 클라이언트 사용."""
    from db.chroma_search import search_law_articles
    return search_law_articles(query, n=n)

_llm = None

def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm

_MAX_RETRIES = 2
_SIMILARITY_THRESHOLD = 0.60   # 전체 결과의 최고 유사도가 이 미만이면 품질 낮음으로 판정
_MIN_RESULTS_COUNT = 3          # 결과 N건 미만이면 재시도


# ── State ─────────────────────────────────────────────────────────────────────

class InsightState(TypedDict):
    query: str
    case_id: Optional[str]
    messages: list   # 이전 대화 [{role, content}]

    # Planner 출력
    search_queries: List[str]   # 분해된 쟁점 검색어
    statute_names: List[str]    # 조회할 법령명

    # Executor 출력
    search_results: Optional[list]
    pattern_results: Optional[dict]
    statute_results: Optional[list]    # 유지 (Neo4j graph 결과, 있으면 추가)
    law_articles: Optional[list]       # Chroma law_articles 조문 검색 결과

    # Insight (case_id 있을 때)
    insight_result: Optional[dict]

    # Critic
    reflection: Optional[str]   # "sufficient" | "retry"
    retry_count: int

    # Reporter
    final_report: Optional[str]


# ── 1. Planner ────────────────────────────────────────────────────────────────

def planner_node(state: InsightState) -> InsightState:
    """
    질문을 분석해 판례 DB에 적합한 검색 쿼리와 법령명을 추출.
    예) "국제조세 특수관계자 거래 분석"
      → search_queries: ["특수관계자간 자산 저가양도 부당행위계산 부인", "이전가격 정상가격 산정 방법"]
      → statute_names: ["국제조세조정에 관한 법률", "법인세법"]
    """
    prompt = (
        "당신은 조세법률 전문 리서치 플래너다.\n"
        "아래 질문을 분석해 판례 검색 전략을 JSON으로만 반환하라.\n\n"
        f"질문: {state['query']}\n\n"
        "반환 형식 (JSON만, 다른 텍스트 없음):\n"
        '{"search_queries": ["법적 쟁점 형태의 검색어 1~3개"], '
        '"statute_names": ["정확한 법령명 0~2개"]}\n\n'
        "규칙:\n"
        "- search_queries: 판례 쟁점 DB 검색에 최적화된 형태 "
        "(예: '특수관계자간 자산 저가양도가 부당행위계산 부인 대상인지')\n"
        "- statute_names: 법령의 공식 명칭만 (예: '국세기본법', '법인세법')\n"
        "- 법령 언급이 없으면 statute_names는 빈 배열"
    )
    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    try:
        plan = json.loads(resp.content.strip())
    except Exception:
        plan = {}

    return {
        **state,
        "search_queries": plan.get("search_queries") or [state["query"]],
        "statute_names": plan.get("statute_names") or [],
    }


# ── 2. Executor ───────────────────────────────────────────────────────────────

def executor_node(state: InsightState) -> InsightState:
    """
    Neo4j + Chroma 병렬 조회:
      - search_similar_issues: 쟁점별 유사 판례 (Neo4j 벡터)
      - analyze_winning_patterns: 승소/패소 패턴 (Neo4j 하이브리드)
      - law_articles: 관련 조문 텍스트 (Chroma law_articles — 14개 세법 6,687조문)
        statute_names가 있으면 법령명+쿼리 조합, 없으면 primary_query만 사용
    """
    queries = state["search_queries"] or [state["query"]]
    # 이전 대화 맥락으로 검색 쿼리 보강
    primary_query = build_context_query(queries[0], state.get("messages") or [])

    # ① Neo4j 판례 검색 (유사 쟁점 + 패턴)
    s = LegalGraphSearch()
    try:
        all_search: list = []
        for q in queries:
            all_search.extend(s.search_similar_issues(q, top_k=5))
        pattern = s.analyze_winning_patterns(primary_query, top_k=10)
    finally:
        s.close()

    # ② Chroma 법령 조문 검색 (statute_names 기반 or primary_query)
    law_query = " ".join(state["statute_names"]) + " " + primary_query if state["statute_names"] else primary_query
    law_arts = _chroma_law_search(law_query, n=6)

    return {
        **state,
        "search_results": all_search,
        "pattern_results": pattern,
        "statute_results": [],   # Neo4j get_statute_cases 제거 (Chroma로 대체)
        "law_articles": law_arts,
    }


# ── 3. Insight ────────────────────────────────────────────────────────────────

def insight_node(state: InsightState) -> InsightState:
    """case_id가 있을 때 캐시된 원문 기반 ExportC 수준 deep insight 생성."""
    case_id = state.get("case_id")
    if not case_id:
        return {**state, "insight_result": None}

    issue_logic_raw = load_cache(case_id, "issue_logic.json") or {}
    paragraphs_raw = load_cache(case_id, "paragraphs.json") or []
    chains = issue_logic_raw.get("issue_logic_chains", []) if isinstance(issue_logic_raw, dict) else issue_logic_raw
    issue_logic_list = [IssueLogic(**item) for item in chains]
    block_texts = [
        (p.get("text", "") if isinstance(p, dict) else str(p))
        for p in paragraphs_raw
        if (p.get("text") if isinstance(p, dict) else p)
    ]

    output = ExportCChain(model=DEFAULT_MODEL).run(
        ExportCInput(issue_logic_list=issue_logic_list, block_texts=block_texts)
    )
    return {**state, "insight_result": output.model_dump()}


# ── 4. Critic ─────────────────────────────────────────────────────────────────

def _assess_result_quality(results: list, query: str) -> tuple[bool, str]:
    """
    검색 결과 품질 평가.
    Returns (should_retry: bool, reason: str)
    """
    # 1. 결과 건수 부족
    if len(results) < _MIN_RESULTS_COUNT:
        return True, f"결과 {len(results)}건 — 최소 {_MIN_RESULTS_COUNT}건 미달"

    # 2. 유사도 임계값 — 최고 유사도가 낮으면 관련성 없는 판례만 검색된 것
    scores = [r.get("similarity", 0) for r in results if isinstance(r, dict)]
    if scores:
        max_sim = max(scores)
        if max_sim < _SIMILARITY_THRESHOLD:
            return True, f"최고 유사도 {max_sim:.3f} < 임계값 {_SIMILARITY_THRESHOLD} — 관련 판례 미검색 의심"

    # 3. LLM 관련성 판단 (유사도 경계값 영역: threshold ≤ max_sim < threshold+0.1)
    if scores and _SIMILARITY_THRESHOLD <= max(scores) < _SIMILARITY_THRESHOLD + 0.10:
        result_preview = "\n".join(
            f"- {r.get('case_number', '')} | 쟁점: {str(r.get('issue', ''))[:60]}"
            for r in results[:5]
            if isinstance(r, dict)
        )
        prompt = (
            "아래 판례 검색 결과가 질문과 실질적으로 관련 있는지 평가하라.\n\n"
            f"[질문] {query}\n\n"
            f"[검색된 판례 목록]\n{result_preview}\n\n"
            "판단 기준:\n"
            "- RELEVANT: 판례가 질문의 법적 쟁점을 직접 다루고 있음\n"
            "- IRRELEVANT: 판례가 질문과 다른 법적 쟁점에 관한 것\n\n"
            'JSON으로만 반환: {"verdict": "RELEVANT" | "IRRELEVANT", "reason": "한 문장"}'
        )
        try:
            resp = _get_llm().invoke([HumanMessage(content=prompt)])
            judgment = json.loads(resp.content.strip().removeprefix("```json").removesuffix("```").strip())
            if judgment.get("verdict") == "IRRELEVANT":
                return True, f"LLM 관련성 판단: IRRELEVANT — {judgment.get('reason', '')}"
        except Exception:
            pass

    return False, "sufficient"


def critic_node(state: InsightState) -> InsightState:
    """
    결과 품질 3단계 평가:
    1. 건수 부족 (< _MIN_RESULTS_COUNT)
    2. 유사도 임계값 미달 (최고 similarity < _SIMILARITY_THRESHOLD)
    3. LLM 관련성 판단 (경계값 영역 — similarity 0.60~0.70)

    재시도 시 원본 질문 + 대안 검색어 추가.
    _MAX_RETRIES 초과 시 부족한 채로 보고서 단계로 진행.
    """
    results = state.get("search_results") or []
    can_retry = state["retry_count"] < _MAX_RETRIES

    should_retry, reason = _assess_result_quality(results, state["query"])
    do_retry = should_retry and can_retry

    if do_retry:
        # 검색어 다각화: 원본 질문 + 더 포괄적인 fallback 쿼리
        extended_queries = list(dict.fromkeys(
            state["search_queries"]
            + [state["query"]]
            + [f"{q} 과세관청 처분 납세자 불복" for q in state["search_queries"][:1]]
        ))
    else:
        extended_queries = state["search_queries"]

    return {
        **state,
        "reflection": "retry" if do_retry else "sufficient",
        "retry_count": state["retry_count"] + 1,
        "search_queries": extended_queries,
    }


def _route_after_critic(state: InsightState) -> str:
    return "executor" if state["reflection"] == "retry" else "report"


# ── 5. Reporter ───────────────────────────────────────────────────────────────

def reporter_node(state: InsightState) -> InsightState:
    """
    수집된 모든 데이터를 구조화된 형태로 정제한 뒤 실무 보고서 생성.
    Reporter는 원시 JSON을 직접 받지 않고 포맷팅된 텍스트를 받는다.
    """
    search_block = _fmt_cases(state.get("search_results") or [])
    pattern_block = _fmt_pattern(state.get("pattern_results") or {})
    law_block = _fmt_law_articles(state.get("law_articles") or [])

    insight_block = ""
    if state.get("insight_result"):
        es = state["insight_result"].get("executive_summary", {})
        rv = es.get("risk_view", {})
        jl = es.get("judicial_logic", {})
        insight_block = (
            "\n[원문 판례 Deep Insight]\n"
            f"• 핵심 요지: {es.get('one_liner', '')}\n"
            f"• 핵심 쟁점: {', '.join(es.get('core_issues', []))}\n"
            f"• 법원 논거: {jl.get('how_the_court_thought', '')}\n"
            f"• 납세자 리스크: {rv.get('taxpayer_risk', '')}\n"
            f"• 판례 시그널: {rv.get('precedent_signal', '')}"
        )

    law_section = f"\n[관련 세법 조문]\n{law_block}" if law_block else ""

    hist_section = make_history_section(state.get("messages") or [])
    prompt = (
        "당신은 조세법률 전문 리서치 센터의 수석 분석관이다.\n"
        "아래 분석 결과를 바탕으로 변호사·세무팀이 즉시 활용할 수 있는 통합 보고서를 작성하라."
        + hist_section + "\n\n"
        f"[분석 요청]\n{state['query']}\n\n"
        f"[유사 판례 검색 결과]\n{search_block}\n\n"
        f"[승소/패소 패턴 분석]\n{pattern_block}"
        f"{law_section}"
        f"{insight_block}\n\n"
        "[보고서 구성 — 반드시 아래 순서]\n"
        "1. 핵심 요약 (2~3문장): 판례군 전체를 관통하는 법리 방향성\n"
        "2. 관련 세법 조문 (2~3 bullet): 조문 번호·내용 요약, 판례와 연결\n"
        "3. 주요 판례 시사점 (3~5 bullet): 판례번호·법원명·판결 결론 반드시 포함\n"
        "4. 승소 전략 포인트 (3~5 bullet): 패턴에서 도출한 구체적 전략, 수치/법령 근거 포함\n"
        "5. 리스크 경고 (2~3 bullet): 납세자·과세관청 각각의 취약점과 리스크 현실화 조건\n"
        "6. 실무 체크리스트 (3~5개): 지금 당장 행동 가능한 항목\n\n"
        "[엄격한 규칙]\n"
        "- 분석 데이터에 없는 판례·법령·사실 생성 절대 금지\n"
        "- '관련 법령을 검토해야 한다' 같은 추상적 조언 금지 → 구체적 조문·판례번호 명시\n"
        "- 판결문 문체 금지 → 실무자 보고서 톤 (간결·직접적)\n"
        "- 각 bullet은 근거 데이터가 없으면 작성하지 말 것"
    )

    result = _get_llm().invoke([HumanMessage(content=prompt)])
    return {**state, "final_report": result.content}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_cases(cases: list) -> str:
    if not cases:
        return "(검색 결과 없음)"
    seen: set = set()
    lines: list = []
    for c in cases:
        cid = c.get("case_id") or c.get("case_number", "")
        if cid in seen or len(lines) >= 8:
            continue
        seen.add(cid)
        issue_preview = str(c.get("issue", ""))[:60]
        lines.append(
            f"• [{c.get('case_number', '')}] {c.get('court_name', '')} "
            f"{c.get('judgment_date', '')} → {c.get('conclusion', '')} | "
            f"쟁점: {issue_preview}"
        )
    return "\n".join(lines)


def _fmt_pattern(pattern: dict) -> str:
    if not pattern:
        return "(패턴 분석 없음)"
    cases = pattern.get("related_cases", [])
    statutes = pattern.get("statutes_cited", [])
    win_keywords = {"인용", "납세자 승", "취소", "경정"}
    win = sum(
        1 for c in cases
        if any(kw in str(c.get("conclusion", "")) for kw in win_keywords)
    )
    top_statutes = (
        ", ".join(s["statute"] for s in statutes[:5]) if statutes else "없음"
    )
    return (
        f"분석 판례: {len(cases)}건 | "
        f"납세자 유리: {win}건 / 과세관청 유리: {len(cases) - win}건\n"
        f"주요 인용 법령: {top_statutes}"
    )


def _fmt_law_articles(articles: list) -> str:
    if not articles:
        return ""
    lines = []
    for a in articles[:6]:
        scope_label = {"law": "법", "decree": "시행령", "rule": "시행규칙"}.get(a.get("scope", ""), a.get("scope", ""))
        preview = str(a.get("document", ""))[:120].replace("\n", " ")
        lines.append(
            f"• {a.get('law_name', '')}({scope_label}) {a.get('article_no', '')} {a.get('title', '')} — {preview}"
        )
    return "\n".join(lines)


# ── Graph ─────────────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(InsightState)
    g.add_node("planner", planner_node)
    g.add_node("executor", executor_node)
    g.add_node("insight", insight_node)
    g.add_node("critic", critic_node)
    g.add_node("report", reporter_node)

    g.set_entry_point("planner")
    g.add_edge("planner", "executor")
    g.add_edge("executor", "insight")
    g.add_edge("insight", "critic")
    g.add_conditional_edges(
        "critic",
        _route_after_critic,
        {"executor": "executor", "report": "report"},
    )
    g.add_edge("report", END)
    return g.compile()


# ── Public API ────────────────────────────────────────────────────────────────

class InsightAgent:
    """
    Plan-Execute-Reflect-Report 패턴의 법률 인사이트 에이전트.

    Usage:
        agent = InsightAgent()

        # 검색 + 패턴 분석
        result = agent.run("국제조세조정법 특수관계자 거래 판례 분석해줘")

        # 검색 + 패턴 + ExportC 수준 deep insight
        result = agent.run("특수관계자 거래 리스크", case_id="2023누1234")

        result["final_report"]  # 최종 보고서 (str)
        result["insight"]       # ExportC deep insight (dict, case_id 제공 시)
        result["steps"]         # 실행 단계 목록
    """

    def __init__(self):
        self.graph = _build_graph()

    def run(self, query: str, case_id: Optional[str] = None, messages: list = []) -> dict:
        initial: InsightState = {
            "query": query,
            "case_id": case_id,
            "messages": messages,
            "search_queries": [],
            "statute_names": [],
            "search_results": None,
            "pattern_results": None,
            "statute_results": None,
            "law_articles": None,
            "insight_result": None,
            "reflection": None,
            "retry_count": 0,
            "final_report": None,
        }
        final = self.graph.invoke(initial)

        steps = ["planned", "executed"]
        if final.get("insight_result"):
            steps.append("deep_insight")
        steps.append(f"critic:{final.get('reflection', 'sufficient')}")
        if final.get("retry_count", 1) > 1:
            steps.append(f"retried({final['retry_count'] - 1}x)")
        steps.append("reported")

        return {
            "query": final["query"],
            "final_report": final["final_report"],
            "insight": final["insight_result"],
            "law_articles_context": final.get("law_articles") or [],
            "steps": steps,
        }
