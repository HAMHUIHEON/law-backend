# agents/conversation.py — 멀티턴 대화 공유 유틸리티

from __future__ import annotations

_MAX_HISTORY_TURNS = 3       # 이전 대화 최대 포함 턴 수
_MAX_CONTENT_LEN  = 600      # 턴당 컨텐츠 최대 길이


def build_history_prompt(messages: list[dict]) -> str:
    """대화 히스토리 → LLM 프롬프트용 문자열.

    messages = [{"role": "user"|"assistant", "content": "..."}]
    최근 _MAX_HISTORY_TURNS 쌍만 포함.
    """
    if not messages:
        return ""
    recent = messages[-(2 * _MAX_HISTORY_TURNS):]
    lines = []
    for m in recent:
        role = "사용자" if m.get("role") == "user" else "AI 분석"
        content = (m.get("content") or "")[:_MAX_CONTENT_LEN]
        lines.append(f"[{role}]: {content}")
    return "\n---\n".join(lines)


def build_context_query(query: str, messages: list[dict]) -> str:
    """이전 대화 맥락을 반영한 검색 쿼리 생성.

    이전 assistant 응답 요약을 붙여 벡터 검색 품질을 높인다.
    """
    if not messages:
        return query
    last_asst = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "assistant"),
        "",
    )
    if not last_asst:
        return query
    ctx = last_asst[:300].replace("\n", " ")
    return f"{query}\n[이전 논의 맥락: {ctx}]"


def make_history_section(messages: list[dict]) -> str:
    """LLM 시스템 프롬프트에 삽입할 이전 대화 섹션."""
    hist = build_history_prompt(messages)
    if not hist:
        return ""
    return f"\n\n[이전 대화 기록]\n{hist}\n위 대화를 참고해 연속적이고 일관된 답변을 제공하세요.\n"
