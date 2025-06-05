"""
News manager with sentiment analysis and deduplication
Coordinates between NewsAPI and GDELT with intelligent fallback
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import hashlib

from ..config import get_config
from ..utils import get_logger, get_quota_guard
from .base import Headline
from .news import NewsAPIAdapter, GDELTAdapter
from .cache_manager import CacheManager

logger = get_logger(__name__)

class NewsManager:
    """
    Manages news data acquisition with fallback and deduplication
    Prioritizes NewsAPI for quality, falls back to GDELT
    """
    
    def __init__(self):
        self.config = get_config()
        self.quota_guard = get_quota_guard()
        self.cache = CacheManager()
        
        # Initialize adapters
        self.newsapi = NewsAPIAdapter()
        self.gdelt = GDELTAdapter()
        
        # Deduplication settings
        self.similarity_threshold = 0.8  # For fuzzy matching
        self.dedup_window_hours = 24
        
    async def initialize(self):
        """Initialize news adapters"""
        logger.info("Initializing news manager...")
        
        # Connect adapters
        try:
            await self.newsapi.connect()
            logger.info("Connected NewsAPI adapter")
        except Exception as e:
            logger.error(f"Failed to connect NewsAPI: {e}")
            
        try:
            await self.gdelt.connect()
            logger.info("Connected GDELT adapter")
        except Exception as e:
            logger.error(f"Failed to connect GDELT: {e}")
            
    async def shutdown(self):
        """Shutdown news adapters"""
        logger.info("Shutting down news manager...")
        
        try:
            await self.newsapi.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting NewsAPI: {e}")
            
        try:
            await self.gdelt.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting GDELT: {e}")
            
    def _headline_hash(self, headline: str) -> str:
        """Generate hash for headline deduplication"""
        # Normalize headline: lowercase, remove extra spaces
        normalized = " ".join(headline.lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()[:16]
        
    def _deduplicate_headlines(self, headlines: List[Headline]) -> List[Headline]:
        """Remove duplicate headlines based on similarity"""
        if not headlines:
            return []
            
        # Sort by timestamp (newest first)
        sorted_headlines = sorted(headlines, key=lambda h: h.timestamp, reverse=True)
        
        unique_headlines = []
        seen_hashes = set()
        
        for headline in sorted_headlines:
            # Generate hash
            h_hash = self._headline_hash(headline.headline)
            
            # Check if we've seen similar headline
            if h_hash not in seen_hashes:
                seen_hashes.add(h_hash)
                unique_headlines.append(headline)
                
        return unique_headlines
        
    async def get_headlines(
        self, 
        symbol: str, 
        limit: int = 10,
        use_cache: bool = True
    ) -> List[Headline]:
        """
        Get headlines for symbol with fallback and deduplication
        Tries NewsAPI first, then GDELT, combines and deduplicates
        """
        # Check cache first
        if use_cache:
            cache_key = f"headlines_{symbol}_{limit}"
            cached = await self.cache.store.get(
                'news',
                {'type': 'headlines', 'symbol': symbol, 'limit': limit}
            )
            if cached:
                # Reconstruct Headline objects
                headlines = []
                for h_data in cached:
                    headlines.append(Headline(
                        symbol=h_data['symbol'],
                        timestamp=datetime.fromisoformat(h_data['timestamp']),
                        headline=h_data['headline'],
                        source=h_data['source'],
                        url=h_data.get('url'),
                        sentiment=h_data.get('sentiment'),
                        provider=h_data.get('provider')
                    ))
                return headlines
                
        all_headlines = []
        
        # Try NewsAPI first (better quality but limited)
        if await self.newsapi.health_check():
            try:
                newsapi_headlines = await self.newsapi.get_headlines(symbol, limit)
                all_headlines.extend(newsapi_headlines)
                logger.info(f"Got {len(newsapi_headlines)} headlines from NewsAPI for {symbol}")
            except Exception as e:
                logger.error(f"NewsAPI error for {symbol}: {e}")
                
        # If we don't have enough, try GDELT
        if len(all_headlines) < limit and await self.gdelt.health_check():
            try:
                gdelt_headlines = await self.gdelt.get_headlines(
                    symbol, 
                    limit - len(all_headlines)
                )
                all_headlines.extend(gdelt_headlines)
                logger.info(f"Got {len(gdelt_headlines)} headlines from GDELT for {symbol}")
            except Exception as e:
                logger.error(f"GDELT error for {symbol}: {e}")
                
        # Deduplicate
        unique_headlines = self._deduplicate_headlines(all_headlines)
        
        # Sort by sentiment (most extreme first) then by time
        unique_headlines.sort(
            key=lambda h: (abs(h.sentiment or 0), h.timestamp),
            reverse=True
        )
        
        # Limit results
        final_headlines = unique_headlines[:limit]
        
        # Cache results
        if final_headlines and use_cache:
            cache_data = []
            for h in final_headlines:
                cache_data.append({
                    'symbol': h.symbol,
                    'timestamp': h.timestamp.isoformat(),
                    'headline': h.headline,
                    'source': h.source,
                    'url': h.url,
                    'sentiment': h.sentiment,
                    'provider': h.provider
                })
            
            await self.cache.store.set(
                'news',
                {'type': 'headlines', 'symbol': symbol, 'limit': limit},
                cache_data,
                ttl_seconds=300  # 5 minute cache for news
            )
            
        return final_headlines
        
    async def get_market_sentiment(self, symbols: List[str]) -> Dict[str, float]:
        """
        Get aggregated sentiment scores for multiple symbols
        Returns sentiment score from -1 (very negative) to 1 (very positive)
        """
        sentiment_scores = {}
        
        # Process in batches to avoid overwhelming APIs
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            
            # Get headlines for each symbol concurrently
            tasks = [self.get_headlines(symbol, limit=5) for symbol in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for symbol, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.error(f"Error getting sentiment for {symbol}: {result}")
                    sentiment_scores[symbol] = 0.0  # Neutral on error
                    continue
                    
                headlines = result
                if not headlines:
                    sentiment_scores[symbol] = 0.0  # Neutral if no news
                    continue
                    
                # Calculate average sentiment
                sentiments = [h.sentiment for h in headlines if h.sentiment is not None]
                if sentiments:
                    avg_sentiment = sum(sentiments) / len(sentiments)
                    sentiment_scores[symbol] = round(avg_sentiment, 3)
                else:
                    sentiment_scores[symbol] = 0.0
                    
        return sentiment_scores
        
    async def search_breaking_news(
        self,
        keywords: List[str],
        hours_back: int = 24
    ) -> List[Headline]:
        """
        Search for breaking news across all sources
        Useful for finding market-moving events
        """
        end = datetime.now()
        start = end - timedelta(hours=hours_back)
        
        # Build search query
        query = " OR ".join(f'"{kw}"' for kw in keywords)
        
        all_headlines = []
        
        # Search both sources
        if await self.newsapi.health_check():
            try:
                newsapi_results = await self.newsapi.search_news(query, start, end, 20)
                all_headlines.extend(newsapi_results)
            except Exception as e:
                logger.error(f"NewsAPI search error: {e}")
                
        if await self.gdelt.health_check():
            try:
                gdelt_results = await self.gdelt.search_news(query, start, end, 20)
                all_headlines.extend(gdelt_results)
            except Exception as e:
                logger.error(f"GDELT search error: {e}")
                
        # Deduplicate and sort by time
        unique_headlines = self._deduplicate_headlines(all_headlines)
        
        return unique_headlines