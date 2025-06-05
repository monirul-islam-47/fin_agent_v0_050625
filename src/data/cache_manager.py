"""
High-level cache manager for market data
Provides convenient methods for caching quotes and bars
"""

from datetime import datetime
from typing import Dict, List, Optional

from .base import Quote, Bar
from .cache import get_cache_store
from ..utils import get_logger

logger = get_logger(__name__)

class CacheManager:
    """
    High-level interface for caching market data
    Handles serialization/deserialization of Quote and Bar objects
    """
    
    def __init__(self):
        self.store = get_cache_store()
        
        # Cache TTLs (in seconds)
        self.quote_ttl = 60  # 1 minute for quotes
        self.bar_ttl = 3600  # 1 hour for historical bars
        self.news_ttl = 300  # 5 minutes for news
        
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Get cached quote for symbol"""
        params = {'type': 'quote', 'symbol': symbol}
        data = await self.store.get('market', params)
        
        if data:
            try:
                # Reconstruct Quote object
                return Quote(
                    symbol=data['symbol'],
                    timestamp=datetime.fromisoformat(data['timestamp']),
                    price=data['price'],
                    bid=data.get('bid'),
                    ask=data.get('ask'),
                    volume=data.get('volume'),
                    provider=data.get('provider'),
                    is_delayed=data.get('is_delayed', False)
                )
            except Exception as e:
                logger.error(f"Failed to deserialize quote: {e}")
                return None
                
        return None
        
    async def put_quote(self, quote: Quote):
        """Cache a quote"""
        params = {'type': 'quote', 'symbol': quote.symbol}
        
        # Serialize Quote to dict
        data = {
            'symbol': quote.symbol,
            'timestamp': quote.timestamp.isoformat(),
            'price': quote.price,
            'bid': quote.bid,
            'ask': quote.ask,
            'volume': quote.volume,
            'provider': quote.provider,
            'is_delayed': quote.is_delayed
        }
        
        await self.store.set('market', params, data, self.quote_ttl)
        
    async def get_quotes(self, symbols: List[str]) -> Dict[str, Optional[Quote]]:
        """Get cached quotes for multiple symbols"""
        results = {}
        
        for symbol in symbols:
            quote = await self.get_quote(symbol)
            results[symbol] = quote
            
        return results
        
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1min"
    ) -> List[Bar]:
        """Get cached bars for symbol"""
        params = {
            'type': 'bars',
            'symbol': symbol,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'interval': interval
        }
        
        data = await self.store.get('market', params)
        
        if data:
            try:
                bars = []
                for bar_data in data:
                    bars.append(Bar(
                        symbol=bar_data['symbol'],
                        timestamp=datetime.fromisoformat(bar_data['timestamp']),
                        open=bar_data['open'],
                        high=bar_data['high'],
                        low=bar_data['low'],
                        close=bar_data['close'],
                        volume=bar_data['volume'],
                        provider=bar_data.get('provider')
                    ))
                return bars
            except Exception as e:
                logger.error(f"Failed to deserialize bars: {e}")
                return []
                
        return []
        
    async def put_bars(self, symbol: str, bars: List[Bar], interval: str = "1min"):
        """Cache bars for symbol"""
        if not bars:
            return
            
        # Use first and last bar timestamps for cache key
        start = bars[0].timestamp
        end = bars[-1].timestamp
        
        params = {
            'type': 'bars',
            'symbol': symbol,
            'start': start.isoformat(),
            'end': end.isoformat(),
            'interval': interval
        }
        
        # Serialize bars to list of dicts
        data = []
        for bar in bars:
            data.append({
                'symbol': bar.symbol,
                'timestamp': bar.timestamp.isoformat(),
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
                'provider': bar.provider
            })
            
        await self.store.set('market', params, data, self.bar_ttl)
        
    async def clear_quotes(self):
        """Clear all quote cache entries"""
        # This would need to be more sophisticated in production
        # For now, just clear today's market cache
        await self.store.clear('market', datetime.now())
        
    async def get_cache_stats(self) -> Dict[str, any]:
        """Get cache statistics"""
        return await self.store.get_stats()