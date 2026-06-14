# agents/rebuttal_agent.py
"""
RebuttalAgent — 반론 초안 생성 에이전트

과세처분 이유서를 입력하면 납세자 승소 판례·재결례에서 반론 논거를 추출해
이의신청서·심판청구서 초안을 생성합니다.

데이터 소스:
  - Chroma taxlaw_prec: 법원 판례 32,628건 (납세자 승소 필터)
  - Chroma taxtr_cases: 조세심판 재결례 2,463건 (인용 필터)
  - Chroma law_articles: 세법 조문 6,687건

LangGraph: ClaimExtractor → CaseSearcher → DraftWriter → Reflector
"""

import json
from datetime import datetime, timedelta
from typing import Optional, TypedDict

from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL
from langgraph.graph import END, StateGraph
from agents.conversation import build_context_query, make_history_section

_llm = None
_MAX_REFLECT = 2  # 반성 사이클 1→2회로 상향


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm


# 불복 기한 계산 (국세기본법 기준)
def _compute_deadlines(disposition_date: str) -> dict:
    try:
        base = datetime.strptime(disposition_date, "%Y-%m-%d")
        return {
            "이의신청_마감": (base + timedelta(days=90)).strftime("%Y-%m-%d"),
            "심판청구_마감": (base + timedelta(days=90)).strftime("%Y-%m-%d"),
            "행정소송_마감": "심판청구 결정 통지일로부터 90일 (행정소송법 제20조)",
            "기준": f"처분 통지일 {disposition_date} 기준",
        }
    except Exception:
        return {}


FILING_TYPE_LABELS = {
    "이의신청": "이  의  신  청  서",
    "심판청구": "심  판  청  구  서",
    "행정소송": "행 정 소 송 소 장",
}

FILING_TYPE_AGENCY = {
    "이의신청": "처분청 (세무서장 / 지방국세청장)",
    "심판청구": "조세심판원장",
    "행정소송": "관할 행정법원",
}


class RebuttalState(TypedDict):
    messages: list          # 이전 대화 [{role, content}]
    # 기본 입력
    disposition_text: str
    filing_type: str       # "이의신청" | "심판청구" | "행정소송"
    taxpayer_name: str
    taxpayer_id: str       # 주민/사업자번호
    tax_office: str        # 처분청
    disposition_date: str  # "YYYY-MM-DD"
    tax_amount: str        # "OOO원" (문자열로 받음)
    tax_type: str          # "법인세", "소득세" 등

    tax_claims: list
    key_issues: list
    deadlines: dict

    winning_court_cases: Optional[list]
    favorable_taxtr_cases: Optional[list]
    law_articles: Optional[list]

    draft: Optional[str]
    reflect_count: int
    final_report: Optional[str]
    unverified_citations: list


# ── 1. ClaimExtractor ─────────────────────────────────────────────────────────

