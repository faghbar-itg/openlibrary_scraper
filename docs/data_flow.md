# Data flow

1. `main.py` initializes logging, then `config/settings.py` loads `.env`, validates Node4 proxy settings and scraper options, and builds the authenticated proxy URL.
2. Read the active worksheet. Required headers are checked; original values, row numbers, duplicates, empty rows, and invalid values are handled explicitly.
3. Each book gets a title-and-author query plus a title-only fallback.
4. `requests_apis/requests_handler.py` configures the HTTP and HTTPS client to reuse the Node4 proxy. API URLs, timeouts, retries, and Edition page size come from settings. No proxy-provider requests or direct connections are used.
5. Request Search API pages sequentially and keep responses in memory. Continue until exact totals are met, the service ends the result set, or a safety/error condition is reached.
6. Deduplicate valid Work IDs, score each Work, select a threshold-qualified candidate, and record ambiguity and pagination uncertainty.
7. For each selected Work, request its `.json` Work Details document and paginated Editions using the same proxied client. Keep these responses in memory. Select the most suitable Edition based on available input publisher, year, language, and page-count criteria; retain Work-level data if no Edition is suitable.
8. After each book, atomically write the accumulated final metadata records to `final_books.json`. No responses, checkpoints, candidate reports, or separate execution reports are written.

All search, Work, and Edition responses live only in memory. A rerun starts over at the first book; the final JSON file preserves results already saved before an interruption, but the application does not use it as a checkpoint.

Proxy authentication failures, connection failures, and timeouts are reported without logging proxy credentials.
