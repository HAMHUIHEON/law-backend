import json
import urllib.parse
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

# 법령 JSON DB — 29_FINAL/law/ (로컬 전용, git/Railway 제외)
LAW_DIR = Path(__file__).parent.parent.parent / "law"

# 법령명 → 폴더 슬러그
LAW_SLUGS = {
    "국제조세조정에 관한 법률": "itcl",
    "법인세법": "corporate_tax",
    "소득세법": "income_tax",
    "부가가치세법": "vat",
    "국세기본법": "gukse_basic",
    "국세징수법": "gukse_collection",
    "조세범처벌법": "tax_crime",
    "조세범처벌절차법": "tax_crime_proc",
    "상속세 및 증여세법": "inheritance_tax",
    "관세법": "customs",
    "자본시장과 금융투자업에 관한 법률": "capital_market",
    "개별소비세법": "individual_consumption",
    "종합부동산세법": "comprehensive_realty",
    "조세특례제한법": "joseteukrejehan",
}

# kind ("LAW"/"DECREE"/"RULE") → 폴더명
KIND_FOLDER = {
    "LAW": "law",
    "DECREE": "decree",
    "RULE": "rule",
}

# 폴더명 → run.py가 기대하는 source_type 값
_FOLDER_TO_SOURCE_TYPE = {
    "law": "law",
    "decree": "admrul",
    "rule": "admrule",
}

# 외부 법령 → 연동 세법 목록
CROSS_LAW_LINKS = {
    "조세특례제한법": ["법인세법", "소득세법", "부가가치세법", "상속세 및 증여세법"],
    "국세기본법": ["법인세법", "소득세법", "부가가치세법", "국세징수법", "국제조세조정에 관한 법률"],
    "상속세 및 증여세법": ["국세기본법", "조세특례제한법"],
}


class ConsultingResult(BaseModel):
    law_name: str
    kind: str
    version_key: str
    revision: Optional[dict] = None
    addenda: Optional[dict] = None
    annex: Optional[dict] = None

    def to_dict(self) -> dict:
        return self.model_dump()


class CrossLawResult(BaseModel):
    source_law: str
    linked_laws: list
    analyses: list

    def model_dump(self) -> dict:
        return {
            "source_law": self.source_law,
            "linked_laws": self.linked_laws,
            "analyses": self.analyses,
        }


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _get_slug(law_name: str) -> str:
    if law_name not in LAW_SLUGS:
        raise ValueError(
            f"지원하지 않는 법령: '{law_name}'. 지원 목록: {list(LAW_SLUGS.keys())}"
        )
    return LAW_SLUGS[law_name]


def _get_folder(kind: str) -> str:
    folder = KIND_FOLDER.get(kind.upper())
    if not folder:
        raise ValueError(f"지원하지 않는 kind: '{kind}'. 가능: {list(KIND_FOLDER.keys())}")
    return folder


def _load_version_index(law_name: str, kind: str) -> dict:
    slug = _get_slug(law_name)
    folder = _get_folder(kind)
    idx_path = LAW_DIR / slug / folder / "_version_index.json"
    if not idx_path.exists():
        raise FileNotFoundError(f"버전 인덱스 없음: {idx_path}")
    with idx_path.open(encoding="utf-8") as f:
        return json.load(f)


def _extract_text_chunks(field) -> list:
    """DRF JSON의 list-of-lists 또는 dict 필드에서 텍스트 블록 추출."""
    if not field:
        return []
    results = []
    # 직접 list인 경우 (예: 제개정이유내용 = [[line1, line2], ...])
    if isinstance(field, list):
        for chunk in field:
            if isinstance(chunk, list):
                results.append("\n".join(str(x) for x in chunk))
            elif isinstance(chunk, str):
                results.append(chunk)
    return results


