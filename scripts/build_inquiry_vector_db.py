"""
국세청 질의회신(reply) 벡터 DB 구축 — Chroma + text-embedding-3-small

소스: taxlaw/data/reply/reply.jsonl  (119,000+건)
임베딩 텍스트: [세목: {tax_type}] {title}\n{gist}
컬렉션: inquiry_cases
저장 위치: vector_db/chroma

실행:
  python scripts/build_inquiry_vector_db.py           # 신규/증분 구축
  python scripts/build_inquiry_vector_db.py --reset   # 초기화 후 재구축
  python scripts/build_inquiry_vector_db.py --max 10000  # 최대 N건 (테스트용)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import dotenv
dotenv.load_dotenv(Path(__file__).parent.parent / ".env")
sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb.utils import embedding_functions

ROOT       = Path(__file__).parent.parent
JSONL_PATH = ROOT / "taxlaw" / "data" / "reply" / "reply.jsonl"
CHROMA_DIR = ROOT / "vector_db" / "chroma"

OPENAI_KEY  = os.getenv("OPENAI_API_KEY", "")
COLLECTION  = "inquiry_cases"
BATCH_SIZE  = 100
MIN_TEXT_LEN = 10


def _build_doc_text(r: dict) -> str:
    parts = []
    tlaw = r.get("NTST_TLAW_CL_NM", "").strip()
    ttl  = r.get("TTL", "").strip()
    gist = r.get("GIST_CNTN", "").strip()

    if tlaw:
        parts.append(f"[세목: {tlaw}]")
    if ttl:
        parts.append(ttl)
    if gist:
        parts.append(gist[:400])
    return "\n".join(parts)


def _parse_date(raw: str) -> str:
    """'20260608000000' → '20260608'"""
    s = str(raw).replace("-", "").strip()
    return s[:8] if len(s) >= 8 else s


def _build_metadata(r: dict) -> dict:
    return {
        "doc_id":    str(r.get("DOC_ID", "")),
        "doc_no":    (r.get("DOCU_NO_STR1", "") or r.get("DOCU_NO_STR2", ""))[:100],
        "tax_type":  r.get("NTST_TLAW_CL_NM", "")[:50],
        "reply_date": _parse_date(r.get("NTST_DCM_RGT_DT", "")),
        "title":     r.get("TTL", "")[:200],
        "category":  r.get("category", "reply"),
    }


def get_collection(reset: bool = False) -> chromadb.Collection:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=OPENAI_KEY,
        model_name="text-embedding-3-small",
    )

    if reset:
        try:
            client.delete_collection(COLLECTION)
            print("기존 컬렉션 삭제 완료")
        except Exception:
            pass

    return client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def get_existing_ids(col: chromadb.Collection) -> set[str]:
    try:
        return set(col.get(include=[])["ids"])
    except Exception:
        return set()


def run(reset: bool = False, max_docs: int = 0) -> None:
    if not JSONL_PATH.exists():
        print(f"소스 없음: {JSONL_PATH}")
        print("먼저 scrape_taxlaw.py --categories reply 실행")
        return

    col = get_collection(reset=reset)
    existing_ids = get_existing_ids(col)
    print(f"기존 인제스트: {len(existing_ids):,}건")

    docs, metas, ids = [], [], []
    skipped_dup = 0
    skipped_short = 0
    total_read = 0
    saved = 0
    t0 = time.time()

    def _flush(force: bool = False) -> None:
        nonlocal docs, metas, ids, saved
        if not docs:
            return
        if not force and len(docs) < BATCH_SIZE:
            return
        col.upsert(documents=docs, metadatas=metas, ids=ids)
        saved += len(docs)
        docs, metas, ids = [], [], []

    with JSONL_PATH.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue

            total_read += 1
            doc_id = f"inquiry__{r.get('DOC_ID', total_read)}"

            if doc_id in existing_ids:
                skipped_dup += 1
                continue

            text = _build_doc_text(r)
            if len(text) < MIN_TEXT_LEN:
                skipped_short += 1
                continue

            docs.append(text)
            metas.append(_build_metadata(r))
            ids.append(doc_id)

            if len(docs) >= BATCH_SIZE:
                _flush(force=True)

            if max_docs and total_read >= max_docs:
                print(f"max_docs({max_docs}) 달성. 중단.")
                break

            if total_read % 5000 == 0:
                elapsed = time.time() - t0
                pct = (saved + skipped_dup) / max(total_read, 1) * 100
                cost_est = saved * 0.0001 / 1000 * 300  # ~300 tokens/doc
                print(
                    f"[{total_read:,}건 읽음] 인제스트 {saved:,} | "
                    f"중복 {skipped_dup:,} | {elapsed:.0f}s | "
                    f"비용 추정 ~${cost_est:.2f}",
                    flush=True,
                )

    _flush(force=True)

    elapsed = time.time() - t0
    final_count = col.count()
    print(f"\n완료 — 컬렉션 총 {final_count:,}건 / {elapsed:.0f}s")
    print(f"  신규 인제스트: {saved:,}건 | 중복 스킵: {skipped_dup:,}건 | 텍스트 부족: {skipped_short:,}건")
    print(f"  저장 위치: {CHROMA_DIR}")
    cost = saved * 0.0001 / 1000 * 300
    print(f"  비용 추정: ~${cost:.2f} (text-embedding-3-small @$0.0001/1k tokens, ~300 tok/doc)")


def main() -> None:
    ap = argparse.ArgumentParser(description="질의회신 inquiry_cases 벡터 DB 구축")
    ap.add_argument("--reset", action="store_true", help="컬렉션 초기화 후 재구축")
    ap.add_argument("--max", type=int, default=0, dest="max_docs",
                    help="최대 문서 수 (0=무제한, 테스트: 500)")
    args = ap.parse_args()
    run(reset=args.reset, max_docs=args.max_docs)


if __name__ == "__main__":
    main()
