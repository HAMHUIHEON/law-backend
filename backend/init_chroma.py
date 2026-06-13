"""Railway cold-start: Chroma DB download if not present or outdated. Pure Python."""
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", "/app/chroma"))
CHROMA_URL = os.environ.get("CHROMA_DOWNLOAD_URL", "")
# 이 버전 번호를 올리면 Railway Volume의 기존 데이터를 삭제하고 재다운로드
CHROMA_VERSION = "v2"

version_file = CHROMA_DIR / ".chroma_version"


def _is_current():
    if not (CHROMA_DIR / "chroma.sqlite3").exists():
        return False
    if not version_file.exists():
        return False
    return version_file.read_text().strip() == CHROMA_VERSION


if _is_current():
    print(f"[init_chroma] Chroma {CHROMA_VERSION} 최신 — 스킵")
    sys.exit(0)

if not CHROMA_URL:
    print("[init_chroma] WARNING: CHROMA_DOWNLOAD_URL not set. Starting without Chroma.")
    sys.exit(0)

# 기존 데이터 삭제 후 재다운로드
if CHROMA_DIR.exists():
    print(f"[init_chroma] 기존 Chroma 삭제 → 재다운로드 (버전: {CHROMA_VERSION})")
    shutil.rmtree(CHROMA_DIR)

print(f"[init_chroma] Chroma 다운로드 시작: {CHROMA_URL}")
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

# 파일 확장자에 따라 다운로드 경로 결정
url_lower = CHROMA_URL.lower()
if ".tar.gz" in url_lower or url_lower.endswith(".tgz"):
    dl_path = Path("/tmp/chroma_data.tar.gz")
else:
    dl_path = Path("/tmp/chroma_data.zip")

subprocess.run(
    ["curl", "-L", "--retry", "3", "--retry-delay", "5", "-o", str(dl_path), CHROMA_URL],
    check=True,
)

print(f"[init_chroma] 압축 해제 → {CHROMA_DIR}")
if dl_path.suffix in (".gz", ".tgz") or str(dl_path).endswith(".tar.gz"):
    with tarfile.open(dl_path, "r:gz") as tf:
        tf.extractall(str(CHROMA_DIR))
else:
    with zipfile.ZipFile(dl_path) as zf:
        for member in zf.infolist():
            member.filename = member.filename.replace("\\", "/")
            zf.extract(member, str(CHROMA_DIR))

dl_path.unlink(missing_ok=True)
version_file.write_text(CHROMA_VERSION)
print(f"[init_chroma] Chroma {CHROMA_VERSION} 준비 완료")
