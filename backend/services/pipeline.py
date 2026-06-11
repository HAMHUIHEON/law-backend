# backend/services/pipeline.py

from pathlib import Path
from bravo.full_pipeline import run_full_pipeline
from export.full_report import (
    run_export_pipeline_A,
    run_export_pipeline_B,
    run_export_pipeline_C,
)

from supabase import create_client
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, status, Request

import os

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


# Clerk userId를 FastAPI에서 받기
def get_current_user_id(request: Request) -> str:
    user_id = request.state.user_id
    if not user_id:
        if os.getenv("DEV_MODE") == "true":
            return "dev_user"
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user_id



#구독자 체크 함수
def assert_user_is_subscriber(user_id: str) -> None:
    res = (
        _get_supabase()
        .from_("user_access_levels")
        .select("access_level, subscription_end_at")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    if not res.data:
        raise HTTPException(status_code=403, detail="SUBSCRIPTION_REQUIRED")

    row = res.data[0]

    if row["access_level"] != "SUBSCRIBER":
        raise HTTPException(status_code=403, detail="SUBSCRIPTION_REQUIRED")

    # 해지했지만 기간 남아있는 경우 허용
    end_at = row.get("subscription_end_at")
    if end_at is not None:
        from datetime import datetime, timezone
        if datetime.now(timezone.utc) > datetime.fromisoformat(end_at):
            raise HTTPException(status_code=403, detail="SUBSCRIPTION_REQUIRED")


# 판례 분석
def run_case_pipeline(case_id: str, pdf_path: Path, user_id: str):
    from utils.cache import load_cache

    cache_hit = load_cache(case_id, "export_C_full.json") is not None

    if not cache_hit:
        assert_user_is_subscriber(user_id)
        assert_case_analysis_allowed(user_id)
        run_full_pipeline(str(pdf_path))
        record_case_analysis_usage(user_id, case_id)

    return {
        "case_id": case_id,
        "cache_hit": cache_hit,
        "report_A": run_export_pipeline_A(case_id),
        "report_B": run_export_pipeline_B(case_id),
        "report_C": run_export_pipeline_C(case_id),
    }



#1️⃣ 사용량 기록 함수 (usage 1건 저장)
_supabase_client = None

def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _supabase_client

supabase = None  # backward-compat: use _get_supabase() internally


def record_case_analysis_usage(user_id: str, case_id: str) -> None:
    _get_supabase().from_("case_analysis_usage").insert({
        "user_id": user_id,
        "case_id": case_id,
    }).execute()


#2️⃣ 이번 달 사용 건수 조회 함수
def get_monthly_case_usage(user_id: str) -> int:
    now = datetime.now(timezone.utc)
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    res = (
        _get_supabase()
        .from_("case_analysis_usage")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .gte("created_at", month_start.isoformat())
        .execute()
    )

    return res.count or 0


# 3️⃣ 건수 제한 체크 함수
def assert_case_analysis_allowed(user_id: str) -> None:
    MONTHLY_CASE_LIMIT = 10
    used = get_monthly_case_usage(user_id)

    if used >= MONTHLY_CASE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Monthly case analysis limit exceeded",
        )


# 차감 확인용 백엔드 함수
def get_monthly_case_usage_detail(user_id: str):
    MONTHLY_CASE_LIMIT = 10
    now = datetime.now(timezone.utc)
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    res = (
        _get_supabase()
        .from_("case_analysis_usage")
        .select("case_id, created_at")
        .eq("user_id", user_id)
        .gte("created_at", month_start.isoformat())
        .order("created_at", desc=True)
        .execute()
    )

    cases = [row["case_id"] for row in res.data]
    used = len(cases)

    return {
        "limit": MONTHLY_CASE_LIMIT,
        "used": used,
        "remaining": max(MONTHLY_CASE_LIMIT - used, 0),
        "cases": cases,
    }


import re
# 노멀라이즈
def normalize_case_id(raw: str) -> str:
    if not raw:
        raise HTTPException(
            status_code=400,
            detail="사건번호가 비어 있습니다."
        )

    # 2자리 또는 4자리 연도 + 한글 사건유형 + 숫자
    m = re.search(r"(?<![0-9가-힣])(\d{2}|\d{4})[가-힣]{1,3}\d+(?![0-9가-힣])", raw)
    if not m:
        raise HTTPException(
            status_code=400,
            detail="파일명에서 사건번호를 인식할 수 없습니다."
        )

    return m.group(0)

