# backend/main.py
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")  # 29_FINAL/.env (로컬 개발용)

from fastapi import FastAPI, Request
import requests
from fastapi.middleware.cors import CORSMiddleware
from routers import cases, law, agent, risk, taxtr, court, strategy, taxlaw_prec, trend, itcl
from routers import (publications, publications_a,
                             publications_b, publications_c,
                             publications_d, publications_e, search)

from jose import jwt   

import sys
print(sys.executable)


app = FastAPI(title="Themis Law API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# 🔐 Clerk Auth Middleware
# =========================
CLERK_ISSUER = os.getenv("CLERK_ISSUER", "")
CLERK_JWKS_URL = f"{CLERK_ISSUER}/.well-known/jwks.json" if CLERK_ISSUER else ""

_jwks_cache = None
def get_jwks():
    global _jwks_cache
    if _jwks_cache is None and CLERK_JWKS_URL:
        _jwks_cache = requests.get(CLERK_JWKS_URL).json()
    return _jwks_cache


@app.middleware("http")
async def clerk_auth_middleware(request: Request, call_next):
    request.state.user_id = None

    if CLERK_ISSUER:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
            try:
                jwks = get_jwks()
                if jwks:
                    payload = jwt.decode(
                        token, jwks, algorithms=["RS256"],
                        issuer=CLERK_ISSUER, options={"verify_aud": False},
                    )
                    request.state.user_id = payload.get("sub")
            except Exception:
                pass

    response = await call_next(request)
    return response


app.include_router(cases.router, prefix="/api/cases", tags=["cases"])
app.include_router(law.router, prefix="/api/law", tags=["law"])

app.include_router(publications.router, prefix="/api/publications", tags=["publications"])
app.include_router(publications_a.router, prefix="/api/publications/a", tags=["publications-a"])
app.include_router(publications_b.router, prefix="/api/publications/b", tags=["publications-b"])
app.include_router(publications_c.router, prefix="/api/publications/c", tags=["publications-c"])
app.include_router(publications_d.router, prefix="/api/publications/d", tags=["publications-d"])
app.include_router(publications_e.router, prefix="/api/publications/e", tags=["publications-e"])
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(risk.router, prefix="/api/risk", tags=["risk"])
app.include_router(taxtr.router, prefix="/api/taxtr", tags=["taxtr"])
app.include_router(court.router, prefix="/api/court", tags=["court"])
app.include_router(strategy.router, prefix="/api/strategy", tags=["strategy"])
app.include_router(taxlaw_prec.router, prefix="/api/prec", tags=["prec"])
app.include_router(trend.router, prefix="/api/trend", tags=["trend"])
app.include_router(itcl.router, prefix="/api/itcl", tags=["itcl"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug/chroma")
def debug_chroma():
    """임시 디버그: Chroma 디렉토리 구조와 컬렉션 목록 반환."""
    import os
    chroma_dir = os.environ.get("CHROMA_DIR", "/app/chroma")
    result = {"chroma_dir": chroma_dir, "files": [], "collections": [], "error": None}
    try:
        for root, dirs, files in os.walk(chroma_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), chroma_dir)
                result["files"].append(rel)
                if len(result["files"]) > 30:
                    result["files"].append("... (truncated)")
                    break
            if len(result["files"]) > 30:
                break
    except Exception as e:
        result["error"] = str(e)
    try:
        import chromadb
        from chromadb.utils import embedding_functions
        ef = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model_name="text-embedding-3-small",
        )
        client = chromadb.PersistentClient(path=chroma_dir)
        for name in ["taxlaw_prec", "taxtr_cases", "law_articles"]:
            try:
                col = client.get_collection(name, embedding_function=ef)
                cnt = col.count()
                result["collections"].append({"name": name, "count": cnt})
            except Exception as ce:
                result["collections"].append({"name": name, "error": str(ce)[:120]})
    except Exception as e:
        result["collections_error"] = str(e)
    return result