# Architecture

The package has three areas:

- `config/settings.py` loads and validates environment configuration, including proxy credentials, API base URL, timeouts, retries, pagination, matching thresholds, and paths.
- `requests/` builds search queries, sends Search/Work/Edition requests through one proxied HTTP session, retries transient failures, parses responses, and paginates Search results.
- `processing/` reads and validates Excel input, normalizes and scores books, selects Editions, orchestrates the run, resumes from checkpoints, caches raw responses, and exports results.

`main.py` handles command-line options, loads settings, configures logging, and calls `processing.pipeline.run_pipeline`. The pipeline creates the request client and processing services, then runs the book workflow. Shared expected errors are defined in `openlibrary_scraper/errors.py`.

The authenticated Node4 proxy URL is built from the required `NODE4_*` environment variables, with username and password URL-encoded. HTTP and HTTPS requests use that same proxy; there is no direct-connection fallback. Proxy authentication, connection, and timeout failures are surfaced explicitly. Search pages, Work details, and Edition pages are cached alongside checkpoint state so interrupted runs can resume without repeating completed requests.
