"""
법제처 DRF API와 보유 버전을 비교해 신규 공포 버전을 감지한다.
법제처 API: http://www.law.go.kr/DRF/lawSearch.do
"""
import json
import urllib.parse
import urllib.request
from typing import Optional


_OC = "seungmi0723"
_DRF_SEARCH = "http://www.law.go.kr/DRF/lawSearch.do"


def _fetch_latest_from_drf(law_name: str) -> Optional[dict]:
    """
    법제처 DRF API에서 법령명 검색 → 최신 공포 버전 정보 반환.
    반환: {"pno": "...", "pdate": "...", "law_name": "..."} 또는 None
    """
    url = (
        f"{_DRF_SEARCH}?OC={_OC}&target=lsHistory&type=JSON"
        f"&query={urllib.parse.quote(law_name)}&display=5&page=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        laws = data.get("LawSearch", {}).get("law", [])
        if not laws:
            return None
        # 첫 번째 항목이 최신
        item = laws[0] if isinstance(laws, list) else laws
        return {
            "pno": str(item.get("공포번호", "")),
            "pdate": str(item.get("공포일자", "")),
            "law_name": str(item.get("법령명한글", law_name)),
        }
    except Exception:
        return None


def poll_all_laws(kinds: list = None) -> dict:
    """
    보유 버전 인덱스와 법제처 DRF API를 비교해 신규 공포 버전을 감지한다.

    Args:
        kinds: 감지할 법령 종류 목록. 기본값 ["LAW"].

    반환:
        {법령명: [{"kind": ..., "pno": ..., "pdate": ...}, ...], ...}
        신규 버전이 없으면 해당 법령은 포함되지 않는다.
    """
    from RISK.consulting import LAW_SLUGS, KIND_FOLDER, LAW_DIR

    if kinds is None:
        kinds = ["LAW"]

    result: dict = {}

    for law_name, slug in LAW_SLUGS.items():
        new_versions = []

        for kind in kinds:
            folder = KIND_FOLDER.get(kind)
            if not folder:
                continue
            idx_path = LAW_DIR / slug / folder / "_version_index.json"
            if not idx_path.exists():
                continue

            with idx_path.open(encoding="utf-8") as f:
                idx = json.load(f)

            known_pnos = {str(entry.get("pno", "")) for entry in idx.values()}

            latest = _fetch_latest_from_drf(law_name)
            if latest and latest["pno"] and latest["pno"] not in known_pnos:
                new_versions.append({
                    "kind": kind,
                    "pno": latest["pno"],
                    "pdate": latest["pdate"],
                    "api_law_name": latest["law_name"],
                })

        if new_versions:
            result[law_name] = new_versions

    return result