def _raw_to_converted(raw: dict, folder: str) -> dict:
    """
    법제처 DRF API raw JSON ({"법령": {...}}) →
    RISK/run.py가 기대하는 converted 형식으로 변환
    """
    law = raw.get("법령", {})
    info = law.get("기본정보", {})

    law_name = info.get("법령명_한글") or info.get("법령명한글") or ""
    source_type = _FOLDER_TO_SOURCE_TYPE.get(folder, "law")

    # 제개정이유
    ri = law.get("제개정이유")
    revision_reasons = []
    if isinstance(ri, dict):
        revision_reasons = _extract_text_chunks(ri.get("제개정이유내용", []))

    # 개정문
    gae = law.get("개정문")
    amendments = []
    if isinstance(gae, dict):
        amendments = _extract_text_chunks(gae.get("개정문내용", []))

    # 부칙
    addenda = []
    buch = law.get("부칙")
    if isinstance(buch, dict):
        for unit in buch.get("부칙단위", []) or []:
            date = str(unit.get("부칙공포일자", ""))
            raw_content = unit.get("부칙내용", [])
            parts = _extract_text_chunks(raw_content)
            text = "\n\n".join(parts)
            if date and text.strip():
                addenda.append({"date": date, "text": text})

    return {
        "law_name": law_name,
        "source_type": source_type,
        "metadata": {
            "공포일자": str(info.get("공포일자", "")),
            "공포번호": str(info.get("공포번호", "")),
            "시행일자": str(info.get("시행일자", "")),
        },
        "revision_reasons": revision_reasons,
        "amendments": amendments,
        "addenda": addenda,
        "annexes": [],  # 별표는 별도 파싱 미구현
    }


def _load_converted(law_name: str, kind: str, version_key: str) -> dict:
    slug = _get_slug(law_name)
    folder = _get_folder(kind)
    idx = _load_version_index(law_name, kind)

    file_name = None
    for entry in idx.values():
        if entry.get("version_key") == version_key:
            file_name = entry["file"]
            break
    if not file_name:
        raise FileNotFoundError(f"version_key '{version_key}' 없음")

    json_path = LAW_DIR / slug / folder / file_name
    with json_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return _raw_to_converted(raw, folder)


# ── 공개 API ─────────────────────────────────────────────────────────────────

def list_version_keys(law_name: str, kind: str) -> list:
    """최신순 version_key 목록 반환."""
    idx = _load_version_index(law_name, kind)
    keys = sorted({entry["version_key"] for entry in idx.values()}, reverse=True)
    return keys


def run_full_analysis(
    law_name: str,
    kind: str,
    version_key: Optional[str] = None,
) -> ConsultingResult:
    """
    법령 1개 버전에 대해 개정관측 + 부칙관측을 실행하고 ConsultingResult 반환.
    캐시가 있으면 재실행 없이 캐시 결과를 반환한다.
    """
    # chain은 호출 시점에 import (lazy — OPENAI_API_KEY 없으면 모듈 로드 실패 방지)
    from RISK.chain import RevisionObservationChain, AddendaObservationChain
    from RISK.run import run_all_revision, run_all_addenda

    if version_key is None:
        keys = list_version_keys(law_name, kind)
        if not keys:
            raise FileNotFoundError(f"{law_name}/{kind} 버전 없음")
        version_key = keys[0]

    converted = _load_converted(law_name, kind, version_key)

    rev_chain = RevisionObservationChain()
    add_chain = AddendaObservationChain()

    revision = run_all_revision(converted, rev_chain)
    addenda_result = run_all_addenda(converted, add_chain)

    return ConsultingResult(
        law_name=law_name,
        kind=kind,
        version_key=version_key,
        revision=revision.model_dump() if revision else None,
        addenda=addenda_result.model_dump() if addenda_result else None,
        annex=None,
    )


def run_cross_law_analysis(source_law: str) -> CrossLawResult:
    """
    외부 참조 법령(source_law)의 최신 개정이 연동 세법에 미치는 영향을 분석.
    """
    if source_law not in CROSS_LAW_LINKS:
        raise ValueError(f"'{source_law}'은 cross-law 분석 대상이 아닙니다.")

    linked = CROSS_LAW_LINKS[source_law]
    analyses = []

    for law_name in linked:
        try:
            result = run_full_analysis(law_name, "LAW")
            rev = result.revision or {}
            analyses.append({
                "law_name": law_name,
                "version_key": result.version_key,
                "revision_notes": rev.get("notes"),
                "observed_changes_count": len(rev.get("observed_changes", [])),
            })
        except Exception as e:
            analyses.append({"law_name": law_name, "error": str(e)})

    return CrossLawResult(
        source_law=source_law,
        linked_laws=linked,
        analyses=analyses,
    )
