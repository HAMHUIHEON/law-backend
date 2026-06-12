#!/bin/bash
# start.sh — Railway 시작 스크립트
# Chroma Volume이 비어 있으면 GitHub Releases에서 자동 다운로드 후 시작

set -e

CHROMA_DIR="${CHROMA_DIR:-/app/chroma}"
CHROMA_URL="${CHROMA_DOWNLOAD_URL:-}"

if [ ! -f "$CHROMA_DIR/chroma.sqlite3" ]; then
    echo "[start.sh] Chroma 데이터 없음 — 다운로드 시작"

    if [ -z "$CHROMA_URL" ]; then
        echo "[start.sh] 경고: CHROMA_DOWNLOAD_URL 환경변수 없음. Chroma 없이 시작."
    else
        mkdir -p "$CHROMA_DIR"
        echo "[start.sh] 다운로드: $CHROMA_URL"
        curl -L --retry 3 --retry-delay 5 -o /tmp/chroma_data.zip "$CHROMA_URL"
        echo "[start.sh] 압축 해제 → $CHROMA_DIR"
        python3 -c "
import zipfile, os
with zipfile.ZipFile('/tmp/chroma_data.zip') as zf:
    # Windows 경로 구분자 정규화
    for member in zf.infolist():
        member.filename = member.filename.replace('\\\\', '/')
        zf.extract(member, '$CHROMA_DIR')
print('압축 해제 완료')
"
        rm /tmp/chroma_data.zip
        echo "[start.sh] Chroma 준비 완료"
    fi
else
    echo "[start.sh] Chroma 데이터 존재 — 다운로드 생략"
fi

echo "[start.sh] uvicorn 시작"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
