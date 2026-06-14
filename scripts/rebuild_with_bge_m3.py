"""
BGE-m3 재임베딩 스크립트
기존 OpenAI text-embedding-3-small 컬렉션 → BAAI/bge-m3 재임베딩

대상 컬렉션 (소스 → BGE 컬렉션):
  law_articles    → law_articles_bge
  taxlaw_prec     → taxlaw_prec_bge
  taxtr_cases     → taxtr_cases_bge
  inquiry_cases   → inquiry_cases_bge
  pdf_court_cases → pdf_court_cases_bge

⚠️ Railway 메모리 주의:
  BGE-m3 모델 ~570MB + 인퍼런스 ~1.5GB 필요
  Railway Pro(8GB): 사용 가능 / Hobby(512MB): 부족 가능
  로컬에서 빌드 후 Volume 업로드 → chroma_search.py EF 교체 필요

실행:
  python scripts/rebuild_with_bge_m3.py --collection law_articles
  python scripts/rebuild_with_bge_m3.py --collection taxlaw_prec taxtr_cases
  python scripts/rebuild_with_bge_m3.py --all
  python scripts/rebuild_with_bge_m3.py --all --reset
  python scripts/rebuild_with_bge_m3.py --collection law_articles --max 500  # 테스트
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import dotenv
dotenv.load_dotenv(Path(__file__).parent.parent / ".env")
sys.stdout.reconfigure(encoding="utf-8")

import chromadb
from chromadb import EmbeddingFunction

ROOT      = Path(__file__).parent.parent
CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", str(ROOT / "vector_db" / "chroma")))

COLLECTION_MAP = {
    "law_articles":    "law_articles_bge",
    "taxlaw_prec":     "taxlaw_prec_bge",
    "taxtr_cases":     "taxtr_cases_bge",
    "inquiry_cases":   "inquiry_cases_bge",
    "pdf_court_cases": "pdf_court_cases_bge",
}

PAGE_SIZE  = 200   # Chroma get() 페이지 크기
BATCH_SIZE = 32    # BGE-m3 인퍼런스 배치 크기
BGE_MODEL  = "BAAI/bge-m3"
BGE_DIM    = 1024  # BGE-m3 dense output dimension

_model = None
_tokenizer = None


def _get_model():
    """transformers 직접 로딩 — safetensors 강제 사용 (torch 2.2 CVE-2025-32434 우회)."""
    global _model, _tokenizer
    if _model is None:
        import torch
        from transformers import AutoTokenizer, AutoModel

        print(f"BGE-m3 모델 로딩: {BGE_MODEL}  (첫 실행 시 ~570MB 다운로드)")
        t0 = time.time()
        _tokenizer = AutoTokenizer.from_pretrained(BGE_MODEL)
        _model = AutoModel.from_pretrained(BGE_MODEL, use_safetensors=True)
        _model.eval()
        print(f"  로딩 완료 ({time.time()-t0:.1f}s)")
    return _model, _tokenizer


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """BGE-m3 dense 임베딩 — CLS 토큰 벡터, L2 정규화."""
    import torch
    import torch.nn.functional as F

    model, tokenizer = _get_model()
    all_embs = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        encoded = tokenizer(
            batch,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with torch.no_grad():
            output = model(**encoded)
        embs = output.last_hidden_state[:, 0, :]
        embs = F.normalize(embs, p=2, dim=1)
        all_embs.extend(embs.cpu().numpy().tolist())
    return all_embs


class BGEM3EmbeddingFunction(EmbeddingFunction):
    """Chroma 커스텀 EF — transformers 직접 로딩 BGE-m3."""
    def __call__(self, input: list[str]) -> list[list[float]]:
        return _embed_texts(input)


def _dummy_ef() -> EmbeddingFunction:
    """소스 컬렉션 get()에 사용하는 더미 EF (쿼리 안 씀)."""
    class _DummyEF(EmbeddingFunction):
        def __call__(self, input):
            return [[0.0] * 1536] * len(input)
    return _DummyEF()


def _get_client() -> chromadb.ClientAPI:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_source_count(client: chromadb.ClientAPI, src_name: str) -> int:
    try:
        col = client.get_collection(src_name, embedding_function=_dummy_ef())
        return col.count()
    except Exception:
        return 0


def rebuild_collection(
    client: chromadb.ClientAPI,
    src_name: str,
    dst_name: str,
    reset: bool = False,
    max_docs: int = 0,
) -> int:
    # 소스 컬렉션 확인
    try:
        src = client.get_collection(src_name, embedding_function=_dummy_ef())
    except Exception as e:
        print(f"  ✗ 소스 없음: {src_name} ({e})")
        return 0

    total = src.count()
    if total == 0:
        print(f"  ✗ {src_name} 비어있음. 스킵.")
        return 0

    # BGE EF 로드 (모델 초기화)
    bge_ef = BGEM3EmbeddingFunction()
    _get_model()  # 지금 로딩 (첫 컬렉션에서만 시간 걸림)

    # 대상 컬렉션
    if reset:
        try:
            client.delete_collection(dst_name)
            print(f"  기존 {dst_name} 삭제")
        except Exception:
            pass

    dst = client.get_or_create_collection(
        name=dst_name,
        embedding_function=bge_ef,
        metadata={"hnsw:space": "cosine", "embedding_model": BGE_MODEL},
    )

    existing_ids: set = set()
    try:
        existing_ids = set(dst.get(include=[])["ids"])
    except Exception:
        pass

    print(f"\n[{src_name}] → [{dst_name}]")
    print(f"  소스 총 {total:,}건 | 기존 인제스트 {len(existing_ids):,}건")

    saved = 0
    skipped = 0
    offset = 0
    t0 = time.time()
    target = min(total, max_docs) if max_docs else total

    while offset < target:
        limit = min(PAGE_SIZE, target - offset)
        try:
            batch = src.get(
                limit=limit,
                offset=offset,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            print(f"  [ERR offset={offset}] {e}")
            offset += PAGE_SIZE
            continue

        raw_ids  = batch.get("ids", [])
        raw_docs = batch.get("documents", [])
        raw_metas = batch.get("metadatas", [])

        if not raw_ids:
            break

        # 신규만 필터
        new_ids, new_docs, new_metas = [], [], []
        for doc_id, doc, meta in zip(raw_ids, raw_docs, raw_metas):
            bge_id = f"bge__{doc_id}"
            if bge_id in existing_ids:
                skipped += 1
                continue
            new_ids.append(bge_id)
            new_docs.append(doc or "")
            new_metas.append(meta or {})

        if new_docs:
            # BGE-m3 임베딩 후 upsert
            try:
                dst.upsert(documents=new_docs, metadatas=new_metas, ids=new_ids)
                saved += len(new_ids)
            except Exception as e:
                print(f"  [UPSERT ERR] {e}")

        offset += PAGE_SIZE
        elapsed = time.time() - t0
        pct = (saved + skipped + len(existing_ids)) / total * 100
        if (offset // PAGE_SIZE) % 5 == 0 or offset >= target:
            print(
                f"  [{saved+skipped:,}/{target:,}] 저장 {saved:,} | "
                f"스킵 {skipped:,} | {elapsed:.0f}s | {pct:.1f}%",
                flush=True,
            )

    elapsed = time.time() - t0
    final = dst.count()
    print(f"  완료 — {src_name} → {dst_name}: {final:,}건 / {elapsed:.0f}s")
    return saved


def main() -> None:
    ap = argparse.ArgumentParser(description="BGE-m3 재임베딩 스크립트")
    ap.add_argument("--collection", nargs="+",
                    choices=list(COLLECTION_MAP.keys()),
                    help="재임베딩할 컬렉션 (미지정 시 --all 필요)")
    ap.add_argument("--all", action="store_true", help="모든 컬렉션 재임베딩")
    ap.add_argument("--reset", action="store_true", help="대상 BGE 컬렉션 초기화 후 재구축")
    ap.add_argument("--max", type=int, default=0, dest="max_docs",
                    help="컬렉션별 최대 문서 수 (0=무제한, 테스트: 100)")
    args = ap.parse_args()

    if not args.collection and not args.all:
        ap.error("--collection <이름> 또는 --all 을 지정하세요.")

    targets = list(COLLECTION_MAP.keys()) if args.all else args.collection

    print("=== BGE-m3 재임베딩 시작 ===")
    print(f"대상: {targets}")
    print(f"Chroma: {CHROMA_DIR}")
    if args.reset:
        print("⚠️  --reset: 기존 BGE 컬렉션 삭제 후 재구축")
    if args.max_docs:
        print(f"--max {args.max_docs}건 (테스트 모드)")
    print()

    client = _get_client()

    # 소스 존재 여부 사전 확인
    for name in targets:
        cnt = get_source_count(client, name)
        dst = COLLECTION_MAP[name]
        status = f"{cnt:,}건" if cnt else "없음"
        print(f"  {name:20s} → {dst:24s}  소스: {status}")
    print()

    total_saved = 0
    t_all = time.time()
    for name in targets:
        dst = COLLECTION_MAP[name]
        n = rebuild_collection(
            client, name, dst,
            reset=args.reset,
            max_docs=args.max_docs,
        )
        total_saved += n

    elapsed = time.time() - t_all
    print(f"\n=== 전체 완료: {total_saved:,}건 재임베딩 / {elapsed:.0f}s ===")
    print()
    print("다음 단계 — chroma_search.py EF 교체:")
    print("  _get_ef() → BGEM3EmbeddingFunction() 으로 교체하거나")
    print("  search_* 함수에서 컬렉션 이름을 *_bge 로 변경")
    print()
    print("Railway 배포 시 주의:")
    print(f"  BGE-m3 모델({BGE_MODEL}) ~570MB 다운로드 필요")
    print("  Pro 플랜(8GB) 권장. Hobby 플랜은 메모리 부족 가능.")


if __name__ == "__main__":
    main()
