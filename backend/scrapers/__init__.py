"""Top-level scrapers package — re-exports for convenience.

Pattern:
    from scrapers import unified_company_lookup, BraveClient, GoogleMapsClient
"""

from .base import ScrapeResult
from .searxng import SearXNGClient
from .httpbs import fetch_and_extract
from .multi_search import (
    BraveClient,
    EcosiaClient,
    BingClient,
    unified_search,
)
from .google_maps import GoogleMapsClient, GoogleMapsPlace
from .google_aio import GoogleAIOClient, GoogleAIOSnippet
from .unified import unified_company_lookup, CompanyLookup

__all__ = [
    "ScrapeResult",
    "SearXNGClient",
    "fetch_and_extract",
    "BraveClient",
    "EcosiaClient",
    "BingClient",
    "GoogleMapsClient",
    "GoogleMapsPlace",
    "GoogleAIOClient",
    "GoogleAIOSnippet",
    "unified_search",
    "unified_company_lookup",
    "CompanyLookup",
]
