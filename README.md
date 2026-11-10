# Open Library Search Scraper

## Purpose and scope

The scraper reads book metadata from Excel, searches the Open Library Search API through the configured Node4 proxy, retrieves paginated Work results, scores candidates, and fetches Work details and paginated Editions for selected matches. It exports candidate scores, selected matches, a run report, and one consolidated metadata record per input book.

## Architecture

`main.py` loads settings and starts the book workflow. Production code is grouped into three folders by responsibility. See [docs/architecture.md](docs/architecture.md), [docs/data_flow.md](docs/data_flow.md), and [docs/matching_logic.md](docs/matching_logic.md).

## Project layout

```text
main.py
config/
  settings.py         Environment validation and API/matching settings
requests_apis/
  requests_handler.py Proxied HTTP requests, response parsing, pagination
processing/
  data_processor.py   Workbook, matching, metadata, sequential workflow
  storage.py          Atomic saving of final_books.json
data/
  input/books.xlsx
  output/               Created at runtime
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
Use `.env.example` as the variable-name template; put your private Node4 values in `.env`, not in the example file.
`OPENLIBRARY_BASE_URL` optionally changes the API base URL (default `https://openlibrary.org`), and `EDITION_PAGE_SIZE` configures Edition page size (default `100`).

## Run

```powershell
python main.py
```

The scraper reuses the configured Node4 proxy for sequential Open Library requests. Connection failures and timeouts use bounded retries through the same proxy; proxy authentication failures are reported as HTTP 407. Logs trace each API page, matching score, metadata request, and save operation without printing proxy credentials. The program writes only `data/output/final_books.json`; API responses and other intermediate values exist only in memory.

## Input validation

The active worksheet must contain `book_title`, `author`, `target_publisher`, `target_publication_year`, `target_language_code`, and `subject`. Empty rows are skipped with a warning. Non-empty rows, including invalid or duplicate records, are retained in the best-match output and recorded with validation errors. The source values are preserved; normalized whitespace/text is used for matching.

## Pagination and recovery

The Search API's `numFound`, exactness flag, `start`, and `docs` drive pagination. Requests continue using actual response starts and received record counts, deduplicate valid Work IDs, and stop at `MAX_PAGES`. Missing/inconsistent totals, premature empty pages, and count changes are recorded as uncertainty or errors. Responses stay in memory and are discarded after the current book is processed.

## Matching

Each candidate gets independent title, author, publisher, year, and language scores weighted 35%, 30%, 20%, 10%, and 5%. Subject similarity is a supporting signal only. Scores are based on normalized text and RapidFuzz similarity; configurable default thresholds are 0.85 for `HIGH_CONFIDENCE` and 0.60 for `POSSIBLE_MATCH`. A weaker top candidate becomes `NO_MATCH`; close top candidates are marked `POSSIBLE_MATCH` with an ambiguity explanation. Search-level publisher/year/language are edition-aggregated evidence, not proof of an Edition match. See [docs/matching_logic.md](docs/matching_logic.md).

## Outputs

Only `final_books.json` is written under `data/output/`. It contains one
consolidated metadata object per input book in input order. It is atomically
replaced after each processed book, so completed results survive an
interruption. Search pages, Work details, Edition pages, candidate scores,
checkpoints, and reports are not saved.

## Troubleshooting

- **Missing proxy configuration:** set each of `NODE4_HOST`, `NODE4_PORT`, `NODE4_USERNAME`, and `NODE4_PASSWORD` in `.env`.
- **Proxy authentication failure (HTTP 407):** verify the Node4 username and password.
- **Proxy connection failure or timeout:** verify Node4 connectivity and the configured host and port.
- **Excel output is open/locked:** progressive CSV/JSON outputs continue to update and the report records the workbook error. Close the output workbook and rerun to refresh its XLSX file.
- **Missing/invalid workbook fields:** correct a copy of the workbook; the original is never changed.
- **Incomplete search:** inspect the terminal logs. A rerun starts from the first input book because intermediate responses and checkpoints are intentionally not stored.
