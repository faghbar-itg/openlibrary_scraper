# Architecture

Production code is grouped into three folders:

- `main.py` initializes logging, loads configuration, runs processing, and reports the run result.
- `config/settings.py` defines application errors and loads/validates environment variables, proxy credentials, API options, and matching thresholds.
- `requests_apis/requests_handler.py` builds queries, sends Search/Work/Edition requests through one proxied HTTP session, parses responses, retries transient failures, and paginates Search results.
- `processing/data_processor.py` reads and validates Excel input, normalizes and scores books, selects Editions, and runs the sequential workflow.
- `processing/storage.py` atomically writes the single final `final_books.json` output.

The call path starts in `main.py`, enters `processing.data_processor.run_pipeline()`, calls `requests_apis.requests_handler` for each API operation, and calls `processing.storage` after each book to persist the accumulated final records.

The authenticated Node4 proxy URL is built from the required `NODE4_*` environment variables, with username and password URL-encoded. HTTP and HTTPS requests use that same proxy; there is no direct-connection fallback. Proxy authentication, connection, and timeout failures are surfaced explicitly. Search pages, Work details, Edition pages, and match data are held in memory only. Only `data/output/final_books.json` is persisted, atomically after each book. A rerun starts from the first input record; there is no response cache or checkpoint.