def claim_extractor_node(state: RebuttalState) -> dict:
    prompt = (
        "당신은 조세 전문 변호사다. 아래 과세처분 이유서에서 과세관청의 주장을 분석하라.\n\n"
        f"[과세처분 이유서]\n{state['disposition_text']}\n\n"
        "반환 형식 (JSON만):\n"
        '{"tax_claims": ["과세관청 주장 1", "주장 2"], '
        '"key_issues": ["반론해야 할 핵심 쟁점 1 (법적 쟁점 형태)", "쟁점 2"]}'
    )
    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    try:
        data = json.loads(resp.content.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        data = {
            "tax_claims": [state["disposition_text"][:200]],
            "key_issues": [state["disposition_text"][:200]],
        }

    deadlines = _compute_deadlines(state.get("disposition_date", ""))

    return {
        "tax_claims": data.get("tax_claims") or [],
        "key_issues": data.get("key_issues") or [],
        "deadlines": deadlines,
    }


# ── 2. CaseSearcher ───────────────────────────────────────────────────────────

def case_searcher_node(state: RebuttalState) -> dict:
    from db.chroma_search import search_taxlaw_prec, search_taxtr_cases, search_law_articles

    # 쟁점 + 세목 조합 쿼리
    combined = " ".join(state["key_issues"]) + " " + state.get("tax_type", "")

    winning_court = search_taxlaw_prec(combined, n=10, filter_winning=True)
    if len(winning_court) < 3:
        winning_court = search_taxlaw_prec(combined, n=10)

    favorable_taxtr = search_taxtr_cases(combined, n=6, filter_favorable=True)
    if len(favorable_taxtr) < 2:
        favorable_taxtr = search_taxtr_cases(combined, n=6)

    law_articles = search_law_articles(combined, n=5)

    return {
        "winning_court_cases": winning_court,
        "favorable_taxtr_cases": favorable_taxtr,
        "law_articles": law_articles,
    }


# ── 3. DraftWriter ────────────────────────────────────────────────────────────

def draft_writer_node(state: RebuttalState) -> dict:
    court_str = json.dumps(state.get("winning_court_cases") or [], ensure_ascii=False, indent=2)
    taxtr_str = json.dumps(state.get("favorable_taxtr_cases") or [], ensure_ascii=False, indent=2)
    law_str = json.dumps(state.get("law_articles") or [], ensure_ascii=False, indent=2)
    claims_str = "\n".join(f"- {c}" for c in state.get("tax_claims") or [])
    issues_str = "\n".join(f"- {i}" for i in state.get("key_issues") or [])
    deadlines = state.get("deadlines") or {}
    deadline_str = "\n".join(f"- {k}: {v}" for k, v in deadlines.items()) if deadlines else "처분일 미입력 — 기한 계산 불가"

    filing_type = state.get("filing_type") or "심판청구"
    filing_label = FILING_TYPE_LABELS.get(filing_type, filing_type)
    filing_agency = FILING_TYPE_AGENCY.get(filing_type, "")
    header = _build_header(state, filing_label, filing_agency, deadlines)

    hist_section = make_history_section(state.get("messages") or [])
    prompt = (
        "당신은 조세심판원 심판관 경력의 조세전문 변호사다.\n"
        "아래 정보를 바탕으로 실제 제출 가능한 수준의 반론 초안을 작성하라."
        + hist_section + "\n\n"
        f"[청구 유형] {filing_type}\n\n"
        f"[불복 기한]\n{deadline_str}\n\n"
        f"[과세관청 주요 주장]\n{claims_str}\n\n"
        f"[반론해야 할 핵심 쟁점]\n{issues_str}\n\n"
        f"[참고 법원 판례 (납세자 승소 중심) — 검색된 데이터만 인용할 것]\n{court_str}\n\n"
        f"[참고 조세심판 재결례 (인용 중심) — 검색된 데이터만 인용할 것]\n{taxtr_str}\n\n"
        f"[관련 세법 조문 — 조문 번호는 아래 목록에서만 인용]\n{law_str}\n\n"
        "다음 구조로 반론 본문을 작성하라 (헤더는 별도 작성됨):\n\n"
        "## 1. 처분의 내용\n"
        "  처분의 경위, 과세표준, 세액 간략 서술\n\n"
        "## 2. 이 사건의 쟁점\n"
        "  각 쟁점을 번호로 구분하여 명확히 제시\n\n"
        "## 3. 청구 이유\n\n"
        "  ### 가. 처분의 위법성 개요\n\n"
        "  ### 나. 쟁점별 상세 반론\n"
        "  각 쟁점마다:\n"
        "    ① 과세관청 주장\n"
        "    ② 납세자 반론 (법령 조문 번호 명시 필수)\n"
        "    ③ 관련 판례·재결례 (번호 명시 — 위 검색 데이터에 있는 번호만 인용)\n"
        "    ④ 소결\n\n"
        "  ### 다. 관련 법령 근거\n"
        "  [조문 목록에서 인용, 원문 일부 발췌]\n\n"
        "## 4. 결론 및 청구취지\n"
        f'  "이 사건 과세처분은 위법하므로 취소되어야 합니다."\n\n'
        "[절대 규칙]\n"
        "- 위 검색 데이터에 없는 판례·조문 번호 생성 절대 금지\n"
        "- 판례 인용 시 반드시 case_no 필드의 번호 그대로 사용\n"
        "- 조문 인용 시 검색 결과의 law_name + article_no 조합만 사용\n"
        "- 불확실한 사실관계는 '이 건 처분 경위에 따르면'으로 처리"
    )

    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    full_draft = header + "\n\n" + resp.content
    return {"draft": full_draft}


def _build_header(state: RebuttalState, filing_label: str, filing_agency: str, deadlines: dict) -> str:
    taxpayer_name = state.get("taxpayer_name") or "청구인"
    taxpayer_id = state.get("taxpayer_id") or ""
    tax_office = state.get("tax_office") or "처분청"
    disposition_date = state.get("disposition_date") or ""
    tax_amount = state.get("tax_amount") or ""
    tax_type = state.get("tax_type") or ""
    deadline_note = deadlines.get("심판청구_마감") or deadlines.get("이의신청_마감") or ""

    lines = [
        f"{'═' * 50}",
        f"       {filing_label}",
        f"{'═' * 50}",
        "",
        f"청 구 인:  {taxpayer_name}" + (f"  ({taxpayer_id})" if taxpayer_id else ""),
        f"처 분 청:  {tax_office}",
    ]
    if filing_agency:
        lines.append(f"제출기관:  {filing_agency}")
    if tax_type:
        lines.append(f"세    목:  {tax_type}")
    if disposition_date:
        lines.append(f"처 분 일:  {disposition_date}")
    if tax_amount:
        lines.append(f"처 분 액:  {tax_amount}")
    if deadline_note:
        lines.append(f"청구기한:  {deadline_note} (이 날까지 제출 필요)")
    lines += ["", "─" * 50, ""]
    return "\n".join(lines)


# ── 4. Reflector ──────────────────────────────────────────────────────────────

def reflector_node(state: RebuttalState) -> dict:
    prompt = (
        "당신은 조세심판원 수석 심판관이다. 아래 반론 초안을 엄격하게 검토하라.\n\n"
        f"[반론 초안]\n{state['draft']}\n\n"
        "평가 기준 (각 항목 1~5점):\n"
        "1. 과세관청 주장을 각 쟁점별로 정확히 반박하는가\n"
        "2. 판례·재결례 번호가 구체적이고 본문에서 사실관계와 연결되는가\n"
        "3. 법령 조문 번호와 조문 내용이 정확하게 인용되는가\n"
        "4. '청구취지'가 명확하게 기재되는가 (취소 대상 처분 특정)\n"
        "5. 논리 흐름이 심판관이 읽기에 자연스러운가\n\n"
        "반환 형식 (JSON만):\n"
        '{"score": 총점(5~25), "verdict": "pass" 또는 "revise", '
        '"missing": ["빠진 항목 1", "빠진 항목 2"], '
        '"feedback": "개선 필요 사항 (구체적으로 3~5문장)"}'
    )
    resp = _get_llm().invoke([HumanMessage(content=prompt)])
    try:
        data = json.loads(resp.content.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        data = {"score": 18, "verdict": "pass", "missing": [], "feedback": ""}

    if data.get("verdict") == "pass" or state["reflect_count"] >= _MAX_REFLECT:
        # Citation Guard 적용
        from utils.citation_guard import apply_citation_guard
        guarded, unverified = apply_citation_guard(
            state["draft"],
            state.get("winning_court_cases") or [],
            state.get("favorable_taxtr_cases") or [],
        )
        return {
            "final_report": guarded,
            "unverified_citations": unverified,
            "reflect_count": state["reflect_count"],
        }

    feedback = data.get("feedback", "")
    missing = "\n".join(f"- {m}" for m in data.get("missing") or [])
    refine_prompt = (
        f"아래 피드백과 누락 항목을 반영해 반론 초안을 개선하라.\n\n"
        f"[누락 항목]\n{missing}\n\n"
        f"[피드백]\n{feedback}\n\n"
        f"[기존 초안]\n{state['draft']}\n\n"
        "[규칙] 검색 데이터에 없는 판례·조문 번호 추가 금지."
    )
    refined = _get_llm().invoke([HumanMessage(content=refine_prompt)])
    return {
        "draft": refined.content,
        "final_report": refined.content,
        "reflect_count": state["reflect_count"] + 1,
        "unverified_citations": [],
    }


def _route_reflector(state: RebuttalState) -> str:
    if state.get("final_report"):
        return END
    return "reflector"


# ── Graph ─────────────────────────────────────────────────────────────────────

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(RebuttalState)
        g.add_node("claim_extractor", claim_extractor_node)
        g.add_node("case_searcher", case_searcher_node)
        g.add_node("draft_writer", draft_writer_node)
        g.add_node("reflector", reflector_node)
        g.set_entry_point("claim_extractor")
        g.add_edge("claim_extractor", "case_searcher")
        g.add_edge("case_searcher", "draft_writer")
        g.add_edge("draft_writer", "reflector")
        g.add_conditional_edges("reflector", _route_reflector)
        _graph = g.compile()
    return _graph


class RebuttalAgent:
    """
    과세처분 이유서 → 반론 초안 (이의신청서/심판청구서)

    result = agent.run(
        disposition_text="과세처분 이유서...",
        filing_type="심판청구",          # "이의신청" | "심판청구" | "행정소송"
        taxpayer_name="홍길동",
        taxpayer_id="123-45-67890",
        tax_office="서울지방국세청",
        disposition_date="2025-03-15",   # "YYYY-MM-DD"
        tax_amount="500,000,000원",
        tax_type="법인세",
    )
    result["final_report"]           # 최종 반론 초안 (헤더 포함)
    result["winning_court_cases"]    # 참고한 납세자 승소 판례
    result["favorable_taxtr_cases"]  # 참고한 인용 재결례
    result["law_articles"]           # 관련 조문
    result["unverified_citations"]   # 검증 안 된 판례 번호 목록
    result["deadlines"]              # 불복 기한
    """

    def run(
        self,
        disposition_text: str,
        filing_type: str = "심판청구",
        taxpayer_name: str = "",
        taxpayer_id: str = "",
        tax_office: str = "",
        disposition_date: str = "",
        tax_amount: str = "",
        tax_type: str = "",
        messages: list = [],
    ) -> dict:
        initial: RebuttalState = {
            "messages": messages,
            "disposition_text": disposition_text,
            "filing_type": filing_type,
            "taxpayer_name": taxpayer_name,
            "taxpayer_id": taxpayer_id,
            "tax_office": tax_office,
            "disposition_date": disposition_date,
            "tax_amount": tax_amount,
            "tax_type": tax_type,
            "tax_claims": [],
            "key_issues": [],
            "deadlines": {},
            "winning_court_cases": None,
            "favorable_taxtr_cases": None,
            "law_articles": None,
            "draft": None,
            "reflect_count": 0,
            "final_report": None,
            "unverified_citations": [],
        }
        return _get_graph().invoke(initial)
