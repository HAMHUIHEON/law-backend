"""Railway 시작 스크립트. Dockerfile CMD로 실행 — sys.executable은 항상 /usr/local/bin/python."""
import os
import subprocess
import sys

subprocess.run([sys.executable, "init_chroma.py"], check=False)

port = os.environ.get("PORT", "8000")
print(f"[start.py] uvicorn 시작 (Python: {sys.executable}, PORT: {port})")
os.execvp(sys.executable, [
    sys.executable, "-m", "uvicorn", "main:app",
    "--host", "0.0.0.0", "--port", port,
])
