"""
Data acquisition layer
Handles market data, news, and caching with intelligent fallback
"""

from .base import (
    DataProvider,
    Quote,
    Bar,
    Headline,
    BaseAdapter,
    MarketDataAdapter,
    NewsAdapter
)

from .market import MarketDataManager, DataPriority
from .cache import CacheStore, get_cache_store
from .cache_manager import CacheManager
from .news_manager import NewsManager

__all__ = [
    # Base classes
    'DataProvider',
    'Quote', 
    'Bar',
    'Headline',
    'BaseAdapter',
    'MarketDataAdapter',
    'NewsAdapter',
    
    # Main interfaces
    'MarketDataManager',
    'NewsManager',
    'DataPriority',
    'CacheStore',
    'CacheManager',
    'get_cache_store'
]