"""
extractors/__init__.py
───────────────────────
Public surface of the extractors package.

Import from here everywhere outside the extractors folder:
    from extractors import USGSExtractor, NewsExtractor, RSSExtractor, ...
"""

from .ais              import AISExtractor
from .gdelt            import GDELTExtractor
from .hackernews       import HackerNewsExtractor
from .news             import NewsExtractor
from .opensky          import OpenSkyExtractor
from .rss              import RSSExtractor
from .socials          import RedditExtractor, SocialExtractor, YouTubeExtractor
from .stocks           import StockExtractor
from .telegram_monitor import TelegramExtractor
from .twitter          import TwitterExtractor
from .usgs             import USGSExtractor

__all__ = [
    "HackerNewsExtractor",
    "NewsExtractor",
    "AISExtractor",
    "GDELTExtractor",
    "OpenSkyExtractor",
    "RSSExtractor",
    "RedditExtractor",
    "SocialExtractor",
    "StockExtractor",
    "TelegramExtractor",
    "TwitterExtractor",
    "USGSExtractor",
    "YouTubeExtractor",
]
