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
CHROMA_VERSION = "v4"

version_file = CHROMA_DIR / ".chroma_version"


def _chroma_sqlite_exists() -> bool:
    """chromadb 버전에 따라 sqlite3 위치가 달라질 수 있어 두 경로 모두 확인."""
    return (CHROMA_DIR / "chroma.sqlite3").exists() or (CHROMA_DIR / "chroma" / "chroma.sqlite3").exists()


def _is_current():
    # Version file alone is enough — trust it even if sqlite3 is still initializing
    if version_file.exists() and version_file.read_text().strip() == CHROMA_VERSION:
        return True
    # No version file but data exists: version unknown, re-download needed
    return False


if _is_current():
    print(f"[init_chroma] Chroma {CHROMA_VERSION} 최신 — 스킵")
    sys.exit(0)

if not CHROMA_URL:
    print("[init_chroma] WARNING: CHROMA_DOWNLOAD_URL not set. Starting without Chroma.")
    sys.exit(0)

# 기존 데이터 삭제 후 재다운로드
if CHROMA_DIR.exists():
    existing_ver = version_file.read_text().strip() if version_file.exists() else ""
    # 이미 동일 버전 표시가 있는데 URL이 오래된 버전이면 덮어쓰기 금지
    if existing_ver and existing_ver >= CHROMA_VERSION:
        print(f"[init_chroma] 이미 {existing_ver} 마킹됨 — URL 재다운로드 차단 (volume 데이터 보호)")
        sys.exit(0)
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
        members = tf.getmembers()
        # tar 내부에 최상위 'chroma/' 디렉토리가 있으면 프리픽스 제거 후 추출
        top_dirs = {m.name.split("/")[0] for m in members if m.name}
        strip_prefix = None
        if len(top_dirs) == 1 and list(top_dirs)[0] == "chroma":
            strip_prefix = "chroma"
        for member in members:
            if strip_prefix and member.name.startswith(strip_prefix + "/"):
                member.name = member.name[len(strip_prefix) + 1:]
                if not member.name:  # 프리픽스 자체 엔트리 건너뜀
                    continue
            elif strip_prefix and member.name == strip_prefix:
                continue
            tf.extract(member, str(CHROMA_DIR))
else:
    with zipfile.ZipFile(dl_path) as zf:
        for member in zf.infolist():
            member.filename = member.filename.replace("\\", "/")
            zf.extract(member, str(CHROMA_DIR))

dl_path.unlink(missing_ok=True)
version_file.write_text(CHROMA_VERSION)
print(f"[init_chroma] Chroma {CHROMA_VERSION} 준비 완료")
