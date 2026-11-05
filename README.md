# Open Library Search Scraper

## Purpose and scope

The scraper reads book metadata from Excel, searches the Open Library Search API through the configured Node4 proxy, retrieves paginated Work results, scores candidates, and fetches Work details and paginated Editions for selected matches. It exports candidate scores, selected matches, a run report, and one consolidated metadata record per input book.

## Architecture

`main.py` loads settings and starts `processing/pipeline.py`. The package has three focused areas: configuration, Open Library requests, and post-response processing. See [docs/architecture.md](docs/architecture.md) and [docs/data_flow.md](docs/data_flow.md).

## Project layout

```text
main.py
openlibrary_scraper/
  config/
    settings.py       Environment and scraper configuration
  requests/
    client.py         Search, Work and Edition requests with proxy/retries
    pagination.py     Search API pagination and query fallback
    response_parser.py
    retry.py
  processing/
    books.py          Excel input, book records and text normalization
    matching.py       Candidate scoring and selection
    metadata.py       Edition selection and final metadata mapping
    pipeline.py       End-to-end workflow and orchestration
    storage.py        Checkpoints and cached raw API responses
    export.py         Excel, CSV and JSON output generation
  errors.py           Shared scraper exceptions
data/
  input/books.xlsx
  output/               Created at runtime
tests/
  unit/
  integration/
docs/
```

## Setup

Python 3.10 or later is required.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The supplied workbook is copied to `data/input/books.xlsx`; replace that copy when using a different input. The original workbook in the workspace root is not modified.

## Proxy configuration

The local `.env` file is ignored by Git. Configure all four Node4 values there:

```env
NODE4_HOST=
NODE4_PORT=
NODE4_USERNAME=
NODE4_PASSWORD=
```

All four values are required before the scraper starts. The username and password are URL-encoded when the authenticated proxy URL is built. Both HTTP and HTTPS requests use this single configured proxy; there is no direct-connection fallback.
`OPENLIBRARY_BASE_URL` optionally changes the API base URL (default `https://openlibrary.org`), and `EDITION_PAGE_SIZE` configures Edition page size (default `100`).

## Run

```powershell
python main.py
```

Resume is enabled by default. To start a new run without reusing the current checkpoint:

```powershell
python main.py --fresh
```

The scraper reuses the configured Node4 proxy for sequential Open Library requests. Connection failures and timeouts use bounded retries through the same proxy; proxy authentication failures are reported as HTTP 407. After candidate matching, it retrieves the selected Work details and Editions, then chooses the most suitable Edition using publisher, year, language, and optional page-count criteria.

## Input validation

The active worksheet must contain `book_title`, `author`, `target_publisher`, `target_publication_year`, `target_language_code`, and `subject`. Empty rows are skipped with a warning. Non-empty rows, including invalid or duplicate records, are retained in the best-match output and recorded with validation errors. The source values are preserved; normalized whitespace/text is used for matching.

## Pagination and recovery

The Search API's `numFound`, exactness flag, `start`, and `docs` drive pagination. Requests continue using actual response starts and received record counts, deduplicate valid Work IDs, and stop at `MAX_PAGES`. Missing/inconsistent totals, premature empty pages, and count changes are recorded as uncertainty or errors. Each raw page is saved before its checkpoint advances. Checkpoints allow already saved pages to be reused after interruption.

## Matching

Each candidate gets independent title, author, publisher, year, and language scores weighted 35%, 30%, 20%, 10%, and 5%. Subject similarity is a supporting signal only. Scores are based on normalized text and RapidFuzz similarity; configurable default thresholds are 0.85 for `HIGH_CONFIDENCE` and 0.60 for `POSSIBLE_MATCH`. A weaker top candidate becomes `NO_MATCH`; close top candidates are marked `POSSIBLE_MATCH` with an ambiguity explanation. Search-level publisher/year/language are edition-aggregated evidence, not proof of an Edition match. See [docs/matching_logic.md](docs/matching_logic.md).

## Outputs

The following are written under `data/output/`:

- `raw_search/<run_id>/<book_id>/query_<hash>/page_NNN.json` and per-book `metadata.json`
- `search_results.xlsx` and `search_results.csv` (all Work candidates and scores)
- `best_work_matches.xlsx` and `best_work_matches.csv` (one row per input book)
- `execution_report.json` (counts, status, errors, warnings, proxy statistics; no credentials)
- `final_books.json` (one consolidated metadata object per input book, in input order)
- `checkpoints/current.json`
- `raw_search/<run_id>/<book_id>/details/` (cached Work details and Editions API responses)

Structured output files are updated on each successful run; raw responses are isolated by run ID.

## Tests

```powershell
python -m pytest
```

The suite uses mocked search responses and does not require a live proxy. Tests do not contact external services.

## Troubleshooting

- **Missing proxy configuration:** set each of `NODE4_HOST`, `NODE4_PORT`, `NODE4_USERNAME`, and `NODE4_PASSWORD` in `.env`.
- **Proxy authentication failure (HTTP 407):** verify the Node4 username and password.
- **Proxy connection failure or timeout:** verify Node4 connectivity and the configured host and port.
- **Missing/invalid workbook fields:** correct a copy of the workbook; the original is never changed.
- **Incomplete search:** inspect per-book raw metadata and the execution report; rerun to resume, or pass `--fresh` to begin a clean run.
