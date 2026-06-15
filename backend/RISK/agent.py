from langchain_core.messages import HumanMessage
from utils.llm import get_llm, DEFAULT_MODEL

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = get_llm(model=DEFAULT_MODEL, temperature=0)
    return _llm


def _format_revision(revision: dict) -> str:
    if not revision:
        return "(개정 관측 결과 없음)"
    lines = [
        f"공포일: {revision.get('promulgated_at', '')}",
        f"시행일: {revision.get('effective_at', '')}",
    ]
    if revision.get("notes"):
        lines.append(f"요약: {revision['notes']}")
    changes = revision.get("observed_changes", [])
    if changes:
        lines.append(f"\n변경사항 ({len(changes)}건):")
        for c in changes[:6]:
            tgt = c.get("target", {})
            lines.append(
                f"  [{c.get('change_type', '')}] {tgt.get('label', '')} — "
                f"{c.get('description', '')[:120]}"
            )
    return "\n".join(lines)


def _format_addenda(addenda: dict) -> str:
    if not addenda:
        return "(부칙 분석 없음)"
    items = addenda.get("addenda", [])
    lines = [f"부칙 수: {len(items)}건"]
    for item in items[:3]:
        roles = item.get("roles", [])
        role_labels = ", ".join(r.get("role", "") for r in roles[:3])
        lines.append(f"  {item.get('addenda_date', '')}: {role_labels}")
    return "\n".join(lines)


class RiskAgent:
    """법령 개정 리스크 자연어 질의 에이전트."""

    def ask(self, question: str) -> str:
        from RISK.consulting import LAW_SLUGS, run_full_analysis

        # 질문에서 법령명 감지 (긴 이름 우선 → 짧은 이름이 부분 매칭되는 문제 방지)
        matched_law = None
        for law_name in sorted(LAW_SLUGS.keys(), key=len, reverse=True):
            if law_name in question or law_name.replace(" ", "") in question:
                matched_law = law_name
                break

        if matched_law:
            try:
                result = run_full_analysis(matched_law, "LAW")
                rev_text = _format_revision(result.revision)
                add_text = _format_addenda(result.addenda)
                context = (
                    f"### {matched_law} (버전: {result.version_key})\n\n"
                    f"[개정 관측]\n{rev_text}\n\n"
                    f"[부칙 분석]\n{add_text}"
                )
            except FileNotFoundError as e:
                context = f"법령 데이터 없음: {e}"
            except Exception as e:
                context = f"분석 오류: {e}"
        else:
            supported = ", ".join(LAW_SLUGS.keys())
            context = (
                f"질문에서 법령을 특정하지 못했습니다.\n"
                f"지원 법령: {supported}"
            )

        prompt = (
            "당신은 세법 개정 리스크 전문 AI 어시스턴트입니다.\n"
            "아래 법령 개정 분석 결과를 바탕으로 질문에 답하세요.\n\n"
            f"[분석 결과]\n{context}\n\n"
            f"[질문]\n{question}\n\n"
            "[답변 지침]\n"
            "- 개정 배경과 주요 변경 내용을 명확하게 설명\n"
            "- 실무적 리스크(신고 부담, 해석 불확실성, 절차 변경)를 구체적으로 기술\n"
            "- 제공된 데이터 외 사실 생성 금지"
        )
        resp = _get_llm().invoke([HumanMessage(content=prompt)])
        return resp.content
