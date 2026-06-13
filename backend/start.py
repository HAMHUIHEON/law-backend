"""
Railway 시작 스크립트. sys.executable로 현재 Python 그대로 uvicorn 실행 — PATH 의존 없음.
"""
import os
import subprocess
import sys

# uvicorn이 없으면 설치 (어떤 Python 환경이든 대응)
try:
    import uvicorn  # noqa: F401
except ImportError:
    print("[start.py] uvicorn 없음 — 설치 중...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "uvicorn[standard]", "-q"],
        check=True,
    )

# Chroma 초기화
subprocess.run([sys.executable, "init_chroma.py"], check=False)

# uvicorn 시작 (sys.executable = 현재 Python 그대로 사용)
port = os.environ.get("PORT", "8000")
print(f"[start.py] uvicorn 시작 (Python: {sys.executable}, PORT: {port})")
os.execvp(
    sys.executable,
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", port],
)
