import os
import json
import tempfile
from typing import Any
from supabase import create_client, Client
import hashlib

_BUCKET = "case-cache"


class SupabaseStorageCache:
    def __init__(self):
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.client: Client = create_client(url, key)

    def _object_path(self, case_id: str, filename: str) -> str:
        prefix = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
        return f"{prefix}/{filename}"

    def save_cache(self, case_id: str, filename: str, data: Any) -> str:
        path = self._object_path(case_id, filename)
        print(f"[SUPABASE CACHE] save {path}")

        # 🔑 1) binary vs text/json 분기
        if isinstance(data, (bytes, bytearray)):
            return self._save_binary(path, data)
        else:
            return self._save_text(path, data)
    
    def _save_binary(self, path: str, data: bytes) -> str:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            self.client.storage.from_(_BUCKET).upload(
                path,
                tmp_path,
                {"upsert": "true"},
            )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        return path

    def _save_text(self, path: str, data: Any) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            delete=False,
        ) as tmp:
            if isinstance(data, str):
                tmp.write(data)
            else:
                json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name

        try:
            self.client.storage.from_(_BUCKET).upload(
                path,
                tmp_path,
                {"upsert": "true"},
            )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        return path
    
    def load_cache(self, case_id: str, filename: str) -> Any | None:
        path = self._object_path(case_id, filename)

        try:
            res = self.client.storage.from_(_BUCKET).download(path)
        except Exception:
            return None

        if not res:
            return None

        # 🔑 확장자로 판단
        if filename.endswith(".json"):
            return json.loads(res.decode("utf-8"))
        elif filename.endswith(".md"):
            return res.decode("utf-8")
        else:
            return res  # binary