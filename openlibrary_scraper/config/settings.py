from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotenv import load_dotenv

from openlibrary_scraper.errors import ConfigurationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ConfigurationError(f"{name} must be true or false.")
    return normalized == "true"


def _int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        result = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if result < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}.")
    return result


@dataclass(frozen=True)
class Settings:
    project_root: Path
    proxy_url: str
    request_timeout: int
    max_retries: int
    page_size: int
    max_pages: int
    resume_enabled: bool
    start_fresh: bool
    log_level: str
    user_agent: str
    input_file: Path
    output_dir: Path
    high_confidence_threshold: float
    possible_match_threshold: float
    ambiguity_delta: float
    api_base_url: str = "https://openlibrary.org"
    edition_page_size: int = 100


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    proxy_variables = ("NODE4_HOST", "NODE4_PORT", "NODE4_USERNAME", "NODE4_PASSWORD")
    proxy_values = {name: os.getenv(name, "").strip() for name in proxy_variables}
    missing = [name for name, value in proxy_values.items() if not value]
    if missing:
        raise ConfigurationError(
            f"Missing required Node4 environment variable(s): {', '.join(missing)}."
        )

    port = _int("NODE4_PORT", 0)
    if port > 65535:
        raise ConfigurationError("NODE4_PORT must be between 1 and 65535.")

    host = proxy_values["NODE4_HOST"]
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"
    proxy_url = (
        f"http://{quote(proxy_values['NODE4_USERNAME'], safe='')}:"
        f"{quote(proxy_values['NODE4_PASSWORD'], safe='')}@{host}:{port}"
    )

    try:
        high_threshold = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.85"))
        possible_threshold = float(os.getenv("POSSIBLE_MATCH_THRESHOLD", "0.60"))
        ambiguity_delta = float(os.getenv("AMBIGUITY_DELTA", "0.03"))
    except ValueError as exc:
        raise ConfigurationError(
            "Match thresholds must be numeric values between 0 and 1."
        ) from exc
    valid_thresholds = 0 <= possible_threshold <= high_threshold <= 1
    valid_ambiguity_delta = 0 <= ambiguity_delta <= 1
    if not valid_thresholds or not valid_ambiguity_delta:
        raise ConfigurationError("Match thresholds must satisfy 0 <= possible <= high <= 1.")

    api_base_url = os.getenv(
        "OPENLIBRARY_BASE_URL", "https://openlibrary.org"
    ).rstrip("/")
    api_parts = urlsplit(api_base_url)
    if (
        api_parts.scheme not in {"http", "https"}
        or not api_parts.netloc
        or api_parts.query
        or api_parts.fragment
    ):
        raise ConfigurationError("OPENLIBRARY_BASE_URL must be an absolute HTTP(S) URL.")

    root = PROJECT_ROOT
    input_file = Path(os.getenv("INPUT_FILE", "data/input/books.xlsx"))
    output_dir = Path(os.getenv("OUTPUT_DIR", "data/output"))
    if not input_file.is_absolute():
        input_file = root / input_file
    if not output_dir.is_absolute():
        output_dir = root / output_dir

    return Settings(
        project_root=root,
        proxy_url=proxy_url,
        request_timeout=_int("REQUEST_TIMEOUT", 30),
        max_retries=_int("MAX_RETRIES", 3, 0),
        page_size=_int("PAGE_SIZE", 100),
        max_pages=_int("MAX_PAGES", 100),
        resume_enabled=_bool("RESUME_ENABLED", True),
        start_fresh=_bool("START_FRESH", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        user_agent=os.getenv(
            "USER_AGENT", "OpenLibrarySearchScraper/0.1 (contact: configure USER_AGENT)"
        ),
        input_file=input_file,
        output_dir=output_dir,
        high_confidence_threshold=high_threshold,
        possible_match_threshold=possible_threshold,
        ambiguity_delta=ambiguity_delta,
        api_base_url=api_base_url,
        edition_page_size=_int("EDITION_PAGE_SIZE", 100),
    )
