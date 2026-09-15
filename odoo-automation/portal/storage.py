"""Where uploaded files live: the local disk, or Supabase Storage.

The portal keeps every night-audit pack and every invoice, because they are the evidence
behind an accounting entry.  On one container with a persistent disk, the disk is fine.  On a
host that replaces the container, or once more than one copy runs, they belong in object
storage instead.

    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY=...          # the service role key, kept on the host, never in git
    SUPABASE_BUCKET=night-audit       # created in the dashboard, private

Set those three and uploads go to Supabase; leave them unset and they go to the data
directory exactly as before.

A "locator" is what gets written to the database: an absolute path locally, or
`supabase://bucket/key` remotely.  Parsers need a real file, so `local_path()` downloads a
remote object to a temporary file and caches it for the life of the process.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

SUPABASE_SCHEME = "supabase://"


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, key: str, data: bytes) -> str:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)

    def read(self, locator: str) -> bytes:
        return Path(locator).read_bytes()

    def local_path(self, locator: str) -> Path:
        return Path(locator)

    def exists(self, locator: str) -> bool:
        return Path(locator).exists()

    def delete(self, locator: str) -> None:
        Path(locator).unlink(missing_ok=True)

    def describe(self) -> str:
        return f"local disk at {self.root}"


class SupabaseStorage:
    def __init__(self, url: str, key: str, bucket: str):
        self.base = url.rstrip("/")
        self.key = key
        self.bucket = bucket
        self._cache: dict[str, Path] = {}
        self._tmp = Path(tempfile.mkdtemp(prefix="portal-files-"))

    # -- helpers --------------------------------------------------------------
    def _object_url(self, key: str) -> str:
        return f"{self.base}/storage/v1/object/{self.bucket}/{key.lstrip('/')}"

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {"Authorization": f"Bearer {self.key}"}
        h.update(extra or {})
        return h

    @staticmethod
    def _key_of(locator: str) -> str:
        return locator[len(SUPABASE_SCHEME):].split("/", 1)[1]

    # -- interface ------------------------------------------------------------
    def save(self, key: str, data: bytes) -> str:
        import httpx
        import mimetypes
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        r = httpx.post(self._object_url(key), content=data, timeout=60,
                       headers=self._headers({"Content-Type": content_type, "x-upsert": "true"}))
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase upload failed ({r.status_code}): {r.text[:200]}")
        locator = f"{SUPABASE_SCHEME}{self.bucket}/{key.lstrip('/')}"
        path = self._tmp / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)              # keep it to hand; it is usually read straight back
        self._cache[locator] = path
        return locator

    def read(self, locator: str) -> bytes:
        return self.local_path(locator).read_bytes()

    def local_path(self, locator: str) -> Path:
        if not locator.startswith(SUPABASE_SCHEME):
            return Path(locator)            # written before storage moved; still on disk
        cached = self._cache.get(locator)
        if cached and cached.exists():
            return cached
        import httpx
        key = self._key_of(locator)
        r = httpx.get(self._object_url(key), headers=self._headers(), timeout=60)
        if r.status_code >= 300:
            raise FileNotFoundError(f"Supabase download failed ({r.status_code}) for {locator}")
        path = self._tmp / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        self._cache[locator] = path
        return path

    def exists(self, locator: str) -> bool:
        try:
            self.local_path(locator)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, locator: str) -> None:
        if not locator.startswith(SUPABASE_SCHEME):
            Path(locator).unlink(missing_ok=True)
            return
        import httpx
        httpx.delete(self._object_url(self._key_of(locator)), headers=self._headers(), timeout=30)
        cached = self._cache.pop(locator, None)
        if cached:
            cached.unlink(missing_ok=True)

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def describe(self) -> str:
        return f"Supabase Storage bucket '{self.bucket}' at {self.base}"


def build(data_dir: Path):
    """Supabase when all three variables are set, the local disk otherwise."""
    url, key, bucket = (os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY"),
                        os.environ.get("SUPABASE_BUCKET"))
    if url and key and bucket:
        return SupabaseStorage(url, key, bucket)
    return LocalStorage(data_dir)
