from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from openlibrary_scraper.domain.models.search_result import SearchPage
from openlibrary_scraper.infrastructure.openlibrary.response_parser import parse_search_page
from openlibrary_scraper.utils.errors import SearchError
from openlibrary_scraper.utils.retry_policy import backoff_seconds


LOGGER = logging.getLogger(__name__)
SEARCH_URL = "https://openlibrary.org/search.json"


class OpenLibrarySearchClient:
    def __init__(
        self,
        proxy_url: str,
        user_agent: str,
        timeout: int,
        max_retries: int,
        session: requests.Session | None = None,
    ) -> None:
        self.proxy_map = {"http": proxy_url, "https": proxy_url}
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": user_agent})
        self.request_count = 0
        self.proxy_stats = {
            "provider_batches": 0,
            "provider_http_requests": 0,
            "provided": 1,
            "validated": 0,
            "failed": 0,
            "rotations": 0,
        }

    def search_page(self, query: str, limit: int, offset: int) -> SearchPage:
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                response = self.session.get(
                    SEARCH_URL,
                    params={
                        "q": query,
                        "fields": "key,title,author_name,publisher,publish_year,language,subject",
                        "limit": limit,
                        "offset": offset,
                    },
                    proxies=self.proxy_map,
                    timeout=self.timeout,
                )
                if response.status_code == 407:
                    self.proxy_stats["failed"] += 1
                    raise SearchError(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD."
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise SearchError(
                            f"Open Library returned retryable HTTP {response.status_code} "
                            f"after {attempt + 1} attempts."
                        )
                    time.sleep(backoff_seconds(attempt, response.headers.get("Retry-After")))
                    continue
                if response.status_code >= 400:
                    raise SearchError(
                        f"Open Library returned non-retryable HTTP {response.status_code}."
                    )
                try:
                    payload: Any = response.json()
                except ValueError as exc:
                    raise SearchError("Open Library returned invalid JSON.") from exc
                return parse_search_page(payload)
            except SearchError:
                raise
            except requests.RequestException as exc:
                self.proxy_stats["failed"] += 1
                if isinstance(exc, requests.exceptions.ProxyError) and (
                    getattr(getattr(exc, "response", None), "status_code", None) == 407
                    or re.search(r"\b407\b", str(exc))
                ):
                    raise SearchError(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD."
                    ) from exc
                if attempt >= self.max_retries:
                    if isinstance(exc, requests.exceptions.Timeout):
                        message = (
                            f"Node4 proxy request timed out after {attempt + 1} attempts "
                            f"({self.timeout} seconds per attempt)."
                        )
                    elif isinstance(exc, requests.exceptions.ProxyError):
                        message = (
                            f"Node4 proxy connection failed after {attempt + 1} attempts."
                        )
                    elif isinstance(exc, requests.exceptions.ConnectionError):
                        message = (
                            f"Open Library connection through the Node4 proxy failed "
                            f"after {attempt + 1} attempts."
                        )
                    else:
                        message = (
                            f"Open Library request through the Node4 proxy failed "
                            f"after {attempt + 1} attempts ({type(exc).__name__})."
                        )
                    raise SearchError(
                        message
                    ) from exc
                delay = backoff_seconds(attempt)
                LOGGER.warning(
                    "Search request through the Node4 proxy failed; retrying in %.1f seconds.",
                    delay,
                )
                time.sleep(delay)
        raise SearchError("Open Library search exhausted its retry budget.")

    def get_json(
        self,
        url: str,
        params: dict[str, int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=self.proxy_map,
                    timeout=self.timeout,
                )
                if response.status_code == 407:
                    self.proxy_stats["failed"] += 1
                    raise SearchError(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD."
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise SearchError(
                            f"Open Library returned HTTP {response.status_code} "
                            f"after {attempt + 1} attempts for a details request."
                        )
                    time.sleep(backoff_seconds(attempt, response.headers.get("Retry-After")))
                    continue
                if response.status_code == 404:
                    raise SearchError(f"Open Library record was not found: {url}")
                if response.status_code >= 400:
                    raise SearchError(
                        f"Open Library returned non-retryable HTTP {response.status_code} "
                        f"for a details request."
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise SearchError("Open Library returned invalid JSON for a details request.") from exc
            except SearchError:
                raise
            except requests.RequestException as exc:
                self.proxy_stats["failed"] += 1
                if isinstance(exc, requests.exceptions.ProxyError) and (
                    getattr(getattr(exc, "response", None), "status_code", None) == 407
                    or re.search(r"\b407\b", str(exc))
                ):
                    raise SearchError(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD."
                    ) from exc
                if attempt >= self.max_retries:
                    if isinstance(exc, requests.exceptions.Timeout):
                        message = (
                            f"Node4 proxy request timed out after {attempt + 1} attempts "
                            f"({self.timeout} seconds per attempt)."
                        )
                    elif isinstance(exc, requests.exceptions.ProxyError):
                        message = (
                            f"Node4 proxy connection failed after {attempt + 1} attempts."
                        )
                    else:
                        message = (
                            f"Open Library details request through the Node4 proxy failed "
                            f"after {attempt + 1} attempts ({type(exc).__name__})."
                        )
                    raise SearchError(message) from exc
                delay = backoff_seconds(attempt)
                LOGGER.warning(
                    "Open Library details request through the Node4 proxy failed; "
                    "retrying in %.1f seconds.",
                    delay,
                )
                time.sleep(delay)
        raise SearchError("Open Library details request exhausted its retry budget.")
