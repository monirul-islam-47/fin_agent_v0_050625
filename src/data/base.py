"""
Base classes for data adapters
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum

class DataProvider(Enum):
    """Available data providers"""
    FINNHUB = "finnhub"
    IEX = "iex"
    YAHOO = "yahoo"
    ALPHA_VANTAGE = "alpha_vantage"
    GDELT = "gdelt"
    NEWSAPI = "newsapi"

@dataclass
class Quote:
    """Market quote data"""
    symbol: str
    timestamp: datetime
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[int] = None
    provider: Optional[str] = None
    is_delayed: bool = False

@dataclass
class Bar:
    """OHLCV bar data"""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    provider: Optional[str] = None

@dataclass
class Headline:
    """News headline data"""
    symbol: str
    timestamp: datetime
    headline: str
    source: str
    url: Optional[str] = None
    sentiment: Optional[float] = None
    provider: Optional[str] = None

class BaseAdapter(ABC):
    """Base class for all data adapters"""
    
    def __init__(self, provider: DataProvider):
        self.provider = provider
        self.is_connected = False
    
    @abstractmethod
    async def connect(self):
        """Establish connection to data source"""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close connection to data source"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the adapter is healthy"""
        pass

class MarketDataAdapter(BaseAdapter):
    """Base class for market data adapters"""
    
    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Get current quote for symbol"""
        pass
    
    @abstractmethod
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Optional[Quote]]:
        """Get current quotes for multiple symbols"""
        pass
    
    @abstractmethod
    async def get_bars(
        self, 
        symbol: str, 
        start: datetime, 
        end: datetime,
        interval: str = "1min"
    ) -> List[Bar]:
        """Get historical bars for symbol"""
        pass

class NewsAdapter(BaseAdapter):
    """Base class for news data adapters"""
    
    @abstractmethod
    async def get_headlines(
        self, 
        symbol: str,
        limit: int = 10
    ) -> List[Headline]:
        """Get recent headlines for symbol"""
        pass
    
    @abstractmethod
    async def search_news(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 20
    ) -> List[Headline]:
        """Search news by query and date range"""
        pass