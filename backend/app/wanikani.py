"""WaniKani client used to deduplicate vocabulary the learner already knows.

Assignments at SRS stage 5 ("Guru I") or above are treated as known. Their
subject ids are resolved to the actual Japanese characters, and any Anki card
whose expression matches one of them is dropped before it reaches the user.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# WaniKani's collection endpoints page at 500 (assignments) / 1000 (subjects).
_SUBJECT_ID_BATCH = 800
_CACHE_TTL_SECONDS = 15 * 60


class WaniKaniError(RuntimeError):
    """Raised when the WaniKani API cannot be queried."""


class WaniKaniClient:
    """Minimal async WaniKani v2 client with a short-lived in-process cache."""

    def __init__(self, token: str = "", api_base: str = "", known_srs_stage: int = 5) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.known_srs_stage = known_srs_stage
        self._cache: set[str] | None = None
        self._cache_time = 0.0
        self._lock = asyncio.Lock()

    def configure(self, token: str, api_base: str, known_srs_stage: int) -> None:
        """Point the client at the current settings.

        Changing the token drops the cached vocabulary: it belongs to whoever
        the old token identified, so serving it to a new account would filter
        against a stranger's progress.
        """
        if token != self.token:
            self._cache = None
            self._cache_time = 0.0
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.known_srs_stage = known_srs_stage

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def get_known_vocabulary(self, *, force_refresh: bool = False) -> set[str]:
        """Return every vocabulary word the learner has at Guru or above."""
        if not self.enabled:
            return set()

        async with self._lock:
            fresh = self._cache is not None and time.time() - self._cache_time < _CACHE_TTL_SECONDS
            if fresh and not force_refresh:
                return self._cache or set()

            subject_ids = await self._fetch_known_subject_ids()
            words = await self._fetch_subject_characters(subject_ids)
            self._cache = words
            self._cache_time = time.time()
            return words

    # --- internals -------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Wanikani-Revision": "20170710",
        }

    async def _fetch_known_subject_ids(self) -> list[int]:
        stages = ",".join(str(stage) for stage in range(self.known_srs_stage, 10))
        url: str | None = f"{self.api_base}/assignments"
        params: dict[str, str] | None = {
            "subject_types": "vocabulary,kana_vocabulary",
            "srs_stages": stages,
            "hidden": "false",
        }

        subject_ids: list[int] = []
        async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
            while url:
                payload = await self._get_json(client, url, params)
                for item in payload.get("data", []):
                    subject_id = (item.get("data") or {}).get("subject_id")
                    if isinstance(subject_id, int):
                        subject_ids.append(subject_id)
                # `next_url` already carries the query string.
                url = (payload.get("pages") or {}).get("next_url")
                params = None

        return subject_ids

    async def _fetch_subject_characters(self, subject_ids: list[int]) -> set[str]:
        if not subject_ids:
            return set()

        words: set[str] = set()
        async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
            for start in range(0, len(subject_ids), _SUBJECT_ID_BATCH):
                batch = subject_ids[start : start + _SUBJECT_ID_BATCH]
                url: str | None = f"{self.api_base}/subjects"
                params: dict[str, str] | None = {
                    "ids": ",".join(str(i) for i in batch),
                    "hidden": "false",
                }
                while url:
                    payload = await self._get_json(client, url, params)
                    for item in payload.get("data", []):
                        characters = (item.get("data") or {}).get("characters")
                        if characters:
                            words.add(characters)
                    url = (payload.get("pages") or {}).get("next_url")
                    params = None

        return words

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, str] | None,
    ) -> dict[str, Any]:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise WaniKaniError("WaniKani rejected the API token (401).") from exc
            if status == 429:
                raise WaniKaniError("WaniKani rate limit reached (429).") from exc
            raise WaniKaniError(f"WaniKani request failed with HTTP {status}.") from exc
        except httpx.HTTPError as exc:
            raise WaniKaniError(f"Could not reach WaniKani: {exc}") from exc
