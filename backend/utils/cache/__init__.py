# utils/cache/__init__.py

import os
from utils.cache import local

USE_SUPABASE_CACHE = os.environ.get("USE_SUPABASE_CACHE") == "1"

_supabase = None

def _get_supabase():
    global _supabase
    if _supabase is None and os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        from utils.cache.supabase import SupabaseStorageCache
        _supabase = SupabaseStorageCache()
    return _supabase


def save_cache(case_id, filename, data):
    sb = _get_supabase()
    if USE_SUPABASE_CACHE and sb:
        return sb.save_cache(case_id, filename, data)
    return local.save_cache(case_id, filename, data)


def load_cache(case_id, filename):
    sb = _get_supabase()
    if sb:
        try:
            data = sb.load_cache(case_id, filename)
            if data is not None:
                return data
        except Exception:
            pass
    return local.load_cache(case_id, filename)