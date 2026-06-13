"""Railway start script — Railpack venv Python 자동 탐색."""
import os
import subprocess
import sys
from pathlib import Path


def _find_python():
    """uvicorn이 설치된 Python을 찾는다. Railpack은 /app/.venv에 venv를 만든다."""
    # 1. 현재 Python에 uvicorn 있으면 그냥 사용
    try:
        import uvicorn  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    # 2. Railpack/nixpacks venv 경로 탐색
    for candidate in ["/app/.venv/bin/python", "/root/.venv/bin/python", "/home/app/.venv/bin/python"]:
        if Path(candidate).exists():
            r = subprocess.run([candidate, "-c", "import uvicorn"], capture_output=True)
            if r.returncode == 0:
                print(f"[start.py] venv Python 사용: {candidate}")
                return candidate

    # 3. 최후 수단: 현재 Python에 uvicorn 설치
    print("[start.py] uvicorn 설치 중 (최후 수단)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "uvicorn[standard]", "-q"], check=False)
    return sys.executable


python = _find_python()
print(f"[start.py] Python={python}")

subprocess.run([python, "init_chroma.py"], check=False)

port = os.environ.get("PORT", "8000")
print(f"[start.py] uvicorn 시작 PORT={port}")
os.execvp(python, [python, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", port])
