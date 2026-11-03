class ScraperError(Exception):
    """Base class for expected scraper failures."""


class ConfigurationError(ScraperError):
    """Invalid or incomplete application configuration."""


class InputValidationError(ScraperError):
    """Invalid input workbook or records."""


class SearchError(ScraperError):
    """Open Library search request or response failure."""


class OutputError(ScraperError):
    """Failure while persisting application output."""
