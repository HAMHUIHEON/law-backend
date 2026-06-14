# routers/strategy.py — 불복전략 + 반론초안 + 법령개정리스크 API
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

_strategy_agent = None
_rebuttal_agent = None
_risk_agent = None


def _get_strategy():
    global _strategy_agent
    if _strategy_agent is None:
        from agents.strategy_agent import StrategyAgent
        _strategy_agent = StrategyAgent()
    return _strategy_agent


def _get_rebuttal():
    global _rebuttal_agent
    if _rebuttal_agent is None:
        from agents.rebuttal_agent import RebuttalAgent
        _rebuttal_agent = RebuttalAgent()
    return _rebuttal_agent


def _get_risk():
    global _risk_agent
    if _risk_agent is None:
        from agents.risk_agent import RiskAgent
        _risk_agent = RiskAgent()
    return _risk_agent


class CaseRequest(BaseModel):
    summary: str
    disposition_date: Optional[str] = ""
    tax_amount: Optional[str] = ""
    already_filed: Optional[bool] = False
    messages: Optional[list] = []


class RebuttalRequest(BaseModel):
    disposition_text: str
    filing_type: Optional[str] = "심판청구"
    taxpayer_name: Optional[str] = ""
    taxpayer_id: Optional[str] = ""
    tax_office: Optional[str] = ""
    disposition_date: Optional[str] = ""
    tax_amount: Optional[str] = ""
    tax_type: Optional[str] = ""
    messages: Optional[list] = []


class RiskRequest(BaseModel):
    statute_name: str
    revision_summary: str
    effective_date: Optional[str] = ""
    messages: Optional[list] = []


@router.post("/strategy")
def analyze_strategy(req: CaseRequest):
    """의뢰인 사건 요약 → 유사 판례 검색 + 불복전략 보고서 (불복 기한 자동 계산 포함)."""
    if not req.summary.strip():
        raise HTTPException(status_code=400, detail="summary가 비어 있습니다.")
    try:
        result = _get_strategy().run(
            client_summary=req.summary.strip(),
            disposition_date=req.disposition_date or "",
            tax_amount=req.tax_amount or "",
            already_filed=req.already_filed or False,
            messages=req.messages or [],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")
    return {
        "final_report": result.get("final_report"),
        "court_cases": result.get("court_cases"),
        "taxtr_cases": result.get("taxtr_cases"),
        "law_articles": result.get("law_articles"),
        "deadlines": result.get("deadlines"),
    }


@router.post("/rebuttal")
def generate_rebuttal(req: RebuttalRequest):
    """과세처분 이유서 텍스트 → 납세자 승소 판례 검색 + 반론/불복 문서 초안."""
    if not req.disposition_text.strip():
        raise HTTPException(status_code=400, detail="disposition_text가 비어 있습니다.")
    try:
        result = _get_rebuttal().run(
            disposition_text=req.disposition_text.strip(),
            filing_type=req.filing_type or "심판청구",
            taxpayer_name=req.taxpayer_name or "",
            taxpayer_id=req.taxpayer_id or "",
            tax_office=req.tax_office or "",
            disposition_date=req.disposition_date or "",
            tax_amount=req.tax_amount or "",
            tax_type=req.tax_type or "",
            messages=req.messages or [],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")
    return {
        "final_report": result.get("final_report"),
        "winning_court_cases": result.get("winning_court_cases"),
        "favorable_taxtr_cases": result.get("favorable_taxtr_cases"),
        "law_articles": result.get("law_articles"),
        "unverified_citations": result.get("unverified_citations"),
        "deadlines": result.get("deadlines"),
    }


@router.post("/rebuttal/upload")
async def generate_rebuttal_from_file(file: UploadFile = File(...)):
    """PDF/TXT 과세처분 이유서 업로드 → 텍스트 추출 → 반론 초안."""
    filename = (file.filename or "").lower()
    content = await file.read()

    if filename.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(content))
            pages = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t.strip())
            disposition_text = "\n".join(pages)[:6000]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF 추출 실패: {e}")
    elif filename.endswith((".txt", ".md")):
        try:
            disposition_text = content.decode("utf-8", errors="replace")[:6000]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"텍스트 추출 실패: {e}")
    else:
        raise HTTPException(status_code=400, detail="PDF 또는 TXT 파일만 지원합니다.")

    if not disposition_text.strip():
        raise HTTPException(status_code=400, detail="파일에서 텍스트를 추출할 수 없습니다.")

    try:
        result = _get_rebuttal().run(disposition_text=disposition_text.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")

    return {
        "source_file": file.filename,
        "extracted_chars": len(disposition_text),
        "final_report": result.get("final_report"),
        "winning_court_cases": result.get("winning_court_cases"),
        "favorable_taxtr_cases": result.get("favorable_taxtr_cases"),
        "law_articles": result.get("law_articles"),
    }


@router.post("/strategy/upload")
async def analyze_strategy_from_file(file: UploadFile = File(...)):
    """PDF/TXT 사건 서류 업로드 → 텍스트 추출 → 불복전략 보고서."""
    filename = (file.filename or "").lower()
    content = await file.read()

    if filename.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(content))
            pages = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t.strip())
            summary = "\n".join(pages)[:6000]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF 추출 실패: {e}")
    elif filename.endswith((".txt", ".md")):
        try:
            summary = content.decode("utf-8", errors="replace")[:6000]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"텍스트 추출 실패: {e}")
    else:
        raise HTTPException(status_code=400, detail="PDF 또는 TXT 파일만 지원합니다.")

    if not summary.strip():
        raise HTTPException(status_code=400, detail="파일에서 텍스트를 추출할 수 없습니다.")

    try:
        result = _get_strategy().run(client_summary=summary.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")

    return {
        "source_file": file.filename,
        "extracted_chars": len(summary),
        "final_report": result.get("final_report"),
        "court_cases": result.get("court_cases"),
        "taxtr_cases": result.get("taxtr_cases"),
        "law_articles": result.get("law_articles"),
    }


@router.post("/risk")
def analyze_law_risk(req: RiskRequest):
    """법령 개정 내용 → 기존 판례·재결례 영향 리스크 분석."""
    if not req.statute_name.strip():
        raise HTTPException(status_code=400, detail="statute_name이 비어 있습니다.")
    try:
        result = _get_risk().run(
            statute_name=req.statute_name.strip(),
            revision_summary=req.revision_summary.strip(),
            effective_date=req.effective_date or "",
            messages=req.messages or [],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"에이전트 오류: {e}")
    return {
        "final_report": result.get("final_report"),
        "affected_court_cases": result.get("affected_court_cases"),
        "affected_taxtr_cases": result.get("affected_taxtr_cases"),
        "revised_articles": result.get("revised_articles"),
    }
