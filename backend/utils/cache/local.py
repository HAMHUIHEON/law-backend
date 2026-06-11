# utils/cache/local.py

import os
import json
from pathlib import Path
from typing import Any, Optional

CACHE_ROOT = Path("cache")


def _normalize_case_id(case_id: str) -> str:
    s = (case_id or "").strip()
    if "_" in s:
        return s.split("_")[-1].strip()
    return s


def _find_case_dir_by_suffix(case_id: str) -> Optional[Path]:
    if not CACHE_ROOT.exists():
        return None

    suffix = f"_{case_id}"
    matches = [d for d in CACHE_ROOT.iterdir() if d.is_dir() and d.name.endswith(suffix)]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    raise RuntimeError(f"Multiple case dirs found for case_id={case_id}: {[d.name for d in matches]}")


def _resolve_local_path(case_id: str, filename: str) -> Optional[Path]:
    # 1) 신형: cache/{case_id}/{filename}
    direct = CACHE_ROOT / case_id / filename
    if direct.exists():
        return direct

    # 2) 구형: cache/*_{case_id}/{filename}
    d = _find_case_dir_by_suffix(case_id)
    if d:
        p = d / filename
        if p.exists():
            return p

    # 3) normalize 재시도 (대법원_2022두60745 같은 입력 대비)
    normalized = _normalize_case_id(case_id)
    if normalized != case_id:
        direct2 = CACHE_ROOT / normalized / filename
        if direct2.exists():
            return direct2

        d2 = _find_case_dir_by_suffix(normalized)
        if d2:
            p2 = d2 / filename
            if p2.exists():
                return p2

    return None


def ensure_case_folder(case_id):
    path = os.path.join(CACHE_ROOT, case_id)
    os.makedirs(path, exist_ok=True)
    return path


def save_cache(case_id, filename, data):
    base = ensure_case_folder(case_id)
    fp = os.path.join(base, filename)

    if isinstance(data, (bytes, bytearray)):
        with open(fp, "wb") as f:
            f.write(data)
    elif isinstance(data, str):
        with open(fp, "w", encoding="utf-8") as f:
            f.write(data)
    else:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return fp


def load_cache(case_id: str, filename: str) -> Any | None:
    p = _resolve_local_path(case_id, filename)
    if not p:
        return None

    if filename.endswith(".json"):
        return json.loads(p.read_text(encoding="utf-8"))
    if filename.endswith(".md") or filename.endswith(".txt"):
        return p.read_text(encoding="utf-8")
    return p.read_bytes()