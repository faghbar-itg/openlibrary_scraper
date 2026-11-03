# Data flow

1. Load `.env`, require all four Node4 proxy settings, and build the authenticated proxy URL.
2. Read the active worksheet. Required headers are checked; original values, row numbers, duplicates, empty rows, and invalid values are handled explicitly.
3. Initialize the current run checkpoint. Each book gets a title-and-author query plus a title-only fallback.
4. Configure the HTTP and HTTPS clients to reuse the Node4 proxy. No proxy-provider requests or direct connections are used.
5. Request Search API pages sequentially. Save the unmodified JSON page before advancing the checkpoint. Continue until exact totals are met, the service ends the result set, or a safety/error condition is reached.
6. Deduplicate valid Work IDs, score each Work, select a threshold-qualified candidate, and record ambiguity and pagination uncertainty.
7. For each selected Work, request its `.json` Work Details document and paginated Editions using the same proxied client. Cache each successful response and checkpoint progress. Select the most suitable Edition based on available input publisher, year, language, and page-count criteria; retain Work-level data if no Edition is suitable.
8. Export one final metadata object per input book, in input order, to `final_books.json`; also retain the candidate, best-match, and execution-report outputs.

The checkpoint stores run ID, per-book query and saved-page state, retrieval status, and the selected Work ID. Raw pages are scoped by a hash of the query to prevent fallback-query collisions. An incomplete search is never reported as complete merely because one page succeeded.

Proxy authentication failures, connection failures, and timeouts are reported without logging proxy credentials.
