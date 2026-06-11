# backend/main.py
from fastapi import FastAPI, Request
import requests
from fastapi.middleware.cors import CORSMiddleware
from routers import cases, law, agent, risk, taxtr, court, strategy, taxlaw_prec
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
import os
CLERK_ISSUER = os.getenv("CLERK_ISSUER")
if not CLERK_ISSUER:
    raise RuntimeError("CLERK_ISSUER is not set")

CLERK_JWKS_URL = f"{CLERK_ISSUER}/.well-known/jwks.json"

_jwks_cache = None
def get_jwks():
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = requests.get(CLERK_JWKS_URL).json()
    return _jwks_cache


@app.middleware("http")
async def clerk_auth_middleware(request: Request, call_next):
    # 🔑 항상 기본값 먼저
    request.state.user_id = None  # ⭐ 핵심

    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "")

        try:
            jwks = get_jwks()
            payload = jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                issuer=CLERK_ISSUER,
                options={"verify_aud": False},
            )

            #clerk userid
            request.state.user_id = payload.get("sub")

        except Exception:
            # 토큰 문제 → user_id는 None 유지
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


@app.get("/health")
def health():
    return {"status": "ok"}