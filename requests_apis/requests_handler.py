from __future__ import annotations

import logging
import random
import re
import time
import traceback
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import requests

SEARCH_FIELDS = "key,title,author_name,publisher,publish_year,language,subject"


def build_search_queries(title: str, author: str) -> tuple[str, ...]:
    def quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').strip()

    title = quote(title)
    if not title:
        raise ValueError(f"The title parameter must not be empty; received {title!r}.")
    author = quote(author)
    primary = f'title:"{title}" AND author:"{author}"' if author else f'title:"{title}"'
    fallback = f'title:"{title}"'
    return (primary,) if primary == fallback else (primary, fallback)


def prepare_search_params(query: str, limit: int, offset: int) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"Search query parameter must be a non-empty string; received {query!r}.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(
            f"Search limit parameter must be a positive integer; received {limit!r}."
        )
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(
            f"Search offset parameter must be a non-negative integer; received {offset!r}."
        )

    return {
        "q": query,
        "fields": SEARCH_FIELDS,
        "limit": limit,
        "offset": offset,
    }


_WORK_KEY = re.compile(r"^/works/(OL\d+W)$")


@dataclass(frozen=True)
class Work:
    work_id: str | None
    work_url: str | None
    title: str
    authors: tuple[str, ...]
    publishers: tuple[str, ...]
    publication_years: tuple[int, ...]
    languages: tuple[str, ...]
    subjects: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class SearchPage:
    num_found: int | None
    num_found_exact: bool | None
    start: int | None
    docs: tuple[Work, ...]
    raw: dict[str, Any]


def normalized_values(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,) if values else ()
    if isinstance(values, Iterable):
        return tuple(
            str(value)
            for value in values
            if value is not None and str(value).strip()
        )
    return ()


def _years(values: object) -> tuple[int, ...]:
    results: set[int] = set()
    for value in normalized_values(values):
        try:
            year = int(value)
        except Exception as exc:
            traceback.print_exc()
            continue
        if 0 < year <= 9999:
            results.add(year)
    return tuple(sorted(results))


def parse_work(payload: object) -> Work:
    if not isinstance(payload, dict):
        raise Exception(
            "Open Library Work payload must be a dictionary; "
            f"received payload={payload!r}."
        )
    key = payload.get("key")
    match = _WORK_KEY.fullmatch(key) if isinstance(key, str) else None
    work_id = match.group(1) if match else None
    return Work(
        work_id=work_id,
        work_url=f"https://openlibrary.org/works/{work_id}" if work_id else None,
        title=next(iter(normalized_values(payload.get("title"))), ""),
        authors=normalized_values(payload.get("author_name")),
        publishers=normalized_values(payload.get("publisher")),
        publication_years=_years(payload.get("publish_year")),
        languages=normalized_values(payload.get("language")),
        subjects=normalized_values(payload.get("subject")),
        raw=payload,
    )


def parse_search_page(payload: object) -> SearchPage:
    if not isinstance(payload, dict):
        raise Exception(
            f"Open Library Search payload must be a dictionary; received payload={payload!r}."
        )
    docs = payload.get("docs")
    if not isinstance(docs, list):
        raise Exception(
            "Open Library Search response docs parameter must be a list; "
            f"received docs={docs!r}."
        )

    num_found_value = payload.get("numFound", payload.get("num_found"))
    try:
        num_found = int(num_found_value) if num_found_value is not None else None
    except Exception as exc:
        traceback.print_exc()
        num_found = None
    exact_value = payload.get("numFoundExact")
    num_found_exact = exact_value if isinstance(exact_value, bool) else None
    start_value = payload.get("start")
    try:
        start = int(start_value) if start_value is not None else None
    except Exception as exc:
        traceback.print_exc()
        start = None
    works = tuple(parse_work(doc) for doc in docs)
    return SearchPage(num_found, num_found_exact, start, works, payload)


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except Exception as exc:
        traceback.print_exc()
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except Exception as exc:
            traceback.print_exc()
            return None


def backoff_seconds(attempt: int, retry_after: str | None = None) -> float:
    specified = retry_after_seconds(retry_after)
    if specified is not None:
        return specified
    return min(60.0, (2**attempt) + random.uniform(0, 0.5))


LOGGER = logging.getLogger(__name__)
WORK_ID = re.compile(r"^OL\d+W$")
SEARCH_PATH = "/search.json"
DETAIL_HEADERS = {
    "User-Agent": "PostmanRuntime/2.12.2",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def work_details_url(
    work_id: str, api_base_url: str = "https://openlibrary.org"
) -> str:
    if not WORK_ID.fullmatch(work_id):
        raise ValueError(f"Invalid work_id parameter: {work_id!r}.")
    return f"{api_base_url.rstrip('/')}/works/{work_id}.json"


def editions_url(
    work_id: str, api_base_url: str = "https://openlibrary.org"
) -> str:
    if not WORK_ID.fullmatch(work_id):
        raise ValueError(f"Invalid work_id parameter: {work_id!r}.")
    return f"{api_base_url.rstrip('/')}/works/{work_id}/editions.json"


class OpenLibrarySearchClient:
    def __init__(
        self,
        proxy_url: str,
        user_agent: str,
        timeout: int,
        max_retries: int,
        session: requests.Session | None = None,
        api_base_url: str = "https://openlibrary.org",
        edition_page_size: int = 100,
    ) -> None:
        self.proxy_map = {"http": proxy_url, "https": proxy_url}
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_base_url = api_base_url.rstrip("/")
        self.edition_page_size = edition_page_size
        self._owns_session = session is None
        self.session = session if session is not None else requests.Session()
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

    def __enter__(self) -> OpenLibrarySearchClient:
        return self

    def __exit__(self, *_: object) -> None:
        if self._owns_session:
            self.session.close()

    def search_page(self, query: str, limit: int, offset: int) -> SearchPage:
        request_context = (
            f"query={query!r}, limit={limit!r}, offset={offset!r}, timeout={self.timeout}"
        )
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                LOGGER.info(
                    "Sending Search API request: query=%r offset=%s limit=%s attempt=%s/%s.",
                    query,
                    offset,
                    limit,
                    attempt + 1,
                    self.max_retries + 1,
                )
                response = self.session.get(
                    f"{self.api_base_url}{SEARCH_PATH}",
                    params=prepare_search_params(query, limit, offset),
                    proxies=self.proxy_map,
                    timeout=self.timeout,
                )
                LOGGER.info("Search API response received: HTTP %s.", response.status_code)
                if response.status_code == 407:
                    self.proxy_stats["failed"] += 1
                    raise Exception(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD; "
                        f"request parameters: {request_context}."
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise Exception(
                            f"Open Library returned retryable HTTP {response.status_code} "
                            f"after {attempt + 1} attempts."
                        )
                    delay = backoff_seconds(attempt, response.headers.get("Retry-After"))
                    LOGGER.warning(
                        "Search API returned retryable HTTP %s; retrying in %.1f seconds.",
                        response.status_code,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                if response.status_code >= 400:
                    raise Exception(
                        f"Open Library returned non-retryable HTTP {response.status_code}."
                    )
                try:
                    payload: Any = response.json()
                except Exception as exc:
                    traceback.print_exc()
                    raise Exception("Open Library returned invalid JSON.") from exc
                page = parse_search_page(payload)
                LOGGER.info(
                    "Search page parsed: numFound=%s start=%s documents=%s.",
                    page.num_found if page.num_found is not None else "unknown",
                    page.start if page.start is not None else "unknown",
                    len(page.docs),
                )
                return page
            except Exception as exc:
                traceback.print_exc()
                if not isinstance(exc, requests.RequestException):
                    raise Exception(
                        f"Search API request failed; parameters: {request_context}; {exc}"
                    ) from exc
                self.proxy_stats["failed"] += 1
                if isinstance(exc, requests.exceptions.ProxyError) and (
                    getattr(getattr(exc, "response", None), "status_code", None) == 407
                    or re.search(r"\b407\b", str(exc))
                ):
                    raise Exception(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD."
                    ) from exc
                if attempt >= self.max_retries:
                    if isinstance(exc, requests.exceptions.Timeout):
                        message = (
                            f"Node4 proxy request timed out after {attempt + 1} attempts "
                            f"({self.timeout} seconds per attempt); "
                            f"query={query!r}, limit={limit}, offset={offset}."
                        )
                    elif isinstance(exc, requests.exceptions.ProxyError):
                        message = (
                            f"Node4 proxy connection failed after {attempt + 1} attempts; "
                            f"query={query!r}, limit={limit}, offset={offset}."
                        )
                    elif isinstance(exc, requests.exceptions.ConnectionError):
                        message = (
                            f"Open Library connection through the Node4 proxy failed "
                            f"after {attempt + 1} attempts; "
                            f"query={query!r}, limit={limit}, offset={offset}."
                        )
                    else:
                        message = (
                            f"Open Library request through the Node4 proxy failed "
                            f"after {attempt + 1} attempts ({type(exc).__name__}); "
                            f"query={query!r}, limit={limit}, offset={offset}."
                        )
                    raise Exception(
                        message
                    ) from exc
                delay = backoff_seconds(attempt)
                LOGGER.warning(
                    "Search request through the Node4 proxy failed; retrying in %.1f seconds.",
                    delay,
                )
                time.sleep(delay)
        raise Exception(
            f"Open Library search exhausted its retry budget; parameters: {request_context}."
        )

    def get_work_details(self, work_id: str) -> Any:
        return self.get_json(
            work_details_url(work_id, self.api_base_url),
            headers=DETAIL_HEADERS,
        )

    def get_editions_page(self, work_id: str, offset: int) -> Any:
        return self.get_json(
            editions_url(work_id, self.api_base_url),
            params={"limit": self.edition_page_size, "offset": offset},
            headers=DETAIL_HEADERS,
        )

    def get_json(
        self,
        url: str,
        params: dict[str, int] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_context = (
            f"endpoint={urlsplit(url).path}, params={params or {}}, timeout={self.timeout}"
        )
        request_name = (
            "Edition page"
            if "/editions.json" in url
            else "Work details"
            if "/works/" in url and url.endswith(".json")
            else "Open Library details"
        )
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                LOGGER.info(
                    "Sending %s request: endpoint=%s params=%s attempt=%s/%s.",
                    request_name,
                    urlsplit(url).path,
                    params or {},
                    attempt + 1,
                    self.max_retries + 1,
                )
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=self.proxy_map,
                    timeout=self.timeout,
                )
                LOGGER.info(
                    "%s response received: HTTP %s.",
                    request_name,
                    response.status_code,
                )
                if response.status_code == 407:
                    self.proxy_stats["failed"] += 1
                    raise Exception(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD; "
                        f"request parameters: {request_context}."
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.max_retries:
                        raise Exception(
                            f"Open Library returned HTTP {response.status_code} "
                            f"after {attempt + 1} attempts for a details request."
                        )
                    delay = backoff_seconds(attempt, response.headers.get("Retry-After"))
                    LOGGER.warning(
                        "%s returned retryable HTTP %s; retrying in %.1f seconds.",
                        request_name,
                        response.status_code,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                if response.status_code == 404:
                    raise Exception(
                        f"Open Library record was not found: {urlsplit(url).path}"
                    )
                if response.status_code >= 400:
                    raise Exception(
                        f"Open Library returned non-retryable HTTP {response.status_code} "
                        f"for a details request."
                    )
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        LOGGER.debug(
                            "%s JSON object received with keys: %s.",
                            request_name,
                            ", ".join(sorted(payload.keys())),
                        )
                    return payload
                except Exception as exc:
                    traceback.print_exc()
                    raise Exception(
                        "Open Library returned invalid JSON for a details request."
                    ) from exc
            except Exception as exc:
                traceback.print_exc()
                if not isinstance(exc, requests.RequestException):
                    raise Exception(
                        f"{request_name} request failed; parameters: {request_context}; {exc}"
                    ) from exc
                self.proxy_stats["failed"] += 1
                if isinstance(exc, requests.exceptions.ProxyError) and (
                    getattr(getattr(exc, "response", None), "status_code", None) == 407
                    or re.search(r"\b407\b", str(exc))
                ):
                    raise Exception(
                        "Node4 proxy authentication failed (HTTP 407); "
                        "check NODE4_USERNAME and NODE4_PASSWORD."
                    ) from exc
                if attempt >= self.max_retries:
                    if isinstance(exc, requests.exceptions.Timeout):
                        message = (
                            f"Node4 proxy request timed out after {attempt + 1} attempts "
                            f"({self.timeout} seconds per attempt); "
                            f"endpoint={urlsplit(url).path}, params={params or {}}, "
                            f"timeout={self.timeout}."
                        )
                    elif isinstance(exc, requests.exceptions.ProxyError):
                        message = (
                            f"Node4 proxy connection failed after {attempt + 1} attempts; "
                            f"endpoint={urlsplit(url).path}, params={params or {}}, "
                            f"timeout={self.timeout}."
                        )
                    else:
                        message = (
                            f"Open Library details request through the Node4 proxy failed "
                            f"after {attempt + 1} attempts ({type(exc).__name__}); "
                            f"endpoint={urlsplit(url).path}, params={params or {}}, "
                            f"timeout={self.timeout}."
                        )
                    raise Exception(message) from exc
                delay = backoff_seconds(attempt)
                LOGGER.warning(
                    "Open Library details request through the Node4 proxy failed; "
                    "retrying in %.1f seconds.",
                    delay,
                )
                time.sleep(delay)
        raise Exception(
            f"Open Library details request exhausted its retry budget; "
            f"parameters: {request_context}."
        )


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    queries: tuple[str, ...]
    works: tuple[Work, ...]
    expected_total: int | None
    retrieved_total: int
    page_count: int
    pagination_status: str
    complete: bool
    uncertainty: tuple[str, ...]
    errors: tuple[str, ...]


class PaginationService:
    def __init__(
        self,
        client: OpenLibrarySearchClient,
        page_size: int,
        max_pages: int,
    ) -> None:
        self.client = client
        self.page_size = page_size
        self.max_pages = max_pages

    def _get_page(
        self,
        book_id: str,
        query: str,
        page_index: int,
        offset: int,
    ) -> SearchPage:
        LOGGER.info(
            "%s requesting Search page %s at offset %s.",
            book_id,
            page_index + 1,
            offset,
        )
        return self.client.search_page(query, self.page_size, offset)

    def _retrieve_query(
        self, book_id: str, query: str
    ) -> tuple[list[Work], int | None, int, int, bool, list[str], list[str]]:
        expected: int | None = None
        works_by_id: dict[str, Work] = {}
        anonymous_works: list[Work] = []
        uncertainty: list[str] = []
        errors: list[str] = []
        retrieved = 0
        page_count = 0
        offset = 0

        for page_index in range(self.max_pages):
            try:
                page = self._get_page(book_id, query, page_index, offset)
            except Exception as exc:
                traceback.print_exc()
                errors.append(f"{type(exc).__name__}: {exc}")
                break
            page_count += 1

            LOGGER.info(
                "%s page %s: requested offset %s, received %s works, expected total %s.",
                book_id,
                page_index + 1,
                offset,
                len(page.docs),
                page.num_found if page.num_found is not None else "unknown",
            )
            if expected is not None and page.num_found is not None and expected != page.num_found:
                uncertainty.append(
                    f"numFound changed from {expected} to {page.num_found}"
                )
            if page.num_found is not None:
                expected = page.num_found
            else:
                uncertainty.append("Open Library response omitted numFound")
            if page.num_found_exact is False:
                uncertainty.append("Open Library marked numFound as inexact")
            start_mismatch = page.start is not None and page.start != offset
            if start_mismatch:
                uncertainty.append(
                    f"response start {page.start} differed from requested offset {offset}"
                )

            actual_start = page.start if page.start is not None else offset
            retrieved += len(page.docs)
            for work in page.docs:
                self._add_work(work, works_by_id, anonymous_works)
            LOGGER.info(
                "%s page %s returned %s documents; %s unique Work IDs; numFound=%s exact=%s.",
                book_id,
                page_index + 1,
                len(page.docs),
                len(works_by_id),
                expected if expected is not None else "unknown",
                page.num_found_exact if page.num_found_exact is not None else "unknown",
            )

            if start_mismatch:
                errors.append("Inconsistent response start prevents completeness validation")
                LOGGER.warning("%s pagination stopped: response start did not match offset.", book_id)
                break

            next_offset = actual_start + len(page.docs)
            LOGGER.info(
                "%s page %s next offset is %s.",
                book_id,
                page_index + 1,
                next_offset,
            )
            if not page.docs:
                if expected is not None and next_offset < expected:
                    uncertainty.append("empty page before expected result total")
                    errors.append("Search ended before all expected results were retrieved")
                LOGGER.info("%s pagination stopped: response contained no documents.", book_id)
                break
            if page.num_found_exact is True and expected is not None and next_offset >= expected:
                LOGGER.info(
                    "%s pagination complete: reached exact expected total %s.",
                    book_id,
                    expected,
                )
                break
            if page.num_found_exact is None and len(page.docs) < self.page_size:
                if expected is not None and next_offset < expected:
                    uncertainty.append("short page before expected result total")
                    errors.append("Search ended before all expected results were retrieved")
                LOGGER.info(
                    "%s pagination stopped: response was shorter than the page size.",
                    book_id,
                )
                break

            offset = next_offset
            LOGGER.info(
                "%s pagination continues with page %s at offset %s.",
                book_id,
                page_index + 2,
                offset,
            )
        else:
            uncertainty.append(f"maximum page count ({self.max_pages}) reached")
            errors.append("Maximum page count reached before completion")
            LOGGER.warning(
                "%s pagination stopped incomplete after maximum %s pages.",
                book_id,
                self.max_pages,
            )

        return (
            list(works_by_id.values()) + anonymous_works,
            expected,
            retrieved,
            page_count,
            not errors,
            uncertainty,
            errors,
        )

    @staticmethod
    def _add_work(
        work: Work, works_by_id: dict[str, Work], anonymous_works: list[Work]
    ) -> None:
        if work.work_id:
            works_by_id.setdefault(work.work_id, work)
        else:
            anonymous_works.append(work)

    def retrieve(self, book_id: str, queries: tuple[str, ...]) -> RetrievalResult:
        all_works: list[Work] = []
        uncertainty: list[str] = []
        errors: list[str] = []
        expected: int | None = None
        retrieved = 0
        page_count = 0
        complete = False
        successful_query = queries[0]

        for query in queries:
            successful_query = query
            LOGGER.info("%s searching with query %r.", book_id, query)
            (
                works,
                expected,
                retrieved,
                pages,
                complete,
                notes,
                failures,
            ) = self._retrieve_query(book_id, query)
            all_works = works
            uncertainty.extend(notes)
            errors.extend(failures)
            page_count += pages
            if works or query == queries[-1]:
                if not works and query != queries[-1]:
                    LOGGER.info(
                        "%s primary search returned no Works; trying fallback query.",
                        book_id,
                    )
                break

        status = "COMPLETE" if complete else ("INCOMPLETE" if errors or uncertainty else "UNKNOWN")
        return RetrievalResult(
            successful_query,
            queries,
            tuple(all_works),
            expected,
            retrieved,
            page_count,
            status,
            complete,
            tuple(dict.fromkeys(uncertainty)),
            tuple(errors),
        )
