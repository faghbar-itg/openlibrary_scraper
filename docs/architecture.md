# Architecture

The codebase has six small layers:

- `config` validates environment settings and refuses proxy-disabled execution.
- `domain` contains source-neutral records, Search API Work data, safe query construction, text normalization, and explainable scoring.
- `processing` reads the active Excel worksheet and preserves original cell values while recording validation errors and duplicates.
- `infrastructure` handles Open Library Search, Work Details, and Editions requests through the configured Node4 proxy, raw response files, and atomic checkpoints.
- `application` coordinates query fallback, page retrieval, candidate matching, and resume state.
- `reporting` writes candidate and best-match tables plus the execution report.

`main.py` is the composition root. External clients are passed into the application services, which keeps HTTP and filesystem behavior replaceable in tests. No Editions, Work-detail, or Author-detail integrations are included.

The authenticated Node4 proxy URL is built from the required `NODE4_*` environment variables, with username and password URL-encoded. Both HTTP and HTTPS requests use that same proxy; no external proxy-provider request or direct-connection fallback is made. Proxy authentication, connection, and timeout failures are surfaced explicitly. After matching a Work, the metadata service retrieves Work Details and paginated Editions through the same HTTP client, choosing an Edition against the input publisher, year, language, and optional page-count criteria. Detail responses are cached under the run's raw response directory and checkpoint state. The exporter writes one ordered `final_books.json` record per input book.
