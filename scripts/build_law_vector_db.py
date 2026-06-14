"""
법령 조문 벡터 DB 구축 — Chroma + text-embedding-3-small

대상: law/{slug}/{kind}/_version_index.json 기준 최신 버전 MST 파일
청크 단위: Article (조문) — 제목 + 본문 전체
저장 위치: vector_db/chroma (로컬 영구 저장)

실행:
  python scripts/build_law_vector_db.py           # 전체 구축
  python scripts/build_law_vector_db.py --reset   # DB 초기화 후 재구축
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

import dotenv
dotenv.load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb.utils import embedding_functions

ROOT       = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from ITCL.convert_drf_law_to_unified import convert_drf_law_to_unified
LAW_DIR    = ROOT / "law"
CHROMA_DIR = ROOT / "vector_db" / "chroma"

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

LAWS = [
    {"name": "국세기본법",           "slug": "gukse_basic",          "kinds": ["law", "decree"]},
    {"name": "법인세법",             "slug": "corporate_tax",        "kinds": ["law", "decree", "rule"]},
    {"name": "소득세법",             "slug": "income_tax",           "kinds": ["law", "decree", "rule"]},
    {"name": "부가가치세법",         "slug": "vat",                  "kinds": ["law", "decree", "rule"]},
    {"name": "국세징수법",           "slug": "gukse_collection",     "kinds": ["law", "decree", "rule"]},
    {"name": "조세범처벌법",         "slug": "tax_crime",            "kinds": ["law"]},
    {"name": "조세범처벌절차법",     "slug": "tax_crime_proc",       "kinds": ["law", "decree"]},
    {"name": "국제조세조정에 관한 법률", "slug": "itcl",             "kinds": ["law", "decree", "rule"]},
    {"name": "상속세 및 증여세법",   "slug": "inheritance_tax",      "kinds": ["law", "decree", "rule"]},
    {"name": "관세법",               "slug": "customs",              "kinds": ["law", "decree", "rule"]},
    {"name": "자본시장법",           "slug": "capital_market",       "kinds": ["law", "decree", "rule"]},
    {"name": "개별소비세법",         "slug": "individual_consumption","kinds": ["law", "decree"]},
    {"name": "종합부동산세법",       "slug": "comprehensive_realty", "kinds": ["law", "decree"]},
    {"name": "조세특례제한법",       "slug": "joseteukrejehan",      "kinds": ["law", "decree", "rule"]},
]

SCOPE_LABEL = {"LAW": "법", "DECREE": "시행령", "RULE": "시행규칙"}


# ── 텍스트 추출 ────────────────────────────────────────────────────────────────

def _flat_text(v) -> str:
    if isinstance(v, list):
        return " ".join(str(x).strip() for x in v if x)
    return str(v).strip() if v else ""


def _article_text(art: dict, law_name: str, scope: str) -> str:
    """검색에 최적화된 조문 텍스트 구성."""
    title   = _flat_text(art.get("title") or "")
    raw     = _flat_text(art.get("raw_text") or "")
    art_no  = art.get("article_no", "")
    domain  = art.get("domain") or ""

    # 단락 텍스트 보강
    para_texts = []
    for para in art.get("paragraphs", []):
        pt = _flat_text(para.get("text") or "")
        if pt:
            para_texts.append(pt)
        for item in para.get("items", []):
            it = _flat_text(item.get("text") or "")
            if it:
                para_texts.append(it)

    body = raw if raw else " ".join(para_texts)

    header = f"[{law_name} {SCOPE_LABEL.get(scope, scope)} 제{art_no}조 {title}]"
    if domain:
        header += f"\n도메인: {domain}"

    return f"{header}\n\n{body}".strip()


def _article_metadata(art: dict, law_name: str, slug: str,
                      scope: str, version_key: str, effective_date: str,
                      abolition_date: str = "") -> dict:
    return {
        "law_name":        law_name,
        "slug":            slug,
        "scope":           scope,
        "article_id":      art.get("id", ""),
        "article_no":      str(art.get("article_no", "")),
        "title":           _flat_text(art.get("title") or ""),
        "domain":          art.get("domain") or "",
        "version_key":     version_key,
        "effective_date":  effective_date,
        "abolition_date":  abolition_date,  # 다음 버전 시행일 = 현 버전 폐지일 (최신이면 "")
    }


def _iter_articles(law: dict):
    for ch in law.get("chapters", []):
        for art in ch.get("articles", []):
            yield art
        for sec in ch.get("sections", []):
            for art in sec.get("articles", []):
                yield art
            for sub in sec.get("subdivisions", []):
                for art in sub.get("articles", []):
                    yield art


# ── Chroma 클라이언트 ──────────────────────────────────────────────────────────

def get_collection(reset: bool = False) -> chromadb.Collection:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=OPENAI_KEY,
        model_name="text-embedding-3-small",
    )

    if reset:
        try:
            client.delete_collection("law_articles")
            print("기존 컬렉션 삭제 완료", flush=True)
        except Exception:
            pass

    return client.get_or_create_collection(
        name="law_articles",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


# ── 인제스트 ──────────────────────────────────────────────────────────────────

def load_latest_json(slug: str, kind: str) -> tuple[dict, str, str] | None:
    """최신 버전 JSON 로드. (raw_drf, version_key, abolition_date) 반환.

    abolition_date: 최신 버전이므로 항상 "" (현행 유효).
    과거 버전의 abolition_date = 다음 버전의 eff_date.
    """
    kind_dir = LAW_DIR / slug / kind
    idx_path = kind_dir / "_version_index.json"
    if not idx_path.exists():
        return None
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    if not index:
        return None
    # eff_date 기준 정렬 — 최신 버전이 마지막
    sorted_vers = sorted(index.values(), key=lambda v: v.get("eff_date") or v.get("pdate", "0"))
    latest = sorted_vers[-1]
    fp = kind_dir / latest["file"]
    if not fp.exists():
        return None
    # 최신 버전은 폐지일 없음 (현행)
    return json.loads(fp.read_text(encoding="utf-8")), latest.get("version_key", ""), ""


def ingest_slug_kind(col: chromadb.Collection, law_name: str, slug: str,
                     kind: str) -> int:
    result = load_latest_json(slug, kind)
    if not result:
        print(f"  ⏭️  {slug}/{kind}: 파일 없음", flush=True)
        return 0

    raw, version_key, abolition_date = result
    law    = convert_drf_law_to_unified(raw)
    scope  = (law.get("source_type") or "LAW").upper()
    eff    = law.get("metadata", {}).get("시행일자", "")

    # 이미 인제스트된 버전이면 스킵
    existing = col.get(where={"$and": [{"slug": slug}, {"scope": scope}]}, limit=1)
    if existing["ids"]:
        ev = existing["metadatas"][0].get("version_key", "")
        if ev == version_key:
            print(f"  ⏭️  {slug}/{kind}: 이미 최신 버전 ({version_key})", flush=True)
            return 0

    docs, metas, ids = [], [], []
    for art in _iter_articles(law):
        art_id = art.get("id", "")
        if not art_id:
            continue
        text = _article_text(art, law_name, scope)
        if len(text.strip()) < 10:
            continue
        doc_id = f"{slug}__{scope}__{version_key}__{art_id}"
        docs.append(text)
        metas.append(_article_metadata(art, law_name, slug, scope, version_key, eff, abolition_date))
        ids.append(doc_id)

    if not docs:
        return 0

    # 배치 업서트 (100개씩)
    BATCH = 100
    for i in range(0, len(docs), BATCH):
        col.upsert(
            documents=docs[i:i+BATCH],
            metadatas=metas[i:i+BATCH],
            ids=ids[i:i+BATCH],
        )

    print(f"  ✅ {slug}/{kind}: {len(docs)}개 조문 임베딩 완료", flush=True)
    return len(docs)


def run(reset: bool = False) -> None:
    print("=== 법령 조문 벡터 DB 구축 ===\n", flush=True)
    col   = get_collection(reset=reset)
    t0    = time.time()
    total = 0

    for law_def in LAWS:
        name, slug, kinds = law_def["name"], law_def["slug"], law_def["kinds"]
        print(f"\n▶ {name}", flush=True)
        for kind in kinds:
            total += ingest_slug_kind(col, name, slug, kind)

    elapsed = time.time() - t0
    print(f"\n🎉 완료 — 총 {total}개 조문 / {elapsed:.0f}s", flush=True)
    print(f"   저장 위치: {CHROMA_DIR}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="기존 컬렉션 삭제 후 재구축")
    args = ap.parse_args()
    run(reset=args.reset)
