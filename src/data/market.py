"""
Market data manager with intelligent fallback
Coordinates between Finnhub and Yahoo Finance
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Set
from enum import Enum

from ..config import get_config
from ..utils import get_logger, get_quota_guard
from .base import Quote, Bar, MarketDataAdapter
from .finnhub import FinnhubWebSocket
from .yahoo import YahooFinanceAdapter
from .cache_manager import CacheManager

logger = get_logger(__name__)

class DataPriority(Enum):
    """Priority order for data providers"""
    REALTIME = 1  # Finnhub WebSocket
    DELAYED = 2  # Yahoo Finance

class MarketDataManager:
    """
    Manages market data acquisition with intelligent fallback
    Handles quota management and caching across providers
    """
    
    def __init__(self):
        self.config = get_config()
        self.quota_guard = get_quota_guard()
        self.cache = CacheManager()
        
        # Initialize adapters
        self.finnhub = FinnhubWebSocket()
        self.yahoo = YahooFinanceAdapter()
        
        # Track active providers and their status
        self.active_providers = {
            DataPriority.REALTIME: self.finnhub,
            DataPriority.DELAYED: self.yahoo
        }
        
        # Track current data priority level
        self.current_priority = DataPriority.REALTIME
        
        # WebSocket quote storage (latest quotes from stream)
        self.latest_quotes: Dict[str, Quote] = {}
        
        # Subscribe to Finnhub quotes
        self.finnhub.add_quote_callback(self._on_quote_update)
        
    async def initialize(self):
        """Initialize all data adapters"""
        logger.info("Initializing market data manager...")
        
        # Connect adapters in order of priority
        for priority, adapter in self.active_providers.items():
            try:
                await adapter.connect()
                logger.info(f"Connected {adapter.provider.value} adapter")
            except Exception as e:
                logger.error(f"Failed to connect {adapter.provider.value}: {e}")
                
        # Check initial quota status
        await self._check_quota_status()
        
    async def shutdown(self):
        """Shutdown all data adapters"""
        logger.info("Shutting down market data manager...")
        
        for adapter in self.active_providers.values():
            try:
                await adapter.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting {adapter.provider.value}: {e}")
                
    def _on_quote_update(self, quote: Quote):
        """Handle incoming quote from WebSocket"""
        self.latest_quotes[quote.symbol] = quote
        
    async def _check_quota_status(self):
        """Check quota status and adjust priority if needed"""
        status = self.quota_guard.get_status()
        
        # Check Finnhub quota
        finnhub_status = status.get('finnhub', {})
        if finnhub_status.get('percentage', 0) > 95:
            logger.warning("Finnhub quota nearly exhausted, switching to Yahoo Finance")
            self.current_priority = DataPriority.DELAYED
            
    async def get_quote(self, symbol: str, force_fresh: bool = False) -> Optional[Quote]:
        """
        Get quote with automatic fallback
        Uses WebSocket data if available, otherwise falls back
        """
        # Check cache first unless forced fresh
        if not force_fresh:
            cached = await self.cache.get_quote(symbol)
            if cached:
                return cached
                
        # Check WebSocket quotes first
        if symbol in self.latest_quotes:
            quote = self.latest_quotes[symbol]
            # Cache the quote
            await self.cache.put_quote(quote)
            return quote
            
        # Fall back through providers
        quote = None
        priorities = [self.current_priority, DataPriority.DELAYED]
        for priority in priorities:
            adapter = self.active_providers[priority]
            
            if not await adapter.health_check():
                logger.warning(f"{adapter.provider.value} health check failed, skipping")
                continue
                
            try:
                quote = await adapter.get_quote(symbol)
                if quote:
                    # Cache successful quote
                    await self.cache.put_quote(quote)
                    
                    # Log if we're using delayed data
                    if quote.is_delayed:
                        logger.warning(f"Using delayed quote for {symbol} from {quote.provider}")
                        
                    break
                    
            except Exception as e:
                logger.error(f"Error getting quote from {adapter.provider.value}: {e}")
                continue
                
        # Update quota status after request
        await self._check_quota_status()
        
        return quote
        
    async def get_quotes(self, symbols: List[str], force_fresh: bool = False) -> Dict[str, Optional[Quote]]:
        """Get quotes for multiple symbols with fallback"""
        results = {}
        
        # Separate symbols that we have from WebSocket
        ws_symbols = []
        fetch_symbols = []
        
        for symbol in symbols:
            if symbol in self.latest_quotes and not force_fresh:
                results[symbol] = self.latest_quotes[symbol]
                ws_symbols.append(symbol)
            else:
                fetch_symbols.append(symbol)
                
        logger.debug(f"Got {len(ws_symbols)} quotes from WebSocket, fetching {len(fetch_symbols)}")
        
        # Check cache for remaining symbols
        if not force_fresh and fetch_symbols:
            cached_quotes = await self.cache.get_quotes(fetch_symbols)
            still_need = []
            
            for symbol in fetch_symbols:
                if symbol in cached_quotes and cached_quotes[symbol]:
                    results[symbol] = cached_quotes[symbol]
                else:
                    still_need.append(symbol)
                    
            fetch_symbols = still_need
            
        # Fetch remaining symbols through fallback chain
        if fetch_symbols:
            priorities = [self.current_priority, DataPriority.DELAYED]
            for priority in priorities:
                if not fetch_symbols:  # All symbols fetched
                    break
                    
                adapter = self.active_providers[priority]
                
                if not await adapter.health_check():
                    continue
                    
                try:
                    quotes = await adapter.get_quotes(fetch_symbols)
                    
                    # Process results
                    still_need = []
                    for symbol in fetch_symbols:
                        quote = quotes.get(symbol)
                        if quote:
                            results[symbol] = quote
                            # Cache successful quote
                            await self.cache.put_quote(quote)
                            
                            if quote.is_delayed:
                                logger.warning(f"Using delayed quote for {symbol}")
                        else:
                            still_need.append(symbol)
                            
                    fetch_symbols = still_need
                    
                except Exception as e:
                    logger.error(f"Error getting batch quotes from {adapter.provider.value}: {e}")
                    continue
                    
        # Update quota status
        await self._check_quota_status()
        
        return results
        
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1min"
    ) -> List[Bar]:
        """Get historical bars with fallback"""
        # Check cache first
        cached_bars = await self.cache.get_bars(symbol, start, end, interval)
        if cached_bars:
            return cached_bars
            
        # Try each provider in order (use Yahoo for historical)
        bars = []
        for priority in [DataPriority.DELAYED]:  # Only Yahoo provides historical now
            # Skip WebSocket as it doesn't provide historical data
            if priority == DataPriority.REALTIME:
                continue
                
            adapter = self.active_providers[priority]
            
            if not await adapter.health_check():
                continue
                
            try:
                bars = await adapter.get_bars(symbol, start, end, interval)
                if bars:
                    # Cache successful fetch
                    await self.cache.put_bars(symbol, bars, interval)
                    break
                    
            except Exception as e:
                logger.error(f"Error getting bars from {adapter.provider.value}: {e}")
                continue
                
        # Update quota status
        await self._check_quota_status()
        
        return bars
        
    async def subscribe_quotes(self, symbols: List[str]):
        """Subscribe to real-time quotes via WebSocket"""
        if self.current_priority == DataPriority.REALTIME:
            try:
                await self.finnhub.subscribe(symbols)
                logger.info(f"Subscribed to {len(symbols)} symbols on Finnhub WebSocket")
            except Exception as e:
                logger.error(f"Failed to subscribe to quotes: {e}")
        else:
            logger.warning("WebSocket not available due to quota limits")
            
    async def unsubscribe_quotes(self, symbols: List[str]):
        """Unsubscribe from real-time quotes"""
        try:
            await self.finnhub.unsubscribe(symbols)
        except Exception as e:
            logger.error(f"Failed to unsubscribe from quotes: {e}")
            
    async def start_quote_stream(self):
        """Start WebSocket quote streaming"""
        if self.current_priority == DataPriority.REALTIME:
            asyncio.create_task(self.finnhub.listen())
            logger.info("Started Finnhub WebSocket stream")
        else:
            logger.warning("Cannot start WebSocket stream due to quota limits")
            
    def get_latest_quotes(self) -> Dict[str, Quote]:
        """Get all latest quotes from WebSocket"""
        return self.latest_quotes.copy()
        
    def get_current_priority(self) -> str:
        """Get current data priority level"""
        return self.current_priority.name